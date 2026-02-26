import logging
from collections import defaultdict
from datetime import timedelta

from psycopg2 import IntegrityError

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class PosOrder(models.Model):
    _inherit = "pos.order"

    _logger = logging.getLogger(__name__)

    _CR_INVOICE_MOVE_TYPES = ("out_invoice", "out_refund")
    _CR_FINAL_STATES = ("accepted", "rejected", "not_applicable")

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
        index=True,
    )
    cr_fe_error_code = fields.Char(string="Código de error FE", copy=False)
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
    fp_document_type = fields.Selection(
        selection="_selection_fp_document_type",
        string="Tipo de comprobante FE",
        compute="_compute_fp_pos_fe_fields",
        store=True,
    )
    fp_sale_condition = fields.Selection(
        selection="_selection_fp_sale_condition",
        string="Condición de venta FE",
        compute="_compute_fp_pos_fe_fields",
        store=True,
    )
    fp_payment_method = fields.Selection(
        selection="_selection_fp_payment_method",
        string="Medio de pago FE",
        compute="_compute_fp_pos_fe_fields",
        store=True,
    )
    fp_economic_activity_id = fields.Many2one(
        "fp.economic.activity",
        string="Actividad económica FE",
        compute="_compute_fp_pos_fe_fields",
        store=True,
    )

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
                order.cr_fe_document_type = order._cr_get_pos_document_type()
            else:
                order.cr_fe_document_type = False

    def _compute_cr_fe_attachment_ids(self):
        attachments_by_order = defaultdict(lambda: self.env["ir.attachment"])
        if self.ids:
            attachments = self.env["ir.attachment"].search(
                [("res_model", "=", "pos.order"), ("res_id", "in", self.ids)], order="id desc"
            )
            for attachment in attachments:
                attachments_by_order[attachment.res_id] |= attachment

        for order in self:
            order.cr_fe_attachment_ids = attachments_by_order[order.id]

    def _cr_service(self):
        return self.env.registry.models.get("l10n_cr.einvoice.service") and self.env["l10n_cr.einvoice.service"]

    def _cr_call_target_method(self, target, method_name, args, kwargs):
        method = getattr(target, method_name, False)
        if not method:
            return False, None
        try:
            return True, method(*args, **kwargs)
        except TypeError as error:
            # Compatibilidad para implementaciones en pos.order que no reciben order_id como primer argumento.
            if target is self and args and args[0] == self.id:
                try:
                    return True, method(*args[1:], **kwargs)
                except TypeError:
                    pass
            raise error

    def _cr_call_service_method(self, method_names, *args, **kwargs):
        """Call first available FE backend method from service or pos.order."""
        self.ensure_one()
        tried_backends = []
        backends = []
        service = self._cr_service()
        if service:
            backends.append(("l10n_cr.einvoice.service", service))
        backends.append(("pos.order", self))

        for backend_name, backend in backends:
            tried_backends.append(backend_name)
            for method_name in method_names:
                found, result = self._cr_call_target_method(backend, method_name, args, kwargs)
                if found:
                    return result

        raise UserError(
            _(
                "No se encontró un método compatible para FE POS. "
                "Backends revisados: %(backends)s. Métodos buscados: %(methods)s."
            )
            % {
                "backends": ", ".join(tried_backends),
                "methods": ", ".join(method_names),
            }
        )

    def _cr_call_status_backend(self):
        self.ensure_one()
        status_methods = [
            "action_check_hacienda_status",
            "action_consult_hacienda",
            "action_get_hacienda_status",
            "action_refresh_hacienda_status",
        ]
        for method_name in status_methods:
            found, _result = self._cr_call_target_method(self, method_name, tuple(), {})
            if found:
                return True
        raise UserError(
            _(
                "No se encontró método público para consultar estado FE en pos.order y tampoco un "
                "servicio compatible en l10n_cr_einvoice."
            )
        )

    def _selection_fp_document_type(self):
        field = self.env["account.move"]._fields.get("fp_document_type")
        if field and field.selection:
            selection = field.selection(self.env) if callable(field.selection) else field.selection
            if selection:
                return selection
        return [("TE", "Tiquete Electrónico"), ("FE", "Factura Electrónica"), ("NC", "Nota de Crédito")]

    def _selection_fp_sale_condition(self):
        field = self.env["account.move"]._fields.get("fp_sale_condition")
        if field and field.selection:
            selection = field.selection(self.env) if callable(field.selection) else field.selection
            if selection:
                return selection
        return [("01", "Contado"), ("02", "Crédito")]

    def _selection_fp_payment_method(self):
        field = self.env["account.move"]._fields.get("fp_payment_method")
        if field and field.selection:
            selection = field.selection(self.env) if callable(field.selection) else field.selection
            if selection:
                return selection
        return [("01", "Efectivo")]

    @api.depends("config_id.fp_economic_activity_id", "payment_ids.amount", "payment_ids.payment_method_id")
    def _compute_fp_pos_fe_fields(self):
        for order in self:
            doc_type = "NC" if order.amount_total < 0 else ("FE" if order._cr_has_real_invoice_move() else "TE")
            method = order._cr_get_primary_payment_method() if order.payment_ids else self.env["pos.payment.method"]
            order.fp_document_type = doc_type
            order.fp_sale_condition = method.fp_sale_condition if method else False
            order.fp_payment_method = method.fp_payment_method if method else False
            order.fp_economic_activity_id = order.config_id.fp_economic_activity_id

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
            "procesando": "processing",
            "processing": "processing",
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
        return self.cr_fe_status not in self._CR_FINAL_STATES

    def _cr_get_pos_document_type(self):
        self.ensure_one()
        return "nc" if self.amount_total < 0 else "te"

    def _cr_build_idempotency_key(self):
        self.ensure_one()
        return f"POS-{self.company_id.id}-{self.config_id.id}-{self.name or self.pos_reference or self.id}"

    def _cr_sequence_code(self, document_type):
        self.ensure_one()
        doc_type = (document_type or "te").lower()
        return f"cr.pos.fe.{self.company_id.id}.{doc_type}"

    def _cr_get_or_create_sequence(self, document_type):
        self.ensure_one()
        sequence_model = self.env["ir.sequence"].sudo().with_company(self.company_id)
        code = self._cr_sequence_code(document_type)
        sequence = sequence_model.search([("code", "=", code), ("company_id", "=", self.company_id.id)], limit=1)
        if not sequence:
            sequence = sequence_model.create(
                {
                    "name": f"POS FE {self.company_id.display_name} {document_type.upper()}",
                    "code": code,
                    "company_id": self.company_id.id,
                    "implementation": "no_gap",
                    "padding": 10,
                    "number_increment": 1,
                    "number_next": 1,
                }
            )
        return sequence

    def _cr_get_next_consecutivo_by_document_type(self, document_type):
        self.ensure_one()
        service = self._cr_service()
        doc_code = (document_type or self.cr_fe_document_type or "te").upper()
        if service:
            for method_name in (
                "get_next_consecutivo",
                "get_next_consecutive",
                "get_next_consecutivo_by_document_type",
                "get_last_consecutivo_by_document_type",
            ):
                method = getattr(service, method_name, False)
                if not method:
                    continue
                try:
                    result = method(company_id=self.company_id.id, document_type=doc_code)
                except TypeError:
                    result = method(self.company_id.id, doc_code)
                if result:
                    value = result.get("consecutivo") if isinstance(result, dict) else result
                    if method_name == "get_last_consecutivo_by_document_type":
                        digits = "".join(char for char in str(value) if char.isdigit())
                        next_number = int(digits[-10:] or "0") + 1
                        return str(next_number).zfill(10)
                    return str(value)
        sequence = self._cr_get_or_create_sequence(document_type or self.cr_fe_document_type or "te")
        return sequence.next_by_id()

    def _cr_get_fe_document_code(self, document_type=None):
        self.ensure_one()
        doc_type = (document_type or self.cr_fe_document_type or self._cr_get_pos_document_type() or "te").lower()
        mapping = {"fe": "01", "nc": "03", "te": "04"}
        return mapping.get(doc_type, "04")

    def _cr_generate_fe_consecutivo(self, document_type=None):
        self.ensure_one()
        branch = str(getattr(self.company_id, "fp_branch_code", "") or "1")
        terminal = str(getattr(self.company_id, "fp_terminal_code", "") or "1")
        sequence = self._cr_get_next_consecutivo_by_document_type(document_type)
        doc_code = self._cr_get_fe_document_code(document_type=document_type)
        return f"{branch.zfill(3)}{terminal.zfill(5)}{doc_code}{str(sequence).zfill(10)}"

    def _cr_generate_fe_clave(self, consecutivo):
        self.ensure_one()
        company_partner = self.company_id.partner_id
        vat_raw = self.company_id.vat or (company_partner and company_partner.vat) or ""
        vat_digits = "".join(char for char in str(vat_raw) if char.isdigit())[-12:].zfill(12)
        country_code = "506"
        if self.company_id.country_id and getattr(self.company_id.country_id, "phone_code", False):
            country_code = str(self.company_id.country_id.phone_code).zfill(3)
        issue_date = fields.Date.context_today(self)
        issue_ddmmyy = issue_date.strftime("%d%m%y")
        security_code = str(self.id or 0).zfill(8)[-8:]
        situation = "1"
        return f"{country_code}{issue_ddmmyy}{vat_digits}{consecutivo}{situation}{security_code}"

    def _cr_validate_before_send(self):
        self.ensure_one()
        if self.state not in ("paid", "done", "invoiced"):
            raise UserError(_("El pedido debe estar pagado/finalizado para emitir FE."))
        if not self.company_id.vat and not self.company_id.partner_id.vat:
            raise UserError(_("La compañía emisora no tiene identificación (VAT) configurada."))
        if not self.payment_ids:
            raise UserError(_("El pedido no tiene pagos registrados."))

        expected_total = self.amount_tax + (self.amount_total - self.amount_tax)
        if abs(expected_total - self.amount_total) > 0.01:
            raise UserError(_("Inconsistencia en totales del pedido; revise impuestos/líneas antes de enviar."))

        method = self._cr_get_primary_payment_method()
        if method and (not method.fp_payment_method or not method.fp_sale_condition):
            raise UserError(_("El método de pago POS principal debe tener código FE y condición FE configurados."))

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
                order.write({"cr_fe_status": "error", "cr_fe_error_code": "invoice_missing", "cr_fe_last_error": message})
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
                    "cr_fe_error_code": False,
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
            try:
                order._cr_prepare_te_document()
                order.write(
                    {
                        "cr_fe_status": "pending",
                        "cr_fe_last_error": False,
                        "cr_fe_error_code": False,
                        "cr_fe_next_try": fields.Datetime.now(),
                    }
                )
            except UserError as error:
                order.write(
                    {
                        "cr_fe_status": "error",
                        "cr_fe_error_code": "validation",
                        "cr_fe_last_error": str(error),
                    }
                )

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

        self._cr_validate_before_send()

        idempotency_key = self.cr_fe_idempotency_key or self._cr_build_idempotency_key()
        doc_type = self._cr_get_pos_document_type()
        consecutivo = self.cr_fe_consecutivo or self._cr_generate_fe_consecutivo(document_type=doc_type)
        clave = self.cr_fe_clave or self._cr_generate_fe_clave(consecutivo)
        payload = self._cr_build_pos_payload(consecutivo=consecutivo, clave=clave, document_type=doc_type)

        result = self._cr_call_service_method(
            ["build_pos_xml_from_order", "prepare_pos_document", "prepare_from_pos_order", "enqueue_from_pos_order"],
            self.id,
            consecutivo=consecutivo,
            idempotency_key=idempotency_key,
            clave=clave,
            document_type=doc_type,
            payload=payload,
        )

        values = {
            "cr_fe_status": "pending",
            "cr_fe_document_type": doc_type,
            "cr_fe_idempotency_key": idempotency_key,
            "cr_fe_consecutivo": consecutivo,
            "cr_fe_clave": clave,
            "cr_fe_xml_attachment_id": result.get("xml_attachment_id") or self.cr_fe_xml_attachment_id.id,
            "cr_fe_error_code": False,
        }
        try:
            self.write(values)
        except IntegrityError as error:
            self.env.cr.rollback()
            raise UserError(_("La llave de idempotencia ya fue utilizada para esta compañía.")) from error
        return True

    def _cr_build_pos_payload(self, consecutivo, clave, document_type):
        self.ensure_one()
        lines = []
        for line in self.lines:
            taxes = line.tax_ids_after_fiscal_position.compute_all(
                line.price_unit,
                currency=self.pricelist_id.currency_id,
                quantity=line.qty,
                product=line.product_id,
                partner=self.partner_id,
            )
            lines.append(
                {
                    "line_id": line.id,
                    "product_id": line.product_id.id,
                    "name": line.full_product_name or line.product_id.display_name,
                    "qty": line.qty,
                    "uom": line.product_uom_id.name if line.product_uom_id else False,
                    "price_unit": line.price_unit,
                    "discount": line.discount,
                    "subtotal": line.price_subtotal,
                    "subtotal_incl": line.price_subtotal_incl,
                    "taxes": taxes.get("taxes", []),
                }
            )

        return {
            "order_id": self.id,
            "name": self.name,
            "pos_reference": self.pos_reference,
            "company_id": self.company_id.id,
            "partner_id": self.partner_id.id,
            "currency_id": self.pricelist_id.currency_id.id,
            "amount_total": self.amount_total,
            "amount_tax": self.amount_tax,
            "amount_paid": self.amount_paid,
            "document_type": document_type.upper(),
            "fp_document_type": self.fp_document_type,
            "fp_sale_condition": self.fp_sale_condition,
            "fp_payment_method": self.fp_payment_method,
            "fp_economic_activity_id": self.fp_economic_activity_id.id,
            "consecutivo": consecutivo,
            "clave": clave,
            "lines": lines,
        }

    def _cr_send_pending_te_to_hacienda(self, force=False):
        self.ensure_one()
        if self.invoice_status == "invoiced":
            return False
        if self.cr_fe_status not in ("pending", "error_retry") and not force:
            return False
        if not self.cr_fe_xml_attachment_id:
            self._cr_prepare_te_document()

        try:
            self._cr_validate_before_send()
            result = self._cr_call_service_method(
                [
                    "send_to_hacienda",
                    "enqueue_from_pos_order",
                    "send_from_pos_order",
                    "process_pos_order",
                ],
                self.id,
                document_type=self.cr_fe_document_type or self._cr_get_pos_document_type(),
                idempotency_key=self.cr_fe_idempotency_key,
                company_id=self.company_id.id,
            )
            normalized_status = self._cr_normalize_hacienda_status((result or {}).get("status"), default_status=True)
            self.write(
                {
                    "cr_fe_status": normalized_status if result.get("ok") else "error_retry",
                    "cr_fe_retry_count": 0 if result.get("ok") else self.cr_fe_retry_count,
                    "cr_fe_last_error": False if result.get("ok") else result.get("reason"),
                    "cr_fe_error_code": False if result.get("ok") else "send_error",
                    "cr_fe_next_try": fields.Datetime.now() + timedelta(minutes=5) if not result.get("ok") else False,
                    "cr_fe_last_send_date": fields.Datetime.now(),
                    "cr_fe_response_attachment_id": result.get("response_attachment_id") or self.cr_fe_response_attachment_id.id,
                }
            )
            return bool(result.get("ok"))
        except UserError as error:
            self.write(
                {
                    "cr_fe_status": "error",
                    "cr_fe_error_code": "validation",
                    "cr_fe_last_error": str(error),
                    "cr_fe_next_try": False,
                }
            )
            return False
        except Exception as error:  # noqa: BLE001
            retries = self.cr_fe_retry_count + 1
            self.write(
                {
                    "cr_fe_status": "error_retry",
                    "cr_fe_retry_count": retries,
                    "cr_fe_error_code": "send_exception",
                    "cr_fe_last_error": str(error),
                    "cr_fe_next_try": fields.Datetime.now() + timedelta(minutes=min(60, retries * 5)),
                }
            )
            return False

    def _cr_check_pending_te_status(self):
        self.ensure_one()
        if self.invoice_status == "invoiced":
            return False

        status = False
        try:
            status = self._cr_call_service_method(
                ["consult_status", "check_status_from_pos_order", "check_status", "get_pos_order_status"],
                self.id,
                idempotency_key=self.cr_fe_idempotency_key,
            )
        except UserError:
            self._cr_call_status_backend()
            status = {"status": self.cr_fe_status}

        if isinstance(status, dict):
            normalized = self._cr_normalize_hacienda_status(status.get("status"), default_status=False)
            values = {
                "cr_fe_status": normalized,
                "cr_fe_error_code": False if normalized in self._CR_FINAL_STATES else self.cr_fe_error_code,
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
            ("cr_fe_status", "in", ["pending", "error_retry"]),
            ("cr_fe_xml_attachment_id", "!=", False),
            "|",
            ("cr_fe_next_try", "=", False),
            ("cr_fe_next_try", "<=", fields.Datetime.now()),
        ]
        orders = self.search(domain, order="cr_fe_next_try asc, id asc")
        orders = orders.filtered(lambda order: not order._cr_has_real_invoice_move())
        if limit:
            orders = orders[:limit]
        return [(order, "pos_ticket") for order in orders]

    @api.model
    def _cr_get_pending_status_ticket_targets(self, limit=50):
        domain = [
            ("state", "in", ["paid", "done", "invoiced"]),
            ("cr_fe_status", "in", ["sent", "processing"]),
            ("cr_fe_clave", "!=", False),
            "|",
            ("cr_fe_next_try", "=", False),
            ("cr_fe_next_try", "<=", fields.Datetime.now()),
        ]
        orders = self.search(domain, order="cr_fe_next_try asc, id asc")
        orders = orders.filtered(lambda order: not order._cr_has_real_invoice_move())
        if limit:
            orders = orders[:limit]
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
