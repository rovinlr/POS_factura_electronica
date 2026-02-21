from datetime import timedelta
from importlib import import_module

from odoo import _, api, fields, models
from odoo.exceptions import UserError



class PosOrder(models.Model):
    _inherit = "pos.order"

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
            ("to_send", "Pendiente de envío"),
            ("sent", "Enviado"),
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
            "pendiente": "to_send",
            "to_send": "to_send",
            "draft": "draft",
        }
        if normalized in mapping:
            return mapping[normalized]
        if default_status:
            return "sent"
        return self.cr_fe_status or "to_send"

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

    def action_cr_send_hacienda(self):
        for order in self:
            order._cr_send_to_hacienda(force=True)
        return True

    def action_cr_check_hacienda_status(self):
        for order in self:
            order._cr_check_hacienda_status()
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

    def _cr_process_after_payment(self):
        for order in self:
            if order.config_id and not order.config_id.cr_fe_enabled:
                order.cr_fe_status = "not_applicable"
                continue

            if order._cr_has_real_invoice_move():
                invoice = order._cr_get_real_invoice_move()
                order._cr_prepare_invoice_fe_values(invoice)
                order.write({"cr_fe_document_type": "nc" if invoice.move_type == "out_refund" else "fe"})
                continue

            if order._cr_should_emit_ticket():
                order._cr_enqueue_ticket_for_send()

    def _cr_prepare_invoice_fe_values(self, invoice):
        vals = {
            "cr_pos_order_id": self.id,
            "cr_pos_document_type": "nc" if invoice.move_type == "out_refund" else "fe",
            "cr_pos_fe_state": "to_send",
        }
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
        if self._cr_has_real_invoice_move():
            move = self._cr_get_real_invoice_move()
            move._cr_pos_enqueue_for_send(force=force)
            return move._cr_pos_send_to_hacienda()
        return self._cr_send_ticket_from_order(force=force)

    def _cr_check_hacienda_status(self):
        self.ensure_one()
        if self._cr_has_real_invoice_move():
            return self._cr_get_real_invoice_move()._cr_pos_check_hacienda_status()
        return self._cr_check_ticket_status_from_order()

    def _cr_enqueue_ticket_for_send(self, force=False):
        for order in self:
            if not order._cr_should_emit_ticket():
                continue
            if order.cr_fe_status in ("to_send", "sent") and not force:
                continue
            order.write(
                {
                    "cr_fe_status": "to_send",
                    "cr_fe_idempotency_key": order.cr_fe_idempotency_key or order._cr_build_idempotency_key(),
                    "cr_fe_next_try": fields.Datetime.now(),
                    "cr_fe_last_error": False,
                }
            )

    def _cr_send_ticket_from_order(self, force=False):
        self.ensure_one()
        if not self._cr_should_emit_ticket():
            return False

        service = self._cr_service()
        payload = service.build_payload_from_pos_order(self)
        payload["idempotency_key"] = self.cr_fe_idempotency_key or self._cr_build_idempotency_key()

        can_process, reason = service.ensure_idempotency(self, payload)
        if not can_process and not force:
            return reason == "already_processed"

        self.write({"cr_fe_status": "to_send", "cr_fe_idempotency_key": payload["idempotency_key"]})
        try:
            result = self._cr_send_ticket_via_l10n_service(service, payload)
            normalized_status = self._cr_normalize_hacienda_status((result or {}).get("status"), default_status=True)
            self.write(
                {
                    "cr_fe_status": normalized_status if result.get("ok") else "error",
                    "cr_fe_retry_count": 0,
                    "cr_fe_last_error": False,
                    "cr_fe_next_try": False,
                    "cr_fe_last_send_date": fields.Datetime.now(),
                }
            )
            return bool(result.get("ok"))
        except Exception as error:  # noqa: BLE001
            retries = self.cr_fe_retry_count + 1
            self.write(
                {
                    "cr_fe_status": "error",
                    "cr_fe_retry_count": retries,
                    "cr_fe_last_error": str(error),
                    "cr_fe_next_try": fields.Datetime.now() + timedelta(minutes=min(60, retries * 5)),
                }
            )
            return False

    def _cr_send_ticket_via_l10n_service(self, service, payload):
        """Delegate TE send to l10n_cr_einvoice when available.

        Priority:
        1) public model service in l10n_cr_einvoice (`l10n_cr.einvoice.service`)
        2) python service adapter used by this bridge.
        """
        self.ensure_one()

        model_name = "l10n_cr.einvoice.service"
        if model_name in self.env:
            service_model = self.env[model_name]
            method_names = [
                "enqueue_from_pos_order",
                "send_from_pos_order",
                "process_pos_order",
            ]
            for method_name in method_names:
                if hasattr(service_model, method_name):
                    method = getattr(service_model, method_name)
                    try:
                        result = method(self.id, payload=payload, company_id=self.company_id.id, idempotency_key=payload["idempotency_key"])
                    except TypeError:
                        try:
                            result = method(self.id, payload)
                        except TypeError:
                            result = method(self.id)
                    return result if isinstance(result, dict) else {"ok": bool(result), "status": "sent"}

        return service.process_full_flow(self, payload, doc_type="te")

    def _cr_check_ticket_status_from_order(self):
        self.ensure_one()
        service = self._cr_service()

        model_name = "l10n_cr.einvoice.service"
        status = False
        if model_name in self.env:
            service_model = self.env[model_name]
            methods = ["check_status_from_pos_order", "check_status", "get_pos_order_status"]
            for method_name in methods:
                if hasattr(service_model, method_name):
                    method = getattr(service_model, method_name)
                    try:
                        status = method(self.id, idempotency_key=self.cr_fe_idempotency_key)
                    except TypeError:
                        status = method(self.id)
                    break

        if not status:
            status = getattr(service, "check_status_from_pos_order", lambda order: {"status": order.cr_fe_status})(self)

        if isinstance(status, dict):
            normalized = self._cr_normalize_hacienda_status(status.get("status"), default_status=False)
            self.write({"cr_fe_status": normalized})
        return True

    @api.model
    def _cr_get_pending_send_ticket_targets(self, limit=50):
        domain = [
            ("state", "in", ["paid", "done", "invoiced"]),
            ("cr_fe_status", "in", ["to_send", "error"]),
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
            ("cr_fe_status", "in", ["sent"]),
            "|",
            ("cr_fe_next_try", "=", False),
            ("cr_fe_next_try", "<=", fields.Datetime.now()),
        ]
        orders = self.search(domain, limit=limit, order="cr_fe_next_try asc, id asc")
        return [(order, "pos_ticket") for order in orders]
