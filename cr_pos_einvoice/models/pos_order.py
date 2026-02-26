import logging
import base64
import hashlib
from collections import defaultdict
from datetime import timedelta

from psycopg2 import IntegrityError
from psycopg2.errors import SerializationFailure

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
    cr_fe_reference_document_type = fields.Char(
        string="Tipo documento referencia FE",
        compute="_compute_cr_fe_reference_preview",
    )
    cr_fe_reference_document_number = fields.Char(
        string="Número documento referencia FE",
        compute="_compute_cr_fe_reference_preview",
    )
    cr_fe_reference_issue_date = fields.Date(
        string="Fecha emisión referencia FE",
        compute="_compute_cr_fe_reference_preview",
    )
    cr_fe_reference_code = fields.Char(
        string="Código referencia FE",
        compute="_compute_cr_fe_reference_preview",
    )
    cr_fe_reference_reason = fields.Char(
        string="Razón referencia FE",
        compute="_compute_cr_fe_reference_preview",
    )
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
            selection = field.selection(self.env["account.move"]) if callable(field.selection) else field.selection
            if selection:
                return selection
        return [("TE", "Tiquete Electrónico"), ("FE", "Factura Electrónica"), ("NC", "Nota de Crédito")]

    def _selection_fp_sale_condition(self):
        field = self.env["account.move"]._fields.get("fp_sale_condition")
        if field and field.selection:
            selection = field.selection(self.env["account.move"]) if callable(field.selection) else field.selection
            if selection:
                return selection
        return [("01", "Contado"), ("02", "Crédito")]

    def _selection_fp_payment_method(self):
        field = self.env["account.move"]._fields.get("fp_payment_method")
        if field and field.selection:
            selection = field.selection(self.env["account.move"]) if callable(field.selection) else field.selection
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

    @api.depends(
        "amount_total",
        "date_order",
        "cr_fe_document_type",
        "lines.refunded_orderline_id.order_id",
        "lines.refunded_orderline_id.order_id.cr_fe_clave",
        "lines.refunded_orderline_id.order_id.cr_fe_consecutivo",
        "lines.refunded_orderline_id.order_id.cr_fe_document_type",
        "lines.refunded_orderline_id.order_id.date_order",
        "lines.refunded_orderline_id.order_id.account_move",
    )
    def _compute_cr_fe_reference_preview(self):
        for order in self:
            reference_data = order._cr_get_refund_reference_data()
            order.cr_fe_reference_document_type = reference_data.get("document_type")
            order.cr_fe_reference_document_number = reference_data.get("number")
            order.cr_fe_reference_issue_date = reference_data.get("issue_date")
            order.cr_fe_reference_code = reference_data.get("code")
            order.cr_fe_reference_reason = reference_data.get("reason")

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

    def _cr_sync_last_consecutivo_in_einvoice_config(self, document_type, consecutivo):
        """Best-effort sync with FE configuration's "último número" counters."""
        self.ensure_one()
        if not consecutivo:
            return False

        digits = "".join(char for char in str(consecutivo) if char.isdigit())
        last_number = str(int(digits[-10:] or "0"))

        service = self._cr_service()
        doc_code = (document_type or self.cr_fe_document_type or "te").upper()
        sync_methods = (
            "set_last_consecutivo_by_document_type",
            "update_last_consecutivo_by_document_type",
            "set_last_consecutive_by_document_type",
            "set_last_number_by_document_type",
            "update_last_number_by_document_type",
        )
        for method_name in sync_methods:
            method = getattr(service, method_name, False)
            if not method:
                continue
            try:
                method(company_id=self.company_id.id, document_type=doc_code, consecutivo=last_number)
            except TypeError:
                method(self.company_id.id, doc_code, last_number)
            return True

        company = self.company_id.sudo()
        company_fields = getattr(company, "_fields", {})
        fallback_fields = {
            "TE": ("fp_consecutive_te", "fp_consecutive_fe"),
            "FE": ("fp_consecutive_fe",),
            "NC": ("fp_consecutive_nc",),
        }
        for field_name in fallback_fields.get(doc_code, ("fp_consecutive_fe",)):
            if field_name in company_fields:
                company.write({field_name: last_number})
                return True

        return False

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
        return self._cr_attach_fe_fields_to_ui_result(result)

    @api.model
    def _cr_attach_fe_fields_to_ui_result(self, result):
        """Attach FE fields to create_from_ui response for immediate POS printing."""

        order_ids = [item.get("id") if isinstance(item, dict) else item for item in (result or [])]
        order_ids = [order_id for order_id in order_ids if order_id]
        if not order_ids:
            return result

        fields_to_read = [
            "id",
            "cr_fe_document_type",
            "cr_fe_consecutivo",
            "cr_fe_clave",
            "cr_fe_status",
            "fp_payment_method",
        ]
        order_data = {
            row["id"]: row
            for row in self.browse(order_ids)
            .exists()
            .with_context(prefetch_fields=False)
            .read(fields_to_read)
        }

        enriched = []
        for item in result:
            if not isinstance(item, dict):
                enriched.append(item)
                continue
            order_id = item.get("id")
            payload = order_data.get(order_id, {})
            enriched.append({**item, **payload})
        return enriched

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
        vals.update(self._cr_build_refund_reference_values())
        return vals

    def _generate_pos_order_invoice(self, *args, **kwargs):
        """Create POS invoice without triggering email delivery from POS.

        When an order is marked `to_invoice`, `l10n_cr_einvoice` must own the FE
        flow (XML/sign/send/email). We force any known email flag to False to
        prevent POS from preempting that process.
        """

        for key in ("send_email", "email", "mail_invoice", "send_mail"):
            if key in kwargs:
                kwargs[key] = False
        return super()._generate_pos_order_invoice(*args, **kwargs)

    def _cr_get_origin_order_for_refund(self):
        """Find the original POS order referenced by refunded lines."""
        self.ensure_one()
        if self.amount_total >= 0:
            return self.env["pos.order"]

        origin_order = self.lines.mapped("refunded_orderline_id.order_id")
        if not origin_order:
            return self.env["pos.order"]
        return origin_order.sorted("date_order", reverse=True)[:1]

    def _cr_get_origin_invoice_for_refund(self):
        """Find the original customer invoice referenced by a POS refund order."""
        self.ensure_one()
        origin_order = self._cr_get_origin_order_for_refund()
        if not origin_order:
            return self.env["account.move"]

        candidate_moves = origin_order.mapped("account_move").filtered(
            lambda move: move.move_type == "out_invoice" and move.state != "cancel"
        )
        if candidate_moves:
            return candidate_moves.sorted("invoice_date", reverse=True)[:1]
        return self.env["account.move"]

    def _cr_build_refund_reference_values(self):
        """Populate FE reference fields when POS generates a credit note (NC)."""
        self.ensure_one()
        reference_data = self._cr_get_refund_reference_data()
        if not reference_data:
            return {}

        move_fields = self.env["account.move"]._fields
        origin_order = self._cr_get_origin_order_for_refund()
        origin_invoice = self._cr_get_origin_invoice_for_refund()

        origin_doc_type = (origin_order.cr_fe_document_type if origin_order else False) or (
            "fe" if origin_invoice else False
        )
        reference_doc_type = {
            "fe": "01",  # Factura Electrónica
            "te": "04",  # Tiquete Electrónico
            "nc": "03",  # Nota de Crédito
        }.get(origin_doc_type, "01")

        reference_number = (
            (origin_order.cr_fe_clave if origin_order else False)
            or (origin_order.cr_fe_consecutivo if origin_order else False)
            or (getattr(origin_invoice, "l10n_cr_clave", False) if origin_invoice else False)
            or (getattr(origin_invoice, "l10n_cr_numero_consecutivo", False) if origin_invoice else False)
            or (origin_invoice.name if origin_invoice else False)
            or (getattr(origin_invoice, "payment_reference", False) if origin_invoice else False)
            or (getattr(origin_invoice, "ref", False) if origin_invoice else False)
        )
        if not reference_number:
            return {}

        reference_date = (
            (origin_order.date_order.date() if origin_order and origin_order.date_order else False)
            or (origin_invoice.invoice_date if origin_invoice else False)
            or fields.Date.context_today(self)
        )
        reference_code = (
            (getattr(origin_order, "fp_reference_code", False) if origin_order else False)
            or (getattr(origin_invoice, "fp_reference_code", False) if origin_invoice else False)
            or "01"
        )
        reference_reason = (
            (getattr(origin_order, "fp_reference_reason", False) if origin_order else False)
            or (getattr(origin_invoice, "fp_reference_reason", False) if origin_invoice else False)
            or _("Devolución de mercadería")
        )
        values = {}

        for field_name in (
            "fp_reference_document_type",
            "fp_reference_doc_type",
            "reference_document_type",
            "l10n_cr_reference_document_type",
        ):
            if field_name in move_fields:
                values[field_name] = reference_doc_type

        for field_name in (
            "fp_reference_document_code",
            "fp_reference_code",
            "reference_document_code",
            "reference_code",
            "l10n_cr_reference_code",
        ):
            if field_name in move_fields:
                values[field_name] = reference_code

        for field_name in (
            "fp_reference_document_number",
            "fp_reference_number",
            "reference_document_number",
            "reference_number",
            "reversed_entry_number",
            "l10n_cr_reference_document_number",
        ):
            if field_name in move_fields:
                values[field_name] = reference_number

        for field_name in (
            "fp_reference_issue_date",
            "fp_reference_document_date",
            "fp_reference_date",
            "reference_document_date",
            "reference_date",
            "reversed_entry_date",
            "l10n_cr_reference_issue_date",
        ):
            if field_name in move_fields:
                values[field_name] = reference_date

        for field_name in (
            "fp_reference_reason",
            "reference_reason",
            "l10n_cr_reference_reason",
        ):
            if field_name in move_fields:
                values[field_name] = reference_reason

        return values

    def _cr_get_refund_reference_data(self):
        """Return normalized FE reference data for refund orders."""
        self.ensure_one()
        if self.amount_total >= 0:
            return {}

        origin_order = self._cr_get_origin_order_for_refund()
        origin_invoice = self._cr_get_origin_invoice_for_refund()

        origin_doc_type = (origin_order.cr_fe_document_type if origin_order else False) or (
            "fe" if origin_invoice else False
        )
        reference_doc_type = {
            "fe": "01",  # Factura Electrónica
            "te": "04",  # Tiquete Electrónico
            "nc": "03",  # Nota de Crédito
        }.get(origin_doc_type, "01")

        reference_number = (
            (origin_order.cr_fe_clave if origin_order else False)
            or (origin_order.cr_fe_consecutivo if origin_order else False)
            or (getattr(origin_invoice, "l10n_cr_clave", False) if origin_invoice else False)
            or (getattr(origin_invoice, "l10n_cr_numero_consecutivo", False) if origin_invoice else False)
            or (origin_invoice.name if origin_invoice else False)
            or (getattr(origin_invoice, "payment_reference", False) if origin_invoice else False)
            or (getattr(origin_invoice, "ref", False) if origin_invoice else False)
        )
        if not reference_number:
            return {}

        reference_date = (
            (origin_order.date_order.date() if origin_order and origin_order.date_order else False)
            or (origin_invoice.invoice_date if origin_invoice else False)
            or fields.Date.context_today(self)
        )
        reference_code = (
            (getattr(origin_order, "fp_reference_code", False) if origin_order else False)
            or (getattr(origin_invoice, "fp_reference_code", False) if origin_invoice else False)
            or "01"
        )
        reference_reason = (
            (getattr(origin_order, "fp_reference_reason", False) if origin_order else False)
            or (getattr(origin_invoice, "fp_reference_reason", False) if origin_invoice else False)
            or _("Devolución de mercadería")
        )
        return {
            "document_type": reference_doc_type,
            "number": reference_number,
            "issue_date": reference_date,
            "code": reference_code,
            "reason": reference_reason,
        }

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
        self._cr_sync_last_consecutivo_in_einvoice_config(doc_type, consecutivo)
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

        reference_data = {}
        if (document_type or "").lower() == "nc" or self.amount_total < 0:
            reference_data = self._cr_get_refund_reference_data()

        reference_issue_date = reference_data.get("issue_date")
        if reference_issue_date:
            reference_issue_date = fields.Date.to_string(reference_issue_date)

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
            # Compatibilidad amplia con diferentes implementaciones del servicio FE:
            # algunas esperan estructura anidada y otras campos planos.
            "reference": {
                "document_type": reference_data.get("document_type"),
                "number": reference_data.get("number"),
                "issue_date": reference_issue_date,
                "code": reference_data.get("code"),
                "reason": reference_data.get("reason"),
            },
            "reference_document_type": reference_data.get("document_type"),
            "reference_document_number": reference_data.get("number"),
            "reference_issue_date": reference_issue_date,
            "reference_code": reference_data.get("code"),
            "reference_reason": reference_data.get("reason"),
            "fp_reference_document_type": reference_data.get("document_type"),
            "fp_reference_document_number": reference_data.get("number"),
            "fp_reference_issue_date": reference_issue_date,
            "fp_reference_code": reference_data.get("code"),
            "fp_reference_reason": reference_data.get("reason"),
            "lines": lines,
        }

    
    # --- FE backend methods (called via _cr_call_service_method) ---

    def build_pos_xml_from_order(self, order_id, *, consecutivo, idempotency_key, clave, document_type, payload=None, **kwargs):
        """Build and sign TE/FE XML for a POS order and store it as attachment on the order."""
        order = self.browse(order_id)
        order.ensure_one()
        if order.invoice_status == "invoiced":
            return {"ok": False, "reason": "order_invoiced"}

        move = order._cr_build_virtual_move(document_type=document_type, consecutivo=consecutivo, clave=clave)
        xml_text = move._fp_generate_invoice_xml(clave=clave)
        signed_xml_text = move._fp_sign_xml(xml_text)

        xml_bytes = signed_xml_text.encode("utf-8")
        digest = hashlib.sha256(xml_bytes).hexdigest()
        doc_prefix = {
            "te": "TE",
            "fe": "FE",
            "nc": "NC",
        }.get((document_type or order.cr_fe_document_type or order._cr_get_pos_document_type() or "").lower(), "DOC")
        attachment = order.env["ir.attachment"].create(
            {
                "name": f"{doc_prefix}-{consecutivo}-firmado.xml",
                "type": "binary",
                "datas": base64.b64encode(xml_bytes),
                "res_model": "pos.order",
                "res_id": order.id,
                "mimetype": "application/xml",
            }
        )
        order.write(
            {
                "cr_fe_xml_attachment_id": attachment.id,
                "cr_fe_error_code": False,
                "cr_fe_last_error": False,
            }
        )
        return {"ok": True, "xml_attachment_id": attachment.id, "digest": digest}

    def send_to_hacienda(self, order_id, *, document_type=None, idempotency_key=None, company_id=None, **kwargs):
        """Send the already-signed POS XML to Hacienda (Recepción v4.4)."""
        order = self.browse(order_id)
        order.ensure_one()
        if order.invoice_status == "invoiced":
            return {"ok": False, "status": "not_applicable", "reason": "order_invoiced"}
        if not order.cr_fe_xml_attachment_id or not order.cr_fe_xml_attachment_id.datas:
            order._cr_prepare_te_document()

        move = order._cr_build_virtual_move(
            document_type=document_type or order.cr_fe_document_type or order._cr_get_pos_document_type(),
            consecutivo=order.cr_fe_consecutivo,
            clave=order.cr_fe_clave,
        )
        move.fp_xml_attachment_id = order.cr_fe_xml_attachment_id
        move.fp_external_id = order.cr_fe_clave

        company = order.company_id
        payload = move._fp_build_hacienda_payload()
        token = move._fp_get_hacienda_access_token()
        move._fp_call_api(
            endpoint=move._fp_get_hacienda_recepcion_endpoint(),
            payload=payload,
            timeout=company.fp_api_timeout,
            token=token,
            base_url=company.fp_hacienda_api_base_url,
            method="POST",
        )
        return {"ok": True, "status": "sent"}

    def consult_status(self, order_id, *, idempotency_key=None, **kwargs):
        """Consult Hacienda status for a POS document and store response XML on the POS order."""
        order = self.browse(order_id)
        order.ensure_one()
        if order.invoice_status == "invoiced":
            return {"ok": False, "status": "not_applicable", "reason": "order_invoiced"}
        if not order.cr_fe_clave:
            return {"ok": False, "status": "error", "reason": "missing_clave"}

        move = order._cr_build_virtual_move(
            document_type=order.cr_fe_document_type or order._cr_get_pos_document_type(),
            consecutivo=order.cr_fe_consecutivo,
            clave=order.cr_fe_clave,
        )
        move.fp_external_id = order.cr_fe_clave
        token = move._fp_get_hacienda_access_token()
        response_data = move._fp_call_api(
            endpoint=move._fp_get_hacienda_recepcion_endpoint(clave=order.cr_fe_clave),
            payload=None,
            timeout=order.company_id.fp_api_timeout,
            token=token,
            base_url=order.company_id.fp_hacienda_api_base_url,
            method="GET",
            params={"emisor": "".join(ch for ch in (order.company_id.vat or "") if ch.isdigit())},
        )

        response_attachment_id = order._cr_store_hacienda_response_attachment(
            response_data,
            clave=order.cr_fe_clave,
            consecutivo=order.cr_fe_consecutivo,
        )

        status = (response_data.get("ind-estado") or "").lower()
        normalized = order._cr_normalize_hacienda_status(status, default_status=False)
        return {"ok": True, "status": normalized, "response_attachment_id": response_attachment_id}

    # --- Helpers ---

    def _cr_get_general_customer_partner(self):
        """Return the shared 'Cliente general' partner used for TE when no customer is set.

        Requirements:
        - No identification
        - No address (province/canton/district/neighborhood/street)
        - No contact nodes (phone/email)
        """
        self.ensure_one()
        Partner = self.env["res.partner"].sudo()
        partner = Partner.search([("name", "=", "Cliente general"), ("company_id", "=", False)], limit=1)

        clean_vals = {
            "name": "Cliente general",
            "company_id": False,
            "vat": False,
            "phone": False,
            "mobile": False,
            "email": False,
            "street": False,
            "street2": False,
            "zip": False,
            "city": False,
            "state_id": False,
            "country_id": False,
            "fp_identification_type": False,
            "fp_province_id": False,
            "fp_canton_id": False,
            "fp_district_id": False,
            "fp_neighborhood_id": False,
            "fp_province_code": False,
            "fp_canton_code": False,
            "fp_district_code": False,
            "fp_neighborhood_code": False,
        }
        # Compatibility with databases/versions where some partner fields are not available.
        available_fields = set(Partner._fields)
        clean_vals = {key: value for key, value in clean_vals.items() if key in available_fields}

        if partner:
            # Ensure it stays "clean" for TE payloads.
            partner.write(clean_vals)
            return partner

        return Partner.create(clean_vals)

    def _cr_build_virtual_move(self, *, document_type, consecutivo, clave):
        """Build a non-persisted account.move that reuses l10n_cr_einvoice XML generator for POS data."""
        self.ensure_one()
        company = self.company_id
        journal = self.env["account.journal"].with_company(company).search(
            [("type", "=", "sale"), ("company_id", "=", company.id)], limit=1
        )
        line_commands = []
        for line in self.lines:
            line_commands.append(
                (
                    0,
                    0,
                    {
                        "product_id": line.product_id.id,
                        "name": line.full_product_name or line.product_id.display_name,
                        "quantity": line.qty,
                        "price_unit": line.price_unit,
                        "discount": line.discount,
                        "tax_ids": [(6, 0, line.tax_ids_after_fiscal_position.ids)],
                        "product_uom_id": line.product_uom_id.id if line.product_uom_id else False,
                    },
                )
            )

        is_credit_note = (document_type or "").lower() == "nc" or self.amount_total < 0
        move_vals = {
            "move_type": "out_refund" if is_credit_note else "out_invoice",
            "company_id": company.id,
            "journal_id": journal.id if journal else False,
            "partner_id": (self.partner_id.id if self.partner_id else self._cr_get_general_customer_partner().id),
            "currency_id": self.pricelist_id.currency_id.id,
            "invoice_date": (self.date_order.date() if self.date_order else fields.Date.context_today(self)),
            "invoice_line_ids": line_commands,
            "fp_is_electronic_invoice": True,
            "fp_document_type": (document_type or "te").upper(),
            "fp_sale_condition": self.fp_sale_condition,
            "fp_payment_method": self.fp_payment_method,
            "fp_economic_activity_id": self.fp_economic_activity_id.id if self.fp_economic_activity_id else False,
            "fp_consecutive_number": consecutivo,
        }
        if is_credit_note:
            move_vals.update(self._cr_build_refund_reference_values())
            origin_invoice = self._cr_get_origin_invoice_for_refund()
            if origin_invoice and "reversed_entry_id" in self.env["account.move"]._fields:
                move_vals["reversed_entry_id"] = origin_invoice.id
        move = self.env["account.move"].with_company(company).new(move_vals)
        # Asegura cálculo de totales para XML.
        move._compute_amount()
        return move

    def _cr_store_hacienda_response_attachment(self, response_data, *, clave, consecutivo=None):
        self.ensure_one()
        xml_keys = ["respuesta-xml", "respuestaXml", "xmlRespuesta", "xml"]
        xml_payload = next((response_data.get(key) for key in xml_keys if response_data.get(key)), None)
        if not xml_payload:
            return False

        if str(xml_payload).lstrip().startswith("<"):
            xml_text = xml_payload
        else:
            try:
                xml_text = base64.b64decode(xml_payload).decode("utf-8")
            except Exception:  # noqa: BLE001
                xml_text = str(xml_payload)

        doc_prefix = {
            "te": "TE",
            "fe": "FE",
            "nc": "NC",
        }.get((self.cr_fe_document_type or self._cr_get_pos_document_type() or "").lower(), "DOC")
        file_consecutivo = consecutivo or self.cr_fe_consecutivo or clave
        attachment = self.env["ir.attachment"].create(
            {
                "name": f"{doc_prefix}-{file_consecutivo}-respuesta-hacienda.xml",
                "type": "binary",
                "datas": base64.b64encode(xml_text.encode("utf-8")),
                "res_model": "pos.order",
                "res_id": self.id,
                "mimetype": "application/xml",
            }
        )
        self.cr_fe_response_attachment_id = attachment.id
        return attachment.id

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
            try:
                with self.env.cr.savepoint():
                    order._cr_send_pending_te_to_hacienda()
            except SerializationFailure:
                self._logger.warning(
                    "Skipping POS TE send for order %s due to concurrent update; it will retry in next cron run.",
                    order.id,
                )
        return True

    @api.model
    def _cron_cr_pos_check_pending_te_status(self, limit=50):
        for order, _target in self._cr_get_pending_status_ticket_targets(limit=limit):
            try:
                with self.env.cr.savepoint():
                    order._cr_check_pending_te_status()
            except SerializationFailure:
                self._logger.warning(
                    "Skipping POS TE status check for order %s due to concurrent update; it will retry in next cron run.",
                    order.id,
                )
        return True
