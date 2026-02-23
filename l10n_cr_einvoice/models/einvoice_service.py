from odoo import api, models

from ..services.einvoice_service import EInvoiceService


class L10nCrEInvoiceService(models.AbstractModel):
    _name = "l10n_cr.einvoice.service"
    _description = "Servicio FE Costa Rica"

    def _get_service(self):
        return EInvoiceService(self.env)

    def _prepare_pos_payload(self, order, payload=None, idempotency_key=None):
        service = self._get_service()
        normalized_payload = dict(payload or service.build_payload_from_pos_order(order))
        normalized_payload.setdefault("idempotency_key", idempotency_key or order.cr_fe_idempotency_key or order._cr_build_idempotency_key())
        normalized_payload.setdefault("consecutivo", order.cr_fe_consecutivo or order._cr_get_next_consecutivo_by_document_type("te"))
        return service, normalized_payload

    @api.model
    def enqueue_from_pos_order(self, order_id, payload=None, company_id=None, idempotency_key=None):
        order = self.env["pos.order"].browse(order_id).exists()
        if not order:
            return {"ok": False, "status": "error", "reason": "order_not_found"}
        if company_id and order.company_id.id != company_id:
            return {"ok": False, "status": "error", "reason": "company_mismatch"}

        service, normalized_payload = self._prepare_pos_payload(order, payload=payload, idempotency_key=idempotency_key)
        return service.process_full_flow(order, normalized_payload, doc_type="te")

    @api.model
    def send_from_pos_order(self, order_id, payload=None, company_id=None, idempotency_key=None):
        return self.enqueue_from_pos_order(
            order_id,
            payload=payload,
            company_id=company_id,
            idempotency_key=idempotency_key,
        )

    @api.model
    def process_pos_order(self, order_id, payload=None, company_id=None, idempotency_key=None):
        return self.enqueue_from_pos_order(
            order_id,
            payload=payload,
            company_id=company_id,
            idempotency_key=idempotency_key,
        )

    @api.model
    def check_status_from_pos_order(self, order_id, idempotency_key=None):
        order = self.env["pos.order"].browse(order_id).exists()
        if not order:
            return {"status": "error", "reason": "order_not_found"}
        if idempotency_key and order.cr_fe_idempotency_key and order.cr_fe_idempotency_key != idempotency_key:
            return {"status": "error", "reason": "idempotency_key_mismatch"}
        return {"status": order.cr_fe_status or "to_send", "idempotency_key": order.cr_fe_idempotency_key}
