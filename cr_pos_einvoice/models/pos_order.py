import logging
from datetime import timedelta
from importlib import import_module

from odoo import _, api, fields, models
from odoo.exceptions import UserError



class PosOrder(models.Model):
    _inherit = "pos.order"

    _logger = logging.getLogger(__name__)

    _CR_INVOICE_MOVE_TYPES = ("out_invoice", "out_refund")

    cr_ticket_move_id = fields.Many2one("account.move", string="Movimiento FE Tiquete", copy=False, index=True)
    cr_fe_document_type = fields.Selection(
        [("te", "Tiquete Electrónico"), ("fe", "Factura Electrónica"), ("nc", "Nota de Crédito")],
        string="Tipo documento FE",
        compute="_compute_cr_fe_document_type",
        store=True,
    )
    cr_fe_status = fields.Selection(
        [
            ("draft", "Borrador"),
            ("pending", "Pendiente de envío"),
            ("error_retry", "Error con reintento"),
            ("sent", "Enviado"),
            ("processing", "Procesando"),
            ("accepted", "Aceptado"),
            ("rejected", "Rechazado"),
            ("error", "Con error"),
            ("not_applicable", "No aplica"),
        ],
        string="Estado FE",
        default="draft",
        copy=False,
    )
    cr_fe_clave = fields.Char(string="Clave FE", copy=False)
    cr_fe_consecutivo = fields.Char(string="Consecutivo FE", copy=False)
    cr_fe_idempotency_key = fields.Char(string="Clave de idempotencia FE", copy=False, index=True)
    cr_fe_xml_attachment_id = fields.Many2one("ir.attachment", string="XML documento", copy=False)
    cr_fe_response_attachment_id = fields.Many2one("ir.attachment", string="XML respuesta MH", copy=False)
    cr_fe_attachment_ids = fields.Many2many("ir.attachment", string="Adjuntos FE", compute="_compute_cr_fe_attachment_ids")
    cr_fe_retry_count = fields.Integer(string="Reintentos FE", default=0, copy=False)
    cr_fe_next_try = fields.Datetime(string="Próximo intento FE", copy=False)
    cr_fe_last_error = fields.Text(string="Último error FE", copy=False)
    cr_fe_last_send_date = fields.Datetime(string="Último envío FE", copy=False)

    _cr_pos_einvoice_idempotency_key_unique = models.Constraint(
        "unique(company_id, cr_fe_idempotency_key)",
        "La clave de idempotencia FE debe ser única por compañía.",
    )

    @api.depends("account_move", "state", "amount_total")
    def _compute_cr_fe_document_type(self):
        for order in self:
            invoice = order._cr_get_real_invoice_move()
            if invoice:
                order.cr_fe_document_type = "nc" if invoice.move_type == "out_refund" else "fe"
            elif order.state in ("paid", "done", "invoiced"):
                order.cr_fe_document_type = "te"
            else:
                order.cr_fe_document_type = False

    def _cr_service(self):
        """Resolve FE service without breaking module import at registry load time."""
        service_paths = [
            "odoo.addons.l10n_cr_einvoice.services.einvoice_service",
            "l10n_cr_einvoice.services.einvoice_service",
            "odoo.addons.cr_pos_einvoice.services.einvoice_service_fallback",
            "cr_pos_einvoice.services.einvoice_service_fallback",
        ]
        for service_path in service_paths:
            try:
                module = import_module(service_path)
                return module.EInvoiceService(self.env)
            except (ImportError, AttributeError):
                continue
        raise UserError(_("No se pudo inicializar el servicio de Factura Electrónica."))

    def _cr_normalize_hacienda_status(self, status, default_status=False):
        self.ensure_one()
        normalized = (status or "").strip().lower()
        mapping = {
            "aceptado": "accepted",
            "aceptada": "accepted",
            "accepted": "accepted",
            "aprobado": "accepted",
            "approval": "accepted",
            "rechazado": "rejected",
            "rejected": "rejected",
            "denegado": "rejected",
            "error": "error",
            "failed": "error",
            "fallido": "error",
            "enviado": "sent",
            "sent": "sent",
            "procesando": "sent",
            "processing": "sent",
            "pendiente": "pending",
            "to_send": "pending",
            "pending": "pending",
            "error_retry": "error_retry",
            "recibido": "processing",
            "draft": "draft",
        }
        if normalized in mapping:
            return mapping[normalized]
        if default_status:
            return "sent"
        return self.cr_fe_status or "pending"

    def _compute_cr_fe_attachment_ids(self):
        for order in self:
            order.cr_fe_attachment_ids = self.env["ir.attachment"].search(
                [("res_model", "=", "pos.order"), ("res_id", "=", order.id)], order="id desc"
            )

    def _cr_get_real_invoice_move(self):
        self.ensure_one()
        move = self.account_move
        if move and move.move_type in self._CR_INVOICE_MOVE_TYPES and move.state != "cancel":
            return move
        return self.env["account.move"]

    def _cr_has_real_invoice_move(self):
        self.ensure_one()
        return bool(self._cr_get_real_invoice_move())

    def _cr_should_emit_ticket(self):
        self.ensure_one()
        if self.state not in ("paid", "done", "invoiced"):
            return False
        if self._cr_has_real_invoice_move():
            return False
        return self.cr_fe_status not in ("accepted", "rejected")

    def _cr_build_idempotency_key(self):
        self.ensure_one()
        return f"POS-{self.company_id.id}-{self.config_id.id}-{self.name or self.pos_reference or self.id}"

    def _cr_get_next_consecutivo_by_document_type(self, document_type):
        self.ensure_one()
        doc_type = document_type or self.cr_fe_document_type or "te"
        domain = [
            ("company_id", "=", self.company_id.id),
            ("cr_fe_document_type", "=", doc_type),
            ("cr_fe_consecutivo", "!=", False),
            ("id", "!=", self.id),
        ]
        previous_orders = self.search(domain)
        highest = 0
        pad = 10
        for order in previous_orders:
            raw = order.cr_fe_consecutivo or ""
            digits = "".join(char for char in str(raw) if char.isdigit())
            if digits:
                highest = max(highest, int(digits))
                pad = max(pad, len(digits))
        return str(highest + 1).zfill(pad)

    def action_cr_send_hacienda(self):
        for order in self:
            if order.invoice_status == "invoiced":
                order._cr_sync_from_invoice_only()
                continue
            order._cr_send_pending_te_to_hacienda(force=True)
        return True

    def action_cr_check_hacienda_status(self):
        for order in self:
            if order.invoice_status == "invoiced":
                order._cr_sync_from_invoice_only()
                continue
            order._cr_check_pending_te_status()
        return True

    def action_cr_open_fe_document(self):
        self.ensure_one()
        move = self._cr_get_real_invoice_move() or self.cr_ticket_move_id
        if not move:
            raise UserError(_("El documento electrónico se emite desde el pedido POS y no tiene account.move asociado."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Documento Electrónico"),
            "res_model": "account.move",
            "view_mode": "form",
            "res_id": move.id,
            "target": "current",
        }

    @api.model
    def create_from_ui(self, orders, draft=False):
        result = super().create_from_ui(orders, draft=draft)
        if draft:
            return result
        records = self.browse([item.get("id") if isinstance(item, dict) else item for item in result]).exists()
        records._cr_process_after_payment()
        return result

    def _process_order(self, order, draft, existing_order=False, **kwargs):
        try:
            result = super()._process_order(order, draft, existing_order, **kwargs)
        except TypeError:
            result = super()._process_order(order, draft, **kwargs)
        if draft or not result:
            return result
        order_record = self.browse(result).exists() if isinstance(result, int) else result
        order_record._cr_process_after_payment()
        return result

    def _prepare_invoice_vals(self):
        vals = super()._prepare_invoice_vals()
        config = self.config_id
        if config and config.fp_economic_activity_id and "fp_economic_activity_id" in self.env["account.move"]._fields:
            vals["fp_economic_activity_id"] = config.fp_economic_activity_id.id
        if "fp_document_type" in self.env["account.move"]._fields:
            vals.setdefault("fp_document_type", "FE")
        if "cr_pos_order_id" in self.env["account.move"]._fields:
            vals["cr_pos_order_id"] = self.id
        return vals

    def _cr_process_after_payment(self):
        self._cr_dispatch_einvoice_flow()

    def _cr_dispatch_einvoice_flow(self):
        for order in self:
            if order.config_id and not order.config_id.cr_fe_enabled:
                order.cr_fe_status = "not_applicable"
                continue

            if order.invoice_status == "invoiced":
                order._cr_sync_from_invoice_only()
                continue

            order._cr_trigger_te_flow_nonblocking()

    def _cr_sync_from_invoice_only(self):
        for order in self:
            invoice = order._cr_get_real_invoice_move()
            if not invoice:
                message = _("Pedido marcado como facturado, pero no existe account.move asociado.")
                order.write({"cr_fe_status": "error", "cr_fe_last_error": message})
                self._logger.error("POS FE inconsistencia en pedido %s: %s", order.name, message)
                continue

            order._cr_prepare_invoice_fe_values(invoice)
            mapped_status = "pending"
            if "fp_api_state" in invoice._fields and invoice.fp_api_state:
                mapped_status = order._cr_normalize_hacienda_status(invoice.fp_api_state)
            elif "fp_invoice_status" in invoice._fields and invoice.fp_invoice_status:
                mapped_status = order._cr_normalize_hacienda_status(invoice.fp_invoice_status)

            order.write(
                {
                    "cr_fe_document_type": "nc" if invoice.move_type == "out_refund" else "fe",
                    "cr_fe_clave": invoice.fp_external_id if "fp_external_id" in invoice._fields else order.cr_fe_clave,
                    "cr_fe_consecutivo": invoice.fp_consecutive_number if "fp_consecutive_number" in invoice._fields else order.cr_fe_consecutivo,
                    "cr_fe_status": mapped_status,
                    "cr_fe_xml_attachment_id": invoice.fp_xml_attachment_id.id if "fp_xml_attachment_id" in invoice._fields and invoice.fp_xml_attachment_id else order.cr_fe_xml_attachment_id.id,
                    "cr_fe_response_attachment_id": invoice.fp_response_xml_attachment_id.id
                    if "fp_response_xml_attachment_id" in invoice._fields and invoice.fp_response_xml_attachment_id
                    else order.cr_fe_response_attachment_id.id,
                }
            )

    def _cr_trigger_te_flow_nonblocking(self):
        for order in self:
            if not order._cr_should_emit_ticket():
                continue
            order._cr_prepare_te_document()
            order.write({"cr_fe_status": "pending", "cr_fe_last_error": False, "cr_fe_next_try": fields.Datetime.now()})

    def _cr_prepare_invoice_fe_values(self, invoice):
        vals = {
            "cr_pos_order_id": self.id,
            "cr_pos_document_type": "nc" if invoice.move_type == "out_refund" else "fe",
            "cr_pos_fe_state": "to_send",
        }
        if "fp_economic_activity_id" in invoice._fields and self.config_id.fp_economic_activity_id:
            vals["fp_economic_activity_id"] = self.config_id.fp_economic_activity_id.id
        if "fp_document_type" in invoice._fields:
            vals["fp_document_type"] = "FE"
        if "fp_payment_method" in invoice._fields:
            vals["fp_payment_method"] = self._cr_pos_payment_method_code()
        if "fp_sale_condition" in invoice._fields:
            vals["fp_sale_condition"] = self._cr_pos_payment_condition_code()
        invoice.write({k: v for k, v in vals.items() if v})

    def _cr_get_primary_payment_method(self):
        self.ensure_one()
        if not self.payment_ids:
            return self.env["pos.payment.method"]
        return self.payment_ids.sorted(key=lambda pay: (-abs(pay.amount), pay.id))[0].payment_method_id

    def _cr_pos_payment_method_code(self):
        self.ensure_one()
        method = self._cr_get_primary_payment_method()
        return method.fp_payment_method if method else False

    def _cr_pos_payment_condition_code(self):
        self.ensure_one()
        method = self._cr_get_primary_payment_method()
        return method.fp_sale_condition if method else False

    def _cr_send_to_hacienda(self, force=False):
        self.ensure_one()
        if self.invoice_status == "invoiced":
            self._cr_sync_from_invoice_only()
            return True
        return self._cr_send_pending_te_to_hacienda(force=force)

    def _cr_check_hacienda_status(self):
        self.ensure_one()
        if self.invoice_status == "invoiced":
            self._cr_sync_from_invoice_only()
            return True
        return self._cr_check_pending_te_status()

    def _cr_prepare_te_document(self):
        self.ensure_one()
        if self.invoice_status == "invoiced" or not self._cr_should_emit_ticket():
            return False

        service_model = self.env["l10n_cr.einvoice.service"]
        idempotency_key = self.cr_fe_idempotency_key or self._cr_build_idempotency_key()
        consecutivo = self.cr_fe_consecutivo or self._cr_get_next_consecutivo_by_document_type("te")
        clave = self.cr_fe_clave or f"TE-{self.company_id.id}-{self.id}-{consecutivo}"

        result = service_model.build_te_xml_from_pos(self.id, consecutivo=consecutivo, idempotency_key=idempotency_key, clave=clave)
        self.write(
            {
                "cr_fe_status": "pending",
                "cr_fe_document_type": "te",
                "cr_fe_idempotency_key": idempotency_key,
                "cr_fe_consecutivo": consecutivo,
                "cr_fe_clave": clave,
                "cr_fe_xml_attachment_id": result.get("xml_attachment_id") or self.cr_fe_xml_attachment_id.id,
            }
        )
        return True

    def _cr_send_pending_te_to_hacienda(self, force=False):
        self.ensure_one()
        if self.invoice_status == "invoiced":
            return False
        if self.cr_fe_status not in ("pending", "error_retry") and not force:
            return False
        if not self.cr_fe_xml_attachment_id:
            self._cr_prepare_te_document()

        service_model = self.env["l10n_cr.einvoice.service"]
        try:
            result = service_model.send_to_hacienda(self.id)
            normalized_status = self._cr_normalize_hacienda_status((result or {}).get("status"), default_status=True)
            self.write(
                {
                    "cr_fe_status": normalized_status if result.get("ok") else "error_retry",
                    "cr_fe_retry_count": 0 if result.get("ok") else self.cr_fe_retry_count,
                    "cr_fe_last_error": False if result.get("ok") else result.get("reason"),
                    "cr_fe_next_try": fields.Datetime.now() + timedelta(minutes=5) if not result.get("ok") else False,
                    "cr_fe_last_send_date": fields.Datetime.now(),
                    "cr_fe_response_attachment_id": result.get("response_attachment_id") or self.cr_fe_response_attachment_id.id,
                }
            )
            return bool(result.get("ok"))
        except Exception as error:  # noqa: BLE001
            retries = self.cr_fe_retry_count + 1
            self.write(
                {
                    "cr_fe_status": "error_retry",
                    "cr_fe_retry_count": retries,
                    "cr_fe_last_error": str(error),
                    "cr_fe_next_try": fields.Datetime.now() + timedelta(minutes=min(60, retries * 5)),
                }
            )
            return False

    def _cr_check_pending_te_status(self):
        self.ensure_one()
        if self.invoice_status == "invoiced":
            return False
        service_model = self.env["l10n_cr.einvoice.service"]
        status = service_model.consult_status(self.id)

        if isinstance(status, dict):
            normalized = self._cr_normalize_hacienda_status(status.get("status"), default_status=False)
            values = {
                "cr_fe_status": normalized,
                "cr_fe_next_try": False if normalized in ("accepted", "rejected") else fields.Datetime.now() + timedelta(minutes=5),
            }
            if status.get("response_attachment_id"):
                values["cr_fe_response_attachment_id"] = status.get("response_attachment_id")
            self.write(values)
        return True

    @api.model
    def _cr_get_pending_send_ticket_targets(self, limit=50):
        domain = [
            ("state", "in", ["paid", "done", "invoiced"]),
            ("invoice_status", "!=", "invoiced"),
            ("cr_fe_status", "in", ["pending", "error_retry"]),
            ("cr_fe_xml_attachment_id", "!=", False),
            "|",
            ("cr_fe_next_try", "=", False),
            ("cr_fe_next_try", "<=", fields.Datetime.now()),
        ]
        orders = self.search(domain, limit=limit, order="cr_fe_next_try asc, id asc")
        return [(order, "pos_ticket") for order in orders]

    @api.model
    def _cr_get_pending_status_ticket_targets(self, limit=50):
        domain = [
            ("state", "in", ["paid", "done", "invoiced"]),
            ("invoice_status", "!=", "invoiced"),
            ("cr_fe_status", "in", ["sent", "processing"]),
            ("cr_fe_clave", "!=", False),
            "|",
            ("cr_fe_next_try", "=", False),
            ("cr_fe_next_try", "<=", fields.Datetime.now()),
        ]
        orders = self.search(domain, limit=limit, order="cr_fe_next_try asc, id asc")
        return [(order, "pos_ticket") for order in orders]

    @api.model
    def _cron_cr_pos_send_pending_te(self, limit=50):
        for order, _target in self._cr_get_pending_send_ticket_targets(limit=limit):
            order._cr_send_pending_te_to_hacienda()
        return True

    @api.model
    def _cron_cr_pos_check_pending_te_status(self, limit=50):
        for order, _target in self._cr_get_pending_status_ticket_targets(limit=limit):
            order._cr_check_pending_te_status()
        return True
