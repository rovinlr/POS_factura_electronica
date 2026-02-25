from odoo import api, fields, models

from ..services.einvoice_service import EInvoiceService


class L10nCrEInvoiceService(models.AbstractModel):
    _name = "l10n_cr.einvoice.service"
    _description = "Servicio FE Costa Rica"

    def _get_service(self):
        return EInvoiceService(self.env)

    def _get_pos_document_type(self, order, document_type=None):
        if document_type:
            return document_type
        if order.cr_fe_document_type in ("te", "nc"):
            return order.cr_fe_document_type
        return "nc" if order.amount_total < 0 else "te"

    def _prepare_pos_payload(
        self,
        order,
        payload=None,
        idempotency_key=None,
        consecutivo=None,
        clave=None,
        document_type=None,
    ):
        service = self._get_service()
        doc_type = self._get_pos_document_type(order, document_type=document_type)
        normalized_payload = dict(payload or service.build_payload_from_pos_order(order))
        normalized_payload.setdefault("document_type", doc_type)
        normalized_payload.setdefault("idempotency_key", idempotency_key or order.cr_fe_idempotency_key or order._cr_build_idempotency_key())
        normalized_payload.setdefault(
            "consecutivo",
            consecutivo
            or order.cr_fe_consecutivo
            or (order._cr_generate_fe_consecutivo(document_type=doc_type) if hasattr(order, "_cr_generate_fe_consecutivo") else order._cr_get_next_consecutivo_by_document_type(doc_type)),
        )
        normalized_payload.setdefault(
            "clave",
            clave
            or order.cr_fe_clave
            or (order._cr_generate_fe_clave(normalized_payload["consecutivo"]) if hasattr(order, "_cr_generate_fe_clave") else f"{doc_type.upper()}-{order.company_id.id}-{order.id}-{normalized_payload['consecutivo']}"),
        )
        return service, doc_type, normalized_payload

    @api.model
    def build_pos_xml_from_order(
        self,
        order_id,
        payload=None,
        idempotency_key=None,
        consecutivo=None,
        clave=None,
        document_type=None,
    ):
        order = self.env["pos.order"].browse(order_id).exists()
        if not order:
            return {"ok": False, "status": "error", "reason": "order_not_found"}

        service, doc_type, normalized_payload = self._prepare_pos_payload(
            order,
            payload=payload,
            idempotency_key=idempotency_key,
            consecutivo=consecutivo,
            clave=clave,
            document_type=document_type,
        )
        xml = service.generate_xml(normalized_payload, doc_type)
        signed_xml = service.sign_xml(xml)
        attachment = service.attach_xml(order, signed_xml, kind="document")
        order.write(
            {
                "cr_fe_document_type": doc_type,
                "cr_fe_status": "pending",
                "cr_fe_idempotency_key": normalized_payload["idempotency_key"],
                "cr_fe_consecutivo": normalized_payload["consecutivo"],
                "cr_fe_clave": normalized_payload["clave"],
                "cr_fe_xml_attachment_id": attachment.id,
            }
        )
        return {
            "ok": True,
            "status": "pending",
            "xml_attachment_id": attachment.id,
            "idempotency_key": normalized_payload["idempotency_key"],
            "consecutivo": normalized_payload["consecutivo"],
            "clave": normalized_payload["clave"],
            "document_type": doc_type,
        }

    @api.model
    def build_te_xml_from_pos(self, order_id, payload=None, idempotency_key=None, consecutivo=None, clave=None):
        return self.build_pos_xml_from_order(
            order_id,
            payload=payload,
            idempotency_key=idempotency_key,
            consecutivo=consecutivo,
            clave=clave,
            document_type="te",
        )

    @api.model
    def build_nc_xml_from_pos(self, order_id, payload=None, idempotency_key=None, consecutivo=None, clave=None):
        return self.build_pos_xml_from_order(
            order_id,
            payload=payload,
            idempotency_key=idempotency_key,
            consecutivo=consecutivo,
            clave=clave,
            document_type="nc",
        )

    @api.model
    def send_to_hacienda(self, order_id, payload=None, idempotency_key=None, document_type=None):
        order = self.env["pos.order"].browse(order_id).exists()
        if not order:
            return {"ok": False, "status": "error", "reason": "order_not_found"}

        service, doc_type, normalized_payload = self._prepare_pos_payload(
            order,
            payload=payload,
            idempotency_key=idempotency_key,
            document_type=document_type,
        )
        if order.cr_fe_clave:
            normalized_payload["clave"] = order.cr_fe_clave

        xml_blob = b""
        if order.cr_fe_xml_attachment_id and order.cr_fe_xml_attachment_id.datas:
            xml_blob = order.cr_fe_xml_attachment_id.raw or b""
        if not xml_blob:
            xml_blob = service.sign_xml(service.generate_xml(normalized_payload, doc_type))

        response = service.send_to_hacienda(normalized_payload, xml_blob)
        parsed = service.parse_hacienda_response(response)
        status = parsed.get("status") or "sent"
        response_xml = service.build_hacienda_response_xml(response, parsed)
        response_attachment = service.attach_xml(order, response_xml, kind="response") if response_xml else False

        order.write(
            {
                "cr_fe_document_type": doc_type,
                "cr_fe_status": status,
                "cr_fe_last_send_date": fields.Datetime.now(),
                "cr_fe_response_attachment_id": response_attachment.id if response_attachment else False,
                "cr_fe_last_error": False,
            }
        )

        return {
            "ok": True,
            "status": status,
            "track_id": parsed.get("track_id"),
            "response_attachment_id": response_attachment.id if response_attachment else False,
            "document_type": doc_type,
        }

    @api.model
    def consult_status(self, order_id, idempotency_key=None):
        order = self.env["pos.order"].browse(order_id).exists()
        if not order:
            return {"status": "error", "reason": "order_not_found"}
        if idempotency_key and order.cr_fe_idempotency_key and order.cr_fe_idempotency_key != idempotency_key:
            return {"status": "error", "reason": "idempotency_key_mismatch"}

        service = self._get_service()
        parsed_status = False
        if order.cr_fe_response_attachment_id:
            response_xml = order.cr_fe_response_attachment_id.raw or b""
            parsed_status = service.parse_hacienda_status_xml(response_xml)

        status = parsed_status or order.cr_fe_status or "sent"
        if status == "sent":
            status = "processing"
        return {
            "status": status,
            "response_attachment_id": order.cr_fe_response_attachment_id.id if order.cr_fe_response_attachment_id else False,
            "document_type": order.cr_fe_document_type,
        }


    @api.model
    def enqueue_from_pos_order(self, order_id, payload=None, company_id=None, idempotency_key=None):
        order = self.env["pos.order"].browse(order_id).exists()
        if not order:
            return {"ok": False, "status": "error", "reason": "order_not_found"}
        if company_id and order.company_id.id != company_id:
            return {"ok": False, "status": "error", "reason": "company_mismatch"}

        return self.send_to_hacienda(order_id, payload=payload, idempotency_key=idempotency_key)

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
        return self.consult_status(order_id, idempotency_key=idempotency_key)
