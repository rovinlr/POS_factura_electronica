import logging
import base64
import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from datetime import timedelta, datetime, date, time
from lxml import etree
from markupsafe import Markup, escape

from psycopg2 import IntegrityError
from psycopg2.errors import InFailedSqlTransaction, LockNotAvailable, SerializationFailure

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_is_zero


class PosOrder(models.Model):
    _inherit = "pos.order"

    _logger = logging.getLogger(__name__)

    _CR_INVOICE_MOVE_TYPES = ("out_invoice", "out_refund")
    _CR_FINAL_STATES = ("accepted", "rejected", "not_applicable")

    cr_ticket_move_id = fields.Many2one("account.move", string="Movimiento FE Tiquete", copy=False, index=True)
    cr_other_charges_json = fields.Text(
        string="Otros cargos FE (JSON)",
        copy=False,
        help="JSON canónico con la colección de Otros Cargos para FE CR v4.4.",
    )
    cr_other_charges_amount = fields.Monetary(
        string="Otros cargos",
        compute="_compute_cr_other_charges_amount",
        currency_field="currency_id",
        store=False,
    )
    cr_fe_document_type = fields.Selection(
        [("te", "Tiquete Electrónico"), ("fe", "Factura Electrónica"), ("nc", "Nota de Crédito")],
        string="Tipo documento FE",
        compute="_compute_cr_fe_document_type",
        store=True,
        tracking=True,
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
        tracking=True,
    )
    cr_fe_error_code = fields.Char(string="Código de error FE", copy=False, tracking=True)
    cr_fe_clave = fields.Char(string="Clave FE", copy=False, tracking=True)
    cr_fe_consecutivo = fields.Char(string="Consecutivo FE", copy=False, tracking=True)
    cr_fe_idempotency_key = fields.Char(string="Clave de idempotencia FE", copy=False, index=True)
    cr_fe_xml_attachment_id = fields.Many2one("ir.attachment", string="XML documento", copy=False)
    cr_fe_response_attachment_id = fields.Many2one("ir.attachment", string="XML respuesta MH", copy=False)
    cr_fe_pdf_attachment_id = fields.Many2one("ir.attachment", string="PDF comprobante", copy=False)
    cr_fe_attachment_ids = fields.Many2many("ir.attachment", string="Adjuntos FE", compute="_compute_cr_fe_attachment_ids")
    cr_fe_retry_count = fields.Integer(string="Reintentos FE", default=0, copy=False)
    cr_fe_next_try = fields.Datetime(string="Próximo intento FE", copy=False)
    cr_fe_last_error = fields.Text(string="Último error FE", copy=False)
    cr_fe_last_send_date = fields.Datetime(string="Último envío FE", copy=False)
    cr_fe_email_sent = fields.Boolean(string="Correo FE enviado", default=False, copy=False, tracking=True)
    cr_fe_email_sent_date = fields.Datetime(string="Fecha envío correo FE", copy=False, tracking=True)
    cr_fe_email_error = fields.Text(string="Error envío correo FE", copy=False)
    cr_fe_reference_document_type = fields.Char(string="Tipo documento referencia FE", copy=False)
    cr_fe_reference_document_number = fields.Char(string="Número documento referencia FE", copy=False)
    cr_fe_reference_issue_date = fields.Date(string="Fecha emisión referencia FE", copy=False)
    cr_fe_reference_code = fields.Char(string="Código referencia FE", copy=False)
    cr_fe_reference_reason = fields.Char(string="Razón referencia FE", copy=False)
    cr_receipt_html = fields.Text(string="HTML Tiquete POS", copy=False)
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
    cr_tax_rate_display = fields.Char(
        string="Impuesto (%)",
        compute="_compute_cr_tax_report_amounts",
        store=False,
    )
    cr_taxable_amount = fields.Monetary(
        string="Gravado",
        compute="_compute_cr_tax_report_amounts",
        currency_field="currency_id",
        store=False,
    )
    cr_taxable_amount_1 = fields.Monetary(
        string="Gravado 1%",
        compute="_compute_cr_tax_report_amounts",
        currency_field="currency_id",
        store=False,
    )
    cr_taxable_amount_2 = fields.Monetary(
        string="Gravado 2%",
        compute="_compute_cr_tax_report_amounts",
        currency_field="currency_id",
        store=False,
    )
    cr_taxable_amount_4 = fields.Monetary(
        string="Gravado 4%",
        compute="_compute_cr_tax_report_amounts",
        currency_field="currency_id",
        store=False,
    )
    cr_taxable_amount_13 = fields.Monetary(
        string="Gravado 13%",
        compute="_compute_cr_tax_report_amounts",
        currency_field="currency_id",
        store=False,
    )
    cr_exempt_amount = fields.Monetary(
        string="Exento",
        compute="_compute_cr_tax_report_amounts",
        currency_field="currency_id",
        store=False,
    )
    cr_nonsubject_amount = fields.Monetary(
        string="No sujeto",
        compute="_compute_cr_tax_report_amounts",
        currency_field="currency_id",
        store=False,
    )
    cr_exonerated_amount = fields.Monetary(
        string="Exonerado",
        compute="_compute_cr_tax_report_amounts",
        currency_field="currency_id",
        store=False,
    )

    _cr_pos_einvoice_idempotency_key_unique = models.Constraint(
        "unique(company_id, cr_fe_idempotency_key)",
        "La clave de idempotencia FE debe ser única por compañía.",
    )

    @api.depends("account_move", "state", "amount_total", "lines.refunded_orderline_id")
    def _compute_cr_fe_document_type(self):
        for order in self:
            invoice = order._cr_get_real_invoice_move()
            if invoice:
                order.cr_fe_document_type = "nc" if invoice.move_type == "out_refund" else "fe"
            elif order._cr_is_refund_order_candidate():
                # Preconfigure NC as soon as a refund order exists (before payment)
                # so FE references can be prepared deterministically.
                order.cr_fe_document_type = "nc"
            elif order._cr_is_marked_for_invoicing():
                order.cr_fe_document_type = "fe"
            elif order.state in ("paid", "done", "invoiced"):
                order.cr_fe_document_type = order._cr_get_pos_document_type()
            else:
                order.cr_fe_document_type = False

    @api.depends(
        "cr_other_charges_json",
        "lines.price_subtotal",
        "lines.product_id",
        "config_id",
        "session_id.config_id",
    )
    def _compute_cr_other_charges_amount(self):
        for order in self:
            charges = order._cr_get_other_charges_payload()
            order.cr_other_charges_amount = sum(float(charge.get("amount") or 0.0) for charge in charges)

    @api.depends("lines.price_subtotal", "lines.tax_ids_after_fiscal_position")
    def _compute_cr_tax_report_amounts(self):
        tracked_rates = (1.0, 2.0, 4.0, 13.0)
        for order in self:
            taxable = 0.0
            exempt = 0.0
            nonsubject = 0.0
            exonerated = 0.0
            rates = set()
            taxable_by_rate = {rate: 0.0 for rate in tracked_rates}
            for line in order.lines:
                subtotal = line.price_subtotal or 0.0
                taxes = line.tax_ids_after_fiscal_position
                code_taxes = {
                    str(code): taxes.filtered(lambda tax: getattr(tax, "fp_tax_rate_code_iva", False) == code)
                    for code in ("01", "08", "10")
                }
                has_nonsubject = bool(code_taxes["01"])
                has_exempt = bool(code_taxes["10"])
                has_code_08 = bool(code_taxes["08"])
                code_08_positive = code_taxes["08"].filtered(lambda tax: (tax.amount or 0.0) > 0)
                code_08_zero = code_taxes["08"] - code_08_positive

                if has_nonsubject:
                    nonsubject += subtotal
                    continue

                if has_exempt:
                    exempt += subtotal
                    continue

                if has_code_08 and code_08_zero and not code_08_positive:
                    # Exonerado: Hacienda usa código 08 (13%) con monto de impuesto en cero.
                    exonerated += subtotal
                    rates.add("13%")
                    continue

                positive_taxes = taxes.filtered(lambda tax: (tax.amount or 0.0) > 0)
                if positive_taxes:
                    taxable += subtotal
                    for tax in positive_taxes:
                        rates.add(f"{tax.amount:g}%")
                    line_rates = {
                        float(int(tax.amount) if float(tax.amount).is_integer() else tax.amount)
                        for tax in positive_taxes
                        if float(tax.amount) in tracked_rates
                    }
                    if line_rates:
                        allocation = subtotal / len(line_rates)
                        for rate in line_rates:
                            taxable_by_rate[rate] += allocation
                else:
                    exempt += subtotal
            order.cr_taxable_amount = taxable
            order.cr_taxable_amount_1 = taxable_by_rate[1.0]
            order.cr_taxable_amount_2 = taxable_by_rate[2.0]
            order.cr_taxable_amount_4 = taxable_by_rate[4.0]
            order.cr_taxable_amount_13 = taxable_by_rate[13.0]
            order.cr_exempt_amount = exempt
            order.cr_nonsubject_amount = nonsubject
            order.cr_exonerated_amount = exonerated
            if exonerated and float_is_zero(taxable_by_rate[13.0], precision_rounding=order.currency_id.rounding):
                rates.add("13%")
            order.cr_tax_rate_display = ", ".join(sorted(rates)) if rates else "0%"

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

    def _cr_call_service_method(self, method_names, *args, prefer_local=False, **kwargs):
        """Call first available FE backend method from service or pos.order."""
        self.ensure_one()
        tried_backends = []
        backends = []
        service = self._cr_service()
        if prefer_local:
            backends.append(("pos.order", self))
            if service:
                backends.append(("l10n_cr.einvoice.service", service))
        else:
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

    def _cr_get_partner_phone(self, partner):
        """Return partner phone with safe fallback for optional `mobile` field."""
        if not partner:
            return False
        if partner.phone:
            return partner.phone
        if "mobile" in partner._fields:
            return partner.mobile
        return False

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

    @api.depends(
        "cr_fe_document_type",
        "to_invoice",
        "account_move",
        "account_move.move_type",
        "account_move.state",
        "config_id.fp_economic_activity_id",
        "payment_ids.amount",
        "payment_ids.payment_method_id",
        "lines.refunded_orderline_id",
        "amount_total",
    )
    def _compute_fp_pos_fe_fields(self):
        for order in self:
            doc_type = (order.cr_fe_document_type or order._cr_get_pos_document_type() or "te").upper()
            if doc_type not in {"TE", "FE", "NC"}:
                doc_type = "TE"
            method = order._cr_get_primary_payment_method() if order.payment_ids else self.env["pos.payment.method"]
            order.fp_document_type = doc_type
            order.fp_sale_condition = method.fp_sale_condition if method else False
            order.fp_payment_method = method.fp_payment_method if method else False
            order.fp_economic_activity_id = order.config_id.fp_economic_activity_id

    def _cr_get_manual_reference_data(self):
        """Manual NC reference captured on pos.order (backend payment wizard/UI)."""
        self.ensure_one()
        reference_data = {
            "document_type": (self.cr_fe_reference_document_type or "").strip() or False,
            "number": (self.cr_fe_reference_document_number or "").strip() or False,
            "issue_date": fields.Date.to_date(self.cr_fe_reference_issue_date) if self.cr_fe_reference_issue_date else False,
            "code": (self.cr_fe_reference_code or "").strip() or False,
            "reason": (self.cr_fe_reference_reason or "").strip() or False,
        }

        if not self.id:
            return reference_data

        # Force DB read to avoid stale cache when payment wizard/UI writes the
        # reference in a parallel env and the order is sent immediately after.
        # Also merge with in-memory values so same-transaction updates are never
        # lost by a strict DB-only read (prevents false `reference_pending`).
        db_values = self.sudo().with_context(prefetch_fields=False).read(
            [
                "cr_fe_reference_document_type",
                "cr_fe_reference_document_number",
                "cr_fe_reference_issue_date",
                "cr_fe_reference_code",
                "cr_fe_reference_reason",
            ],
            load=False,
        )[0]

        db_reference_data = {
            "document_type": (db_values.get("cr_fe_reference_document_type") or "").strip() or False,
            "number": (db_values.get("cr_fe_reference_document_number") or "").strip() or False,
            "issue_date": fields.Date.to_date(db_values.get("cr_fe_reference_issue_date"))
            if db_values.get("cr_fe_reference_issue_date")
            else False,
            "code": (db_values.get("cr_fe_reference_code") or "").strip() or False,
            "reason": (db_values.get("cr_fe_reference_reason") or "").strip() or False,
        }

        merged_reference_data = {}
        for key in ("document_type", "number", "issue_date", "code", "reason"):
            merged_reference_data[key] = reference_data.get(key) or db_reference_data.get(key) or False
        return merged_reference_data

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

    def _cr_is_marked_for_invoicing(self):
        """Return True when FE must be handled by account.move, not by POS TE flow.

        In Odoo POS, the authoritative user intent is the boolean `to_invoice`
        toggled in the frontend ("Facturar"). `invoice_status` can vary by
        implementation/customizations and must not disable TE/NC by itself when
        `to_invoice` is explicitly False.
        """
        self.ensure_one()
        if "to_invoice" in self._fields:
            return bool(self.to_invoice)
        invoice_status = (self.invoice_status or "").strip().lower()
        return invoice_status in ("to invoice", "to_invoice", "invoiced")

    def _cr_requires_account_move_flow(self):
        """Return True when this order must delegate FE lifecycle to account.move.

        Enterprise decision (CR FE bridge): for POS orders marked as "Facturar",
        FE is emitted from ``pos.order`` (document type FE) and no customer
        invoice is generated automatically from POS.
        """
        self.ensure_one()
        return False

    def _cr_should_emit_ticket(self):
        self.ensure_one()
        if self.state not in ("paid", "done", "invoiced"):
            return False
        if self._cr_requires_account_move_flow():
            return False
        if self._cr_has_real_invoice_move():
            return False
        return self.cr_fe_status not in self._CR_FINAL_STATES

    def _cr_get_pos_document_type(self):
        self.ensure_one()
        # Refunds must always be emitted as NC, even when POS "Facturar" is enabled.
        # Some FE refund flows keep `to_invoice=True` to preserve accounting intent;
        # prioritizing refunds here prevents accidental FE emission for devoluciones.
        if self._cr_is_refund_order_candidate():
            return "nc"
        if self._cr_is_marked_for_invoicing():
            return "fe"
        return "te"

    def _cr_is_refund_order_candidate(self):
        """Detect refund orders reliably, even before they are paid."""
        self.ensure_one()
        if self.amount_total < 0:
            return True
        if self.lines.filtered("refunded_orderline_id"):
            return True

        move = self._cr_get_real_invoice_move()
        return bool(move and move.move_type == "out_refund")

    def _cr_build_idempotency_key(self):
        self.ensure_one()
        return f"POS-{self.company_id.id}-{self.config_id.id}-{self.name or self.pos_reference or self.id}"

    def _cr_get_or_create_idempotency_key(self):
        """Return a stable FE idempotency key and persist it when missing.

        NC/Refund flows can call FE backends from different entry points (prepare,
        send or status check). Persisting the key as soon as we need FE interaction
        keeps retries deterministic and avoids duplicate emission under concurrency.
        """
        self.ensure_one()
        if self.cr_fe_idempotency_key:
            return self.cr_fe_idempotency_key

        key = self._cr_build_idempotency_key()
        try:
            self.write({"cr_fe_idempotency_key": key})
        except IntegrityError as error:
            self.env.cr.rollback()
            raise UserError(_("La llave de idempotencia ya fue utilizada para esta compañía.")) from error
        return key

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
        self._cr_lock_consecutive_counter(document_type)

        service_last = self._cr_get_current_last_consecutive_number(document_type)
        next_from_service = (service_last + 1) if service_last is not None else None

        sequence = self._cr_get_or_create_sequence(document_type or self.cr_fe_document_type or "te")
        sequence_raw = sequence.next_by_id()
        sequence_number = self._cr_extract_last_consecutive_number(sequence_raw)

        if sequence_number is None and next_from_service is None:
            return "0000000001"
        if sequence_number is None:
            target_next = next_from_service
        elif next_from_service is None:
            target_next = sequence_number
        else:
            target_next = max(sequence_number, next_from_service)

        # Keep local sequence aligned with authoritative FE counters when those
        # are ahead of the internal sequence value.
        if sequence_number is not None and target_next > sequence_number:
            sequence.sudo().write({"number_next": target_next + 1})

        return str(target_next).zfill(10)

    def _cr_lock_consecutive_counter(self, document_type):
        """Serialize consecutive assignment per company/document type."""
        self.ensure_one()
        doc_code = (document_type or self.cr_fe_document_type or "te").upper()
        lock_key = f"cr_pos_fe_consecutive:{self.company_id.id}:{doc_code}"
        self.env.cr.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (lock_key,))

    def _cr_get_next_consecutivo_from_service(self, document_type):
        """Read next consecutive from external FE service when supported."""
        self.ensure_one()
        service = self._cr_service()
        doc_code = (document_type or self.cr_fe_document_type or "te").upper()
        if not service:
            return None

        if service:
            for method_name in (
                "get_next_consecutivo",
                "get_next_consecutive",
                "get_next_consecutivo_by_document_type",
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
                    numeric_value = self._cr_extract_last_consecutive_number(value)
                    if numeric_value is not None:
                        return numeric_value
        return None

    def _cr_sync_last_consecutivo_in_einvoice_config(self, document_type, consecutivo):
        """Best-effort sync with FE configuration's "último número" counters."""
        self.ensure_one()
        target_number = self._cr_extract_last_consecutive_number(consecutivo)
        if target_number is None:
            return False

        current_number = self._cr_get_current_last_consecutive_number(document_type)
        if current_number is not None and target_number <= current_number:
            self._logger.info(
                "Skip FE consecutive rollback for %s (company_id=%s): target=%s current=%s",
                (document_type or self.cr_fe_document_type or "te").upper(),
                self.company_id.id,
                target_number,
                current_number,
            )
            return True

        last_number = str(target_number)

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

    def _cr_extract_last_consecutive_number(self, consecutivo):
        self.ensure_one()
        if not consecutivo:
            return None
        digits = "".join(char for char in str(consecutivo) if char.isdigit())
        return int(digits[-10:] or "0")

    def _cr_get_current_last_consecutive_number(self, document_type):
        self.ensure_one()
        doc_code = (document_type or self.cr_fe_document_type or "te").upper()
        service = self._cr_service()
        if service:
            for method_name in ("get_last_consecutivo_by_document_type", "get_last_consecutive_by_document_type"):
                method = getattr(service, method_name, False)
                if not method:
                    continue
                try:
                    result = method(company_id=self.company_id.id, document_type=doc_code)
                except TypeError:
                    result = method(self.company_id.id, doc_code)
                value = result.get("consecutivo") if isinstance(result, dict) else result
                number = self._cr_extract_last_consecutive_number(value)
                if number is not None:
                    return number

        company = self.company_id.sudo()
        fallback_fields = {
            "TE": ("fp_consecutive_te", "fp_consecutive_fe"),
            "FE": ("fp_consecutive_fe",),
            "NC": ("fp_consecutive_nc",),
        }
        for field_name in fallback_fields.get(doc_code, ("fp_consecutive_fe",)):
            if field_name in company._fields:
                number = self._cr_extract_last_consecutive_number(company[field_name])
                if number is not None:
                    return number

        return None

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

    def _cr_has_complete_refund_reference_data(self):
        """Return True when a credit note has all mandatory FE reference fields."""
        self.ensure_one()
        if not self._cr_is_credit_note_order():
            return True

        reference_data = self._cr_get_refund_reference_data()
        required_fields = ("document_type", "number", "issue_date")
        return bool(reference_data and all(reference_data.get(field_name) for field_name in required_fields))

    def _cr_get_missing_refund_reference_fields(self):
        """Return missing FE reference labels for a refund in a deterministic order."""
        self.ensure_one()
        if not self._cr_is_credit_note_order():
            return []

        reference_data = self._cr_get_refund_reference_data()
        required_fields = (
            ("document_type", _("tipo")),
            ("number", _("número")),
            ("issue_date", _("fecha")),
        )
        return [label for key, label in required_fields if not (reference_data and reference_data.get(key))]

    def _cr_build_reference_pending_message(self):
        """Return a user-facing, actionable message for pending NC references."""
        self.ensure_one()
        missing = self._cr_get_missing_refund_reference_fields()
        base = _(
            "La nota de crédito se enviará cuando exista la referencia "
            "(tipo, número y fecha del documento original)."
        )
        if not missing:
            return base

        return _("%s Campos pendientes: %s.") % (base, ", ".join(missing))

    def _cr_should_delay_credit_note_xml(self):
        """Credit notes must wait for references before generating XML."""
        self.ensure_one()
        return self._cr_is_credit_note_order() and not self._cr_has_complete_refund_reference_data()


    def _cr_is_reference_pending_error(self, error=None):
        """Return True when a UserError relates to missing NC reference data.

        In some POS flows, reference data can be written in the same request
        after the FE trigger starts. We therefore check both current reference
        completeness and the exception message to decide whether to retry.
        """
        self.ensure_one()
        if not self._cr_is_credit_note_order():
            return False

        if not self._cr_has_complete_refund_reference_data():
            return True

        if not error:
            return False

        msg = str(error) or ""
        fragments = (
            "requiere información de referencia",
            "requires reference information",
            "requires reference data",
        )
        return any(fragment in msg for fragment in fragments)

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

    def action_cr_generate_pdf_attachment(self):
        self.ensure_one()
        pdf_content = self._cr_render_receipt_pdf_content()
        attachment = self._cr_upsert_receipt_pdf_attachment(pdf_content)
        if not attachment:
            raise UserError(_("No se pudo generar el PDF del comprobante para este pedido POS."))
        return {
            "type": "ir.actions.act_url",
            "url": f"/web/content/{attachment.id}?download=true",
            "target": "self",
        }

    def action_cr_resend_fe_email(self):
        for order in self:
            if not order._cr_get_customer_email():
                raise UserError(_("El cliente no tiene correo electrónico válido para reenviar el comprobante."))
            order.write({"cr_fe_email_sent": False, "cr_fe_email_error": False})
            if not order._cr_try_send_accepted_email():
                raise UserError(order.cr_fe_email_error or _("No se pudo enviar el correo del comprobante electrónico."))
        return True

    def _cr_fe_status_label(self, status):
        return dict(self._fields["cr_fe_status"].selection).get(status, status)

    def _cr_post_fe_event(self, title, body=None, attachments=None):
        self.ensure_one()
        safe_title = escape(title or "")
        safe_body = escape(body or "")
        html_body = Markup("<b>{}</b>{}").format(safe_title, Markup("<br/>{}").format(safe_body) if body else Markup(""))
        values = {"body": html_body}
        if attachments:
            values["attachment_ids"] = [(4, att.id) for att in attachments if att]
        try:
            self.message_post(**values)
        except Exception:  # noqa: BLE001
            # Do not block POS flow if chatter fails.
            return False
        return True

    def _cr_get_customer_email(self):
        self.ensure_one()
        partner = self.partner_id.commercial_partner_id if self.partner_id else self.env["res.partner"]
        email = (partner.email or "").strip().lower()
        return email if email and "@" in email else False

    def _cr_is_auto_email_enabled(self):
        self.ensure_one()
        return bool(self.config_id and self.config_id.cr_fe_enabled and self.config_id.cr_fe_auto_email_accepted_docs)

    def _cr_get_email_subject(self):
        self.ensure_one()
        doc_type = dict(self._fields["cr_fe_document_type"].selection).get(self.cr_fe_document_type, "Comprobante")
        identifier = self.cr_fe_consecutivo or self.name or self.pos_reference or str(self.id)
        return _("%(doc_type)s %(identifier)s aceptado por Hacienda") % {
            "doc_type": doc_type,
            "identifier": identifier,
        }

    def _cr_get_email_body_html(self):
        self.ensure_one()
        company = self.company_id
        identifier = self.cr_fe_consecutivo or self.name or self.pos_reference or str(self.id)
        return Markup(
            "<p>Estimado cliente,</p>"
            "<p>Le compartimos su comprobante electrónico <b>%(identifier)s</b>, aceptado por Hacienda.</p>"
            "<p>Adjuntamos XML y PDF para su respaldo fiscal.</p>"
            "<p>Saludos,<br/>%(company)s</p>"
        ) % {
            "identifier": escape(identifier),
            "company": escape(company.display_name or ""),
        }

    def _cr_get_pdf_report_action(self):
        self.ensure_one()
        # 1) Prefer POS-native PDF reports so customer receives ticket style
        # printed by POS/backoffice, while avoiding qweb-html-only actions.
        preferred_pos_xmlids = [
            "cr_pos_einvoice.action_report_pos_order_ticket_cr",  # deterministic POS ticket PDF for FE email flow
            "point_of_sale.report_invoice",  # legacy/community variants
            "point_of_sale.pos_ticket",  # POS receipt in newer versions
            "point_of_sale.action_report_pos_order",  # Odoo 19 variants
        ]
        for xmlid in preferred_pos_xmlids:
            report = self.env.ref(xmlid, raise_if_not_found=False)
            if report and report.model == "pos.order" and report.report_type in ("qweb-pdf", "qweb-html"):
                return report

        pos_report = self.env["ir.actions.report"].search(
            [("model", "=", "pos.order"), ("report_type", "in", ("qweb-pdf", "qweb-html"))],
            order="id asc",
            limit=1,
        )
        if pos_report:
            return pos_report

        # 2) Conservative fallback to account.move invoice reports.
        candidate_xmlids = ["account.report_invoice_with_payments", "account.account_invoices"]
        for xmlid in candidate_xmlids:
            report = self.env.ref(xmlid, raise_if_not_found=False)
            if report:
                return report
        return False

    def _cr_get_or_create_pdf_attachment(self):
        self.ensure_one()
        move = self._cr_get_real_invoice_move() or self.cr_ticket_move_id

        filename = f"{(self.cr_fe_consecutivo or self.name or (move and move.name) or f'POS-{self.id}').replace('/', '-')}.pdf"
        if (
            self.cr_fe_pdf_attachment_id
            and self.cr_fe_pdf_attachment_id.exists()
            and self.cr_fe_pdf_attachment_id.res_model == "pos.order"
            and self.cr_fe_pdf_attachment_id.res_id == self.id
            and self.cr_fe_pdf_attachment_id.mimetype == "application/pdf"
        ):
            return self.cr_fe_pdf_attachment_id

        existing = self.env["ir.attachment"].search(
            [
                ("res_model", "=", "pos.order"),
                ("res_id", "=", self.id),
                ("mimetype", "=", "application/pdf"),
                ("name", "=", filename),
            ],
            limit=1,
        )
        if existing:
            if self.id and self.cr_fe_pdf_attachment_id != existing:
                self.sudo().write({"cr_fe_pdf_attachment_id": existing.id})
            return existing

        report = self._cr_get_pdf_report_action()
        if not report:
            return self.env["ir.attachment"]

        if report.model == "pos.order":
            record_ids = self.ids
        elif move:
            record_ids = move.ids
        else:
            # No account.move exists for POS TE and selected report is invoice-only.
            # Avoid raising and let mail flow continue with XML attachments.
            return self.env["ir.attachment"]

        report_engine = self.env["ir.actions.report"].sudo()
        pdf_content = b""
        if report.report_name:
            pdf_content, _content_type = report_engine._render_qweb_pdf(report.report_name, res_ids=record_ids)
        if not pdf_content:
            return self.env["ir.attachment"]
        attachment = self.env["ir.attachment"].create(
            {
                "name": filename,
                "type": "binary",
                "datas": base64.b64encode(pdf_content),
                "res_model": "pos.order",
                "res_id": self.id,
                "mimetype": "application/pdf",
            }
        )
        if self.id and self.cr_fe_pdf_attachment_id != attachment:
            self.sudo().write({"cr_fe_pdf_attachment_id": attachment.id})
        return attachment

    def _cr_pdf_attachment_name(self):
        self.ensure_one()
        order_name = (self.name or self.pos_reference or f"POS-{self.id}").replace("/", "-")
        consecutivo = (self.cr_fe_consecutivo or "SIN_CONSECUTIVO").replace("/", "-")
        return f"Ticket_{order_name}_{consecutivo}.pdf"

    def _cr_get_existing_receipt_pdf_attachment(self):
        self.ensure_one()
        if (
            self.cr_fe_pdf_attachment_id
            and self.cr_fe_pdf_attachment_id.exists()
            and self.cr_fe_pdf_attachment_id.res_model == "pos.order"
            and self.cr_fe_pdf_attachment_id.res_id == self.id
            and self.cr_fe_pdf_attachment_id.mimetype == "application/pdf"
        ):
            return self.cr_fe_pdf_attachment_id
        return self.env["ir.attachment"].search(
            [
                ("res_model", "=", "pos.order"),
                ("res_id", "=", self.id),
                ("mimetype", "=", "application/pdf"),
                ("name", "like", f"Ticket_{(self.name or self.pos_reference or f'POS-{self.id}').replace('/', '-')}_%"),
            ],
            order="id desc",
            limit=1,
        )

    def _cr_wrap_receipt_html_for_pdf(self):
        self.ensure_one()
        html = (self.cr_receipt_html or "").strip()
        if not html:
            return False
        # Safety hardening: remove scripts and inline event handlers before wkhtmltopdf.
        html = re.sub(r"<script[\s\S]*?>[\s\S]*?</script>", "", html, flags=re.IGNORECASE)
        html = re.sub(r"\son[a-z]+\s*=\s*\"[^\"]*\"", "", html, flags=re.IGNORECASE)
        html = re.sub(r"\son[a-z]+\s*=\s*'[^']*'", "", html, flags=re.IGNORECASE)
        return (
            "<!doctype html><html><head><meta charset='utf-8'/>"
            "<style>body{font-family:Arial,Helvetica,sans-serif;font-size:12px;color:#111;} .pos-receipt{width:100%;}</style>"
            "</head><body>%s</body></html>"
        ) % html

    def _cr_render_receipt_pdf_content(self):
        self.ensure_one()
        # Enterprise-safe rendering strategy:
        # 1) Force deterministic QWeb PDF rendering for pos.order (mail attachment quality).
        # 2) Reuse existing attachment only if report rendering is unavailable.
        # 3) Keep POS HTML-to-PDF as last-resort compatibility fallback.
        report = self.env.ref("cr_pos_einvoice.action_report_pos_order_ticket_cr", raise_if_not_found=False)
        if report and report.report_name and report.model == "pos.order" and report.report_type == "qweb-pdf":
            report_engine = self.env["ir.actions.report"].sudo()
            pdf_content, _content_type = report_engine._render_qweb_pdf(report.report_name, res_ids=self.ids)
            if pdf_content:
                return pdf_content

        existing_attachment = self._cr_get_existing_receipt_pdf_attachment()
        if existing_attachment and existing_attachment.datas:
            return base64.b64decode(existing_attachment.datas)

        wrapped_html = self._cr_wrap_receipt_html_for_pdf()
        if wrapped_html:
            report_engine = self.env["ir.actions.report"].sudo()
            return report_engine._run_wkhtmltopdf(
                [wrapped_html],
                landscape=False,
                specific_paperformat_args={"margin_top": 6, "margin_bottom": 6, "margin_left": 4, "margin_right": 4},
            )

        return b""

    def _cr_upsert_receipt_pdf_attachment(self, pdf_content):
        self.ensure_one()
        if not pdf_content:
            return self.env["ir.attachment"]
        filename = self._cr_pdf_attachment_name()
        existing = self._cr_get_existing_receipt_pdf_attachment()
        values = {
            "name": filename,
            "type": "binary",
            "datas": base64.b64encode(pdf_content),
            "res_model": "pos.order",
            "res_id": self.id,
            "mimetype": "application/pdf",
        }
        if existing:
            existing.write(values)
            if self.id and self.cr_fe_pdf_attachment_id != existing:
                self.sudo().write({"cr_fe_pdf_attachment_id": existing.id})
            return existing
        attachment = self.env["ir.attachment"].create(values)
        if self.id and self.cr_fe_pdf_attachment_id != attachment:
            self.sudo().write({"cr_fe_pdf_attachment_id": attachment.id})
        return attachment

    @api.model
    def cr_pos_store_receipt_html(self, order_id, receipt_html):
        order = self.browse(order_id).exists()
        if not order:
            return {"ok": False}
        sanitized_html = (receipt_html or "").strip()
        order.sudo().write({"cr_receipt_html": sanitized_html[:5_000_000]})
        if order.cr_fe_status == "accepted":
            order.cr_pos_generate_receipt_pdf_if_accepted([order.id])
        return {"ok": True}

    @api.model
    def cr_pos_generate_receipt_pdf_if_accepted(self, order_ids):
        orders = self.browse(order_ids).exists()
        for order in orders:
            if order._cr_normalize_hacienda_status(order.cr_fe_status) != "accepted":
                continue
            try:
                pdf_content = order._cr_render_receipt_pdf_content()
                attachment = order._cr_upsert_receipt_pdf_attachment(pdf_content)
                if attachment and order.cr_receipt_html:
                    order.sudo().write({"cr_receipt_html": False})
            except Exception:  # noqa: BLE001
                order._logger.exception("Error generating accepted receipt PDF for POS order %s", order.id)
        return True

    def _cr_get_email_attachments(self):
        self.ensure_one()
        attachments = self.env["ir.attachment"]
        for attachment in (self.cr_fe_xml_attachment_id, self.cr_fe_response_attachment_id, self.cr_fe_pdf_attachment_id):
            if attachment:
                attachments |= attachment

        # Prefer the FE-accepted receipt PDF linked directly to pos.order.
        # If FE is accepted, force generation/upsert first so mail can include it.
        if self._cr_normalize_hacienda_status(self.cr_fe_status) == "accepted":
            self.cr_pos_generate_receipt_pdf_if_accepted([self.id])
        pdf_attachment = self._cr_get_existing_receipt_pdf_attachment()
        if not pdf_attachment:
            pdf_attachment = self._cr_get_or_create_pdf_attachment()
        if pdf_attachment:
            attachments |= pdf_attachment
        return attachments

    def _cr_should_send_accepted_email(self):
        self.ensure_one()
        return bool(
            self._cr_is_auto_email_enabled()
            and self.cr_fe_status == "accepted"
            and self.cr_fe_document_type in ("te", "fe", "nc")
            and not self.cr_fe_email_sent
            and self._cr_get_customer_email()
        )

    def _cr_acquire_email_send_lock(self):
        """Serialize FE email dispatch per POS order to avoid duplicate sends."""
        self.ensure_one()
        try:
            with self.env.cr.savepoint(flush=False):
                self.env.cr.execute("SELECT id FROM pos_order WHERE id = %s FOR UPDATE NOWAIT", (self.id,))
            return True
        except LockNotAvailable:
            self._logger.info(
                "Skipping FE email dispatch for POS order %s because another transaction holds the send lock.",
                self.id,
            )
            return False
        except (SerializationFailure, InFailedSqlTransaction) as error:
            self._logger.warning(
                "Concurrent transaction while acquiring FE email lock for POS order %s: %s",
                self.id,
                error,
            )
            return False

    def _cr_try_send_accepted_email(self):
        self.ensure_one()
        if not self._cr_acquire_email_send_lock():
            return False

        self.invalidate_recordset(["cr_fe_status", "cr_fe_email_sent", "cr_fe_email_error", "partner_id", "config_id"])
        if not self._cr_should_send_accepted_email():
            return False

        recipient = self._cr_get_customer_email()
        attachments = self._cr_get_email_attachments()
        existing_mail = self._cr_find_existing_sent_fe_email(recipient)
        if existing_mail:
            self._logger.info(
                "Skipping duplicate FE email for POS order %s; mail %s already exists in state %s.",
                self.id,
                existing_mail.id,
                existing_mail.state,
            )
            self._cr_mark_accepted_email_sent()
            return True
        if not attachments:
            self._cr_set_email_delivery_error(_("No se encontraron adjuntos XML/PDF para el envío por correo."))
            return False

        try:
            mail = (
                self.env["mail.mail"]
                .sudo()
                .create(
                    {
                        "subject": self._cr_get_email_subject(),
                        "email_to": recipient,
                        "body_html": self._cr_get_email_body_html(),
                        "auto_delete": False,
                        "model": "pos.order",
                        "res_id": self.id,
                        "attachment_ids": [(6, 0, attachments.ids)],
                    }
                )
            )
            mail.send()
            self._cr_mark_accepted_email_sent()
            self._cr_post_fe_event(
                title=_("Correo FE enviado"),
                body=_("Se envió TE/NC aceptado al cliente: %s") % recipient,
                attachments=attachments,
            )
            return True
        except (SerializationFailure, InFailedSqlTransaction) as error:
            self._logger.warning(
                "Concurrent transaction while sending FE email for POS order %s: %s",
                self.id,
                error,
            )
            return False
        except Exception as error:  # noqa: BLE001
            self._logger.exception("Error enviando correo FE para POS order %s", self.id)
            self._cr_set_email_delivery_error(str(error))
            return False

    def _cr_find_existing_sent_fe_email(self, recipient):
        self.ensure_one()
        return (
            self.env["mail.mail"]
            .sudo()
            .search(
                [
                    ("model", "=", "pos.order"),
                    ("res_id", "=", self.id),
                    ("email_to", "=", recipient),
                    ("state", "in", ["outgoing", "sent"]),
                ],
                order="id desc",
                limit=1,
            )
        )

    def _cr_mark_accepted_email_sent(self):
        self.ensure_one()
        values = {
            "cr_fe_email_sent": True,
            "cr_fe_email_sent_date": fields.Datetime.now(),
            "cr_fe_email_error": False,
        }
        for _attempt in range(3):
            try:
                with self.env.cr.savepoint(flush=False):
                    self.with_context(cr_fe_skip_email_delivery=True).write(values)
                return True
            except (SerializationFailure, InFailedSqlTransaction):
                self.invalidate_recordset(["cr_fe_email_sent", "cr_fe_email_sent_date", "cr_fe_email_error"])
        self._logger.warning(
            "Unable to persist FE email sent marker for POS order %s due to concurrent updates; "
            "next retry will reconcile state.",
            self.id,
        )
        return False

    def _cr_set_email_delivery_error(self, message):
        self.ensure_one()
        for _attempt in range(3):
            try:
                with self.env.cr.savepoint(flush=False):
                    self.with_context(cr_fe_skip_email_delivery=True).write({"cr_fe_email_error": message})
                return True
            except (SerializationFailure, InFailedSqlTransaction):
                self.invalidate_recordset(["cr_fe_email_error"])
        self._logger.warning(
            "Unable to persist FE email error for POS order %s due to concurrent updates.",
            self.id,
        )
        return False

    def write(self, vals):
        """Post FE milestones to chatter (generated/sent/accepted/rejected/error)."""
        tracked_fields = {"cr_fe_status", "cr_fe_xml_attachment_id", "cr_fe_response_attachment_id", "cr_fe_pdf_attachment_id"}
        needs_track = bool(tracked_fields.intersection(vals))
        old = {}
        if needs_track:
            for order in self:
                old[order.id] = {
                    "status": order.cr_fe_status,
                    "xml": order.cr_fe_xml_attachment_id.id if order.cr_fe_xml_attachment_id else False,
                    "resp": order.cr_fe_response_attachment_id.id if order.cr_fe_response_attachment_id else False,
                }

        res = super().write(vals)

        if needs_track:
            for order in self:
                prev = old.get(order.id, {})
                if not prev:
                    continue

                # XML generated (first time linked)
                new_xml = order.cr_fe_xml_attachment_id
                if (not prev.get("xml")) and new_xml:
                    order._cr_post_fe_event(
                        title=_("Documento FE generado"),
                        body=_("Se generó el XML firmado (%s).") % (new_xml.name or ""),
                        attachments=[new_xml],
                    )

                # Status transitions
                if "cr_fe_status" in vals and prev.get("status") != order.cr_fe_status:
                    label = order._cr_fe_status_label(order.cr_fe_status)
                    extra = ""
                    if order.cr_fe_status in ("sent", "processing"):
                        extra = _("Documento enviado a Hacienda.")
                    elif order.cr_fe_status == "accepted":
                        extra = _("Documento aceptado por Hacienda.")
                    elif order.cr_fe_status == "rejected":
                        extra = _("Documento rechazado por Hacienda.")
                    elif order.cr_fe_status in ("error", "error_retry"):
                        extra = order.cr_fe_last_error or ""

                    order._cr_post_fe_event(
                        title=_("Estado FE: %s") % label,
                        body=extra,
                        attachments=[order.cr_fe_response_attachment_id] if order.cr_fe_response_attachment_id else None,
                    )
                    if (
                        order.cr_fe_status == "accepted"
                        and not self.env.context.get("cr_fe_skip_email_delivery")
                    ):
                        order.cr_pos_generate_receipt_pdf_if_accepted([order.id])
                        order._cr_try_send_accepted_email()

                # Response received (first time linked)
                new_resp = order.cr_fe_response_attachment_id
                if (not prev.get("resp")) and new_resp:
                    order._cr_post_fe_event(
                        title=_("Respuesta Hacienda recibida"),
                        body=_("Se almacenó la respuesta MH (%s).") % (new_resp.name or ""),
                        attachments=[new_resp],
                    )

        if "lines" in vals:
            self._cr_capture_reference_snapshot()

        reference_fields = {
            "cr_fe_reference_document_type",
            "cr_fe_reference_document_number",
            "cr_fe_reference_issue_date",
        }
        if (
            not self.env.context.get("cr_fe_skip_autosend_reference")
            and reference_fields.intersection(vals)
        ):
            targets = self.filtered(
                lambda o: o.cr_fe_error_code == "reference_pending"
                and o.cr_fe_status == "error_retry"
                and o._cr_is_credit_note_order()
                and o._cr_has_complete_refund_reference_data()
                and o.config_id
                and o.config_id.cr_fe_enabled
                and getattr(o.config_id, "cr_fe_auto_send_on_reference", True)
            )
            for order in targets:
                if not order._cr_should_emit_ticket():
                    continue
                try:
                    with self.env.cr.savepoint():
                        order.with_context(cr_fe_skip_autosend_reference=True)._cr_prepare_te_document()
                except SerializationFailure:
                    self._logger.warning(
                        "Skipping immediate FE prepare for POS order %s due to concurrent update; cron will retry.",
                        order.id,
                    )
                except Exception as error:  # noqa: BLE001
                    self._logger.exception("Immediate FE prepare failed for POS order %s: %s", order.id, error)

        return res

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._cr_prefill_reference_from_origin_order()
        records._cr_capture_reference_snapshot()
        return records

    @api.model
    def create_from_ui(self, orders, draft=False):
        result = super().create_from_ui(orders, draft=draft)
        records = self.browse([item.get("id") if isinstance(item, dict) else item for item in result]).exists()
        # Refund references must be available as soon as the order is created,
        # even when the POS keeps it in draft before registering a payment.
        records._cr_prefill_reference_from_origin_order()
        records._cr_capture_reference_snapshot()
        if draft:
            # Refund orders are often created in draft first ("Devolver") and
            # finalized later. Return FE reference fields immediately so OWL POS
            # can display/print the NC linkage without waiting for payment.
            return self._cr_attach_fe_fields_to_ui_result(result)
        records._cr_capture_reference_on_payment()
        records._cr_process_after_payment()
        return self._cr_attach_fe_fields_to_ui_result(result)

    @api.model
    def _cr_attach_fe_fields_to_ui_result(self, result):
        """Attach FE fields to create_from_ui response for immediate POS printing."""

        order_ids = [item.get("id") if isinstance(item, dict) else item for item in (result or [])]
        order_ids = [order_id for order_id in order_ids if order_id]
        if not order_ids:
            return result

        orders = self.browse(order_ids).exists()

        # Enterprise UX: print-safe response must include FE identifiers whenever
        # a ticket is legally emitable. Run deterministic TE preparation before
        # serializing the payload so POS receipt avoids "Pendiente" on first print.
        orders_to_prepare = orders.filtered(
            lambda o: o._cr_should_emit_ticket()
            and not o._cr_should_delay_credit_note_xml()
            and (not o.cr_fe_consecutivo or not o.cr_fe_clave)
        )
        for order in orders_to_prepare:
            try:
                with self.env.cr.savepoint():
                    order._cr_prepare_te_document()
            except Exception as error:  # noqa: BLE001
                self._logger.info(
                    "Unable to enrich FE identifiers in create_from_ui response for POS order %s: %s",
                    order.id,
                    error,
                )

        fields_to_read = [
            "id",
            "cr_fe_document_type",
            "cr_fe_consecutivo",
            "cr_fe_clave",
            "cr_fe_status",
            "fp_payment_method",
            "cr_fe_reference_document_type",
            "cr_fe_reference_document_number",
            "cr_fe_reference_issue_date",
            "cr_fe_reference_code",
            "cr_fe_reference_reason",
        ]
        order_data = {
            row["id"]: row
            for row in orders
            .with_context(prefetch_fields=False)
            .read(fields_to_read)
        }

        enriched = []
        for item in result:
            order_id = item.get("id") if isinstance(item, dict) else item
            payload = order_data.get(order_id, {})
            if isinstance(item, dict):
                enriched.append({**item, **payload})
            else:
                enriched.append(payload or {"id": order_id})
        return enriched

    @api.model
    def cr_pos_get_order_fe_for_receipt(self, order_id=None, references=None):
        """Return FE receipt fields and prepare TE identifiers on-demand when possible."""

        refs = []
        for value in references or []:
            if value in (False, None):
                continue
            normalized = str(value).strip()
            if normalized and normalized not in refs:
                refs.append(normalized)

        domain = []
        if order_id:
            domain = [("id", "=", int(order_id))]
        elif refs:
            domain = ["|", ("pos_reference", "in", refs), ("name", "in", refs)]
        else:
            return {}

        allowed_companies = self.env.user.company_ids.ids
        if allowed_companies:
            domain = ["&", ("company_id", "in", allowed_companies)] + domain

        order = self.search(domain, limit=1)
        if not order:
            return {}

        should_prepare_identifiers = (
            order._cr_should_emit_ticket()
            and not order._cr_should_delay_credit_note_xml()
            and (not order.cr_fe_consecutivo or not order.cr_fe_clave)
        )
        if should_prepare_identifiers:
            try:
                with self.env.cr.savepoint():
                    order.sudo().with_company(order.company_id)._cr_prepare_te_document()
                    order.invalidate_recordset(["cr_fe_document_type", "cr_fe_consecutivo", "cr_fe_clave", "cr_fe_status"])
            except Exception as error:  # noqa: BLE001
                self._logger.info(
                    "Unable to prepare FE identifiers for POS receipt order %s: %s",
                    order.id,
                    error,
                )

        reference_payload = {}
        if order._cr_is_credit_note_order():
            reference_payload = order._cr_get_refund_reference_data() or {}
            if reference_payload:
                default_reason = _("Devolución de mercadería")
                if not reference_payload.get("code"):
                    reference_payload["code"] = "01"
                if not reference_payload.get("reason"):
                    reference_payload["reason"] = default_reason

        company_partner = order.company_id.partner_id
        receptor_partner = order.partner_id
        emisor_address_parts = [
            company_partner.street,
            (getattr(company_partner, "fp_neighborhood_id", False) and getattr(company_partner.fp_neighborhood_id, "name", False)) or False,
            (getattr(company_partner, "fp_district_id", False) and getattr(company_partner.fp_district_id, "name", False)) or False,
            (getattr(company_partner, "fp_canton_id", False) and getattr(company_partner.fp_canton_id, "name", False)) or False,
            (getattr(company_partner, "fp_province_id", False) and getattr(company_partner.fp_province_id, "name", False)) or False,
        ]
        receptor_address_parts = [
            receptor_partner.street if receptor_partner else False,
            ((getattr(receptor_partner, "fp_neighborhood_id", False) and getattr(receptor_partner.fp_neighborhood_id, "name", False)) if receptor_partner else False),
            ((getattr(receptor_partner, "fp_district_id", False) and getattr(receptor_partner.fp_district_id, "name", False)) if receptor_partner else False),
            ((getattr(receptor_partner, "fp_canton_id", False) and getattr(receptor_partner.fp_canton_id, "name", False)) if receptor_partner else False),
            ((getattr(receptor_partner, "fp_province_id", False) and getattr(receptor_partner.fp_province_id, "name", False)) if receptor_partner else False),
        ]

        return {
            "id": order.id,
            "pos_reference": order.pos_reference,
            "cr_fe_document_type": order.cr_fe_document_type,
            "cr_fe_consecutivo": order.cr_fe_consecutivo,
            "cr_fe_clave": order.cr_fe_clave,
            "cr_fe_status": order.cr_fe_status,
            "fp_payment_method": order.fp_payment_method,
            "cr_fe_reference_document_type": order.cr_fe_reference_document_type or reference_payload.get("document_type"),
            "cr_fe_reference_document_number": order.cr_fe_reference_document_number or reference_payload.get("number"),
            "cr_fe_reference_issue_date": order.cr_fe_reference_issue_date or reference_payload.get("issue_date"),
            "cr_fe_reference_code": order.cr_fe_reference_code or reference_payload.get("code"),
            "cr_fe_reference_reason": order.cr_fe_reference_reason or reference_payload.get("reason"),
            "cr_fe_emisor_name": order.company_id.name,
            "cr_fe_emisor_vat": order.company_id.vat,
            "cr_fe_emisor_email": company_partner.email,
            "cr_fe_emisor_phone": order._cr_get_partner_phone(company_partner),
            "cr_fe_emisor_address": ", ".join([part for part in emisor_address_parts if part]),
            "cr_fe_receptor_name": receptor_partner.name if receptor_partner else False,
            "cr_fe_receptor_vat": receptor_partner.vat if receptor_partner else False,
            "cr_fe_receptor_email": receptor_partner.email if receptor_partner else False,
            "cr_fe_receptor_phone": order._cr_get_partner_phone(receptor_partner),
            "cr_fe_receptor_address": ", ".join([part for part in receptor_address_parts if part]),
        }

    @api.model
    def _process_order(self, order, *args, **kwargs):
        """Process a POS order coming from UI sync.

        Odoo and optional addons (e.g. pos_online_payment) call this method with
        different signatures across versions:

        - _process_order(order, existing_order=False)
        - _process_order(order, draft, existing_order=False)

        This override keeps compatibility while ensuring FE flows always see
        persisted NC reference data before building XML.
        """
        order = self._cr_sanitize_ui_order_for_core(order)
        draft = False
        existing_order = False

        if args:
            if isinstance(args[0], bool):
                draft = args[0]
                if len(args) > 1:
                    existing_order = args[1]
            else:
                existing_order = args[0]

        try:
            result = super()._process_order(order, *args, **kwargs)
        except TypeError:
            # Fallback for older/newer signatures.
            try:
                result = super()._process_order(order, draft, existing_order, **kwargs)
            except TypeError:
                result = super()._process_order(order, draft, **kwargs)

        if draft or not result:
            return result

        order_record = self.browse(result).exists() if isinstance(result, int) else result
        # POS frontend payments reach this path directly and may skip
        # `action_pos_order_paid`; persist NC references before FE preparation
        # so `_cr_prepare_te_document` can always build the XML deterministically.
        order_record._cr_capture_reference_on_payment()
        order_record._cr_process_after_payment()
        return result

    @api.model
    def _cr_sanitize_ui_order_for_core(self, order):
        """Remove non-model keys from POS payload before delegating to core.

        Odoo's `pos.order._process_order` may call `create` directly with keys
        present in the incoming payload. If FE-specific frontend payload keys are
        left untouched (for example `cr_other_charges`), core raises:
        `ValueError: Invalid field ... in 'pos.order'`.

        We keep the information in `data` so `_order_fields` can map it to
        persisted fields (`cr_other_charges_json`) in a controlled way.
        """
        if not isinstance(order, dict):
            return order

        sanitized = dict(order)
        payload = sanitized.get("data") if isinstance(sanitized.get("data"), dict) else None
        payload_source = payload if isinstance(payload, dict) else sanitized

        # Compatibility aliases from custom/legacy POS UIs.
        # Keep these values in payload_source so FE extractors can map them safely.
        aliased_payload_keys = (
            "cr_other_charges",
            "other_charges",
            "otros_cargos",
            "service_charge_10",
            "service_charge",
        )
        for key in aliased_payload_keys:
            if key in sanitized and key not in payload_source:
                payload_source[key] = sanitized.get(key)

        self._cr_apply_service_charge_as_tip(sanitized, payload_source)
        self._cr_map_ui_payload_to_order_fields(sanitized, payload_source)

        # `data` is not a `pos.order` field in this deployment and can trigger:
        # ValueError: Invalid field 'data' in 'pos.order'
        sanitized.pop("data", None)
        sanitized.pop("reference", None)
        for key in aliased_payload_keys:
            sanitized.pop(key, None)
        return sanitized

    @api.model
    def _cr_apply_service_charge_as_tip(self, sanitized_order, payload):
        """Map custom service-charge flags to Odoo's native tip field.

        Odoo 19 POS supports "Propinas" natively. When custom UI sends
        `service_charge_10/service_charge`, we convert it to a deterministic tip
        amount (10% del subtotal sin impuestos) if the target tip field exists.
        """
        if not isinstance(sanitized_order, dict) or not isinstance(payload, dict):
            return

        service_flag = payload.get("service_charge_10")
        if service_flag in (None, False, ""):
            service_flag = payload.get("service_charge")
        if isinstance(service_flag, str):
            service_flag = service_flag.strip().lower() in {"1", "true", "t", "yes", "si", "sí"}
        if not service_flag:
            return

        # Odoo versions/modules may expose a different field name.
        tip_field = None
        for candidate in ("tip_amount", "amount_tip"):
            if candidate in self._fields:
                tip_field = candidate
                break
        if not tip_field:
            return

        if sanitized_order.get(tip_field) not in (None, False, ""):
            # Respect value already computed by POS core/UI.
            return

        subtotal = self._cr_extract_subtotal_from_ui_payload(payload)
        tip_amount = self._cr_calculate_service_charge_amount(subtotal)
        if tip_amount <= 0:
            return
        sanitized_order[tip_field] = tip_amount

    @api.model
    def _cr_map_ui_payload_to_order_fields(self, sanitized_order, payload):
        """Map FE payload keys to real `pos.order` fields before direct create().

        Some Odoo/Enterprise flows bypass `_order_fields` and call `create`
        directly with the sanitized payload. Persist critical FE metadata here so
        it survives those flows without leaking unknown keys to ORM.
        """
        if not isinstance(sanitized_order, dict) or not isinstance(payload, dict):
            return

        charges = self._cr_extract_other_charges_from_ui(payload)
        if charges:
            sanitized_order["cr_other_charges_json"] = json.dumps(charges)

        manual_reference = self._cr_extract_manual_reference_from_ui(payload)
        for field_name, value in manual_reference.items():
            if field_name in self._fields and value not in (False, None, ""):
                sanitized_order[field_name] = value

    @api.model
    def _order_fields(self, ui_order):
        fields_vals = super()._order_fields(ui_order)
        self._cr_mark_other_charge_line_commands(fields_vals)
        charges = self._cr_extract_other_charges_from_ui(ui_order)
        if charges:
            fields_vals["cr_other_charges_json"] = json.dumps(charges)

        auto_reference = self._cr_extract_refund_reference_from_ui(ui_order)
        manual_reference = self._cr_extract_manual_reference_from_ui(ui_order)

        if auto_reference or manual_reference:
            # Merge strategy (Enterprise-safe): auto-derived refund reference provides
            # a deterministic baseline and operator-provided values always override.
            # This guarantees `code/reason` defaults are present even when UI sends
            # only partial manual data (common in refund flows).
            merged_reference = {**auto_reference, **manual_reference}
            if merged_reference.get("cr_fe_reference_document_type") and merged_reference.get("cr_fe_reference_document_number") and merged_reference.get("cr_fe_reference_issue_date"):
                merged_reference.setdefault("cr_fe_reference_code", "01")
                merged_reference.setdefault("cr_fe_reference_reason", _("Devolución de mercadería"))
            fields_vals.update(merged_reference)

        return fields_vals

    @api.model
    def _order_line_fields(self, line, session_id=None):
        try:
            fields_vals = super()._order_line_fields(line, session_id=session_id)
        except TypeError:
            fields_vals = super()._order_line_fields(line)

        line_payload = line[2] if isinstance(line, (list, tuple)) and len(line) > 2 else {}
        if self._cr_is_other_charge_line_payload(line_payload):
            if "fp_is_other_charge_line" in self.env["pos.order.line"]._fields:
                fields_vals["fp_is_other_charge_line"] = True
            if "cr_is_other_charge_line" in self.env["pos.order.line"]._fields:
                fields_vals["cr_is_other_charge_line"] = True
        return fields_vals

    @api.model
    def _cr_extract_manual_reference_from_ui(self, ui_order):
        """Extract manual NC reference data serialized by POS UI payload."""
        if not isinstance(ui_order, dict):
            return {}

        payload = ui_order.get("data", ui_order)
        if not isinstance(payload, dict):
            return {}

        reference_payload = payload.get("reference") if isinstance(payload.get("reference"), dict) else {}
        candidate_values = {
            "cr_fe_reference_document_type": payload.get("cr_fe_reference_document_type") or reference_payload.get("document_type"),
            "cr_fe_reference_document_number": payload.get("cr_fe_reference_document_number") or reference_payload.get("number"),
            "cr_fe_reference_issue_date": payload.get("cr_fe_reference_issue_date") or reference_payload.get("issue_date"),
            "cr_fe_reference_code": payload.get("cr_fe_reference_code") or reference_payload.get("code"),
            "cr_fe_reference_reason": payload.get("cr_fe_reference_reason") or reference_payload.get("reason"),
        }

        manual_reference = {}
        for field_name, raw_value in candidate_values.items():
            if raw_value in (False, None):
                continue
            value = raw_value.strip() if isinstance(raw_value, str) else raw_value
            if isinstance(value, str) and value.lower() in {"false", "null", "none", "undefined"}:
                continue
            if value in (False, None, ""):
                continue
            if field_name == "cr_fe_reference_issue_date":
                value = fields.Date.to_date(value)
                if not value:
                    continue
            manual_reference[field_name] = value
        return manual_reference

    @api.model
    def _cr_extract_other_charges_from_ui(self, ui_order):
        if not isinstance(ui_order, dict):
            return []
        payload = ui_order.get("data", ui_order)
        if not isinstance(payload, dict):
            return []
        subtotal = self._cr_extract_subtotal_from_ui_payload(payload)
        candidates = (
            payload.get("cr_other_charges"),
            payload.get("other_charges"),
            payload.get("otros_cargos"),
        )
        for candidate in candidates:
            normalized = self._cr_normalize_other_charges(candidate, subtotal=subtotal)
            if normalized:
                return normalized
        tip_line_charge = self._cr_extract_other_charge_from_ui_lines(payload, subtotal=subtotal)
        if tip_line_charge:
            return [tip_line_charge]
        service_flag = payload.get("service_charge_10") or payload.get("service_charge")
        if isinstance(service_flag, str):
            service_flag = service_flag.strip().lower() in {"1", "true", "t", "yes", "si", "sí"}
        computed_service_charge = self._cr_build_service_charge(subtotal) if service_flag else {}
        if computed_service_charge:
            return [computed_service_charge]
        return []

    @api.model
    def _cr_mark_other_charge_line_commands(self, order_vals):
        """Mark line commands so l10n_cr_einvoice can consume fp_is_other_charge_line."""
        if (
            not isinstance(order_vals, dict)
            or not isinstance(order_vals.get("lines"), list)
        ):
            return
        line_fields = self.env["pos.order.line"]._fields
        for command in order_vals["lines"]:
            if not isinstance(command, (list, tuple)) or len(command) < 3 or not isinstance(command[2], dict):
                continue
            if self._cr_is_other_charge_line_payload(command[2]):
                if "fp_is_other_charge_line" in line_fields:
                    command[2]["fp_is_other_charge_line"] = True
                if "cr_is_other_charge_line" in line_fields:
                    command[2]["cr_is_other_charge_line"] = True

    @api.model
    def _cr_extract_other_charge_from_ui_lines(self, payload, subtotal=0.0):
        """Build OtrosCargos from UI line-level marker fp_is_other_charge_line/is_tip_line."""
        line_commands = payload.get("lines")
        if not isinstance(line_commands, list):
            return {}

        total_amount = 0.0
        for command in line_commands:
            if not isinstance(command, (list, tuple)) or len(command) < 3 or not isinstance(command[2], dict):
                continue
            line_vals = command[2]
            if not self._cr_is_other_charge_line_payload(line_vals):
                continue
            line_amount = self._cr_get_other_charge_line_amount(line_vals)
            if line_amount > 0:
                total_amount += line_amount

        if total_amount <= 0:
            return {}

        percent = round(self._cr_get_service_charge_percent(), 5)
        percent_display = f"{percent:.5f}".rstrip("0").rstrip(".")
        return {
            "type": "01",
            "code": "06",
            "amount": round(total_amount, 5),
            "currency": str(payload.get("currency") or "CRC"),
            "description": f"Imp. Serv {percent_display}%",
            "percent": percent,
            "fp_is_other_charge_line": True,
        }

    @api.model
    def _cr_get_other_charge_line_amount(self, line_vals):
        subtotal_candidates = (
            line_vals.get("price_subtotal"),
            line_vals.get("subtotal"),
            line_vals.get("price_subtotal_incl"),
        )
        for candidate in subtotal_candidates:
            try:
                amount = float(candidate)
            except (TypeError, ValueError):
                continue
            if amount > 0:
                return amount
        try:
            qty = float(line_vals.get("qty", 0.0))
            price_unit = float(line_vals.get("price_unit", 0.0))
            discount = float(line_vals.get("discount", 0.0))
        except (TypeError, ValueError):
            return 0.0
        if qty <= 0 or price_unit <= 0:
            return 0.0
        discount = min(max(discount, 0.0), 100.0)
        return round((qty * price_unit) * (1 - (discount / 100.0)), 5)

    @api.model
    def _cr_is_other_charge_line_payload(self, line_vals):
        if not isinstance(line_vals, dict):
            return False
        for marker in ("fp_is_other_charge_line", "cr_is_other_charge_line", "is_tip_line", "is_tip", "cr_is_tip_line"):
            value = line_vals.get(marker)
            if isinstance(value, str):
                value = value.strip().lower() in {"1", "true", "t", "yes", "si", "sí"}
            if value:
                return True
        product_id = line_vals.get("product_id")
        if isinstance(product_id, (list, tuple)):
            product_id = product_id[0] if product_id else False
        try:
            product_id = int(product_id)
        except (TypeError, ValueError):
            return False
        if product_id <= 0:
            return False
        product = self.env["product.product"].browse(product_id).exists()
        return bool(product and self._cr_is_other_charge_product(product))

    @api.model
    def _cr_extract_subtotal_from_ui_payload(self, payload):
        if not isinstance(payload, dict):
            return 0.0
        subtotal_candidates = (
            payload.get("amount_subtotal"),
            payload.get("subtotal"),
            payload.get("total_without_tax"),
        )
        for candidate in subtotal_candidates:
            try:
                amount = float(candidate)
            except (TypeError, ValueError):
                continue
            if amount > 0:
                return amount
        try:
            amount_total = float(payload.get("amount_total", 0.0))
            amount_tax = float(payload.get("amount_tax", 0.0))
        except (TypeError, ValueError):
            return 0.0
        subtotal = amount_total - amount_tax
        return subtotal if subtotal > 0 else 0.0

    @api.model
    def _cr_build_service_charge(self, subtotal):
        amount = self._cr_calculate_service_charge_amount(subtotal)
        if amount <= 0:
            return {}
        return {
            "type": "01",
            "code": "06",
            "amount": amount,
            "currency": "CRC",
            "description": "Impuesto de servicio 10%",
            "percent": 10,
        }

    @api.model
    def _cr_calculate_service_charge_amount(self, subtotal):
        try:
            base = float(subtotal or 0.0)
        except (TypeError, ValueError):
            return 0.0
        if base <= 0:
            return 0.0
        return round(base * 0.10, 5)

    @api.model
    def _cr_extract_refund_reference_from_ui(self, ui_order):
        """Derive NC reference fields from refunded POS lines in UI payload."""
        if not isinstance(ui_order, dict):
            return {}

        payload = ui_order.get("data", ui_order)
        if not isinstance(payload, dict):
            return {}

        line_commands = payload.get("lines")
        if not isinstance(line_commands, list):
            return {}

        refunded_line_ids = []
        for command in line_commands:
            if not isinstance(command, (list, tuple)) or len(command) < 3:
                continue
            line_values = command[2]
            if not isinstance(line_values, dict):
                continue

            refunded_value = line_values.get("refunded_orderline_id")
            if isinstance(refunded_value, (list, tuple)):
                refunded_value = refunded_value[0] if refunded_value else False

            try:
                refunded_id = int(refunded_value)
            except (TypeError, ValueError):
                continue

            if refunded_id > 0 and refunded_id not in refunded_line_ids:
                refunded_line_ids.append(refunded_id)

        if not refunded_line_ids:
            return {}

        line_data = self.env["pos.order.line"].sudo().with_context(prefetch_fields=False).search_read(
            [("id", "in", refunded_line_ids)], ["id", "order_id"], limit=1
        )
        if not line_data or not line_data[0].get("order_id"):
            return {}

        origin_order_id = line_data[0]["order_id"][0]
        if not origin_order_id:
            return {}

        origin_order_data = self.sudo().with_context(prefetch_fields=False).search_read(
            [("id", "=", origin_order_id)],
            [
                "cr_fe_document_type",
                "cr_fe_clave",
                "cr_fe_consecutivo",
                "date_order",
                "cr_fe_reference_document_type",
                "cr_fe_reference_document_number",
                "cr_fe_reference_issue_date",
                "cr_fe_reference_code",
                "cr_fe_reference_reason",
            ],
            limit=1,
        )
        if not origin_order_data:
            return {}

        origin_vals = origin_order_data[0]
        document_type = origin_vals.get("cr_fe_reference_document_type") or {
            "fe": "01",
            "te": "04",
            "nc": "03",
        }.get(origin_vals.get("cr_fe_document_type"), False)
        number = (
            origin_vals.get("cr_fe_reference_document_number")
            or origin_vals.get("cr_fe_clave")
            or origin_vals.get("cr_fe_consecutivo")
        )
        issue_date = origin_vals.get("cr_fe_reference_issue_date")
        if not issue_date and origin_vals.get("date_order"):
            origin_dt = fields.Datetime.to_datetime(origin_vals["date_order"])
            issue_date = origin_dt.date() if origin_dt else fields.Date.to_date(origin_vals["date_order"])

        if not all((document_type, number, issue_date)):
            return {}

        return {
            "cr_fe_reference_document_type": document_type,
            "cr_fe_reference_document_number": number,
            "cr_fe_reference_issue_date": fields.Date.to_date(issue_date),
            "cr_fe_reference_code": origin_vals.get("cr_fe_reference_code") or "01",
            "cr_fe_reference_reason": origin_vals.get("cr_fe_reference_reason") or _("Devolución de mercadería"),
        }

    def _cr_normalize_other_charges(self, raw_charges, subtotal=0.0):
        if not raw_charges:
            return []
        if isinstance(raw_charges, str):
            try:
                raw_charges = json.loads(raw_charges)
            except json.JSONDecodeError:
                return []
        if not isinstance(raw_charges, list):
            return []

        normalized = []
        for charge in raw_charges:
            if not isinstance(charge, dict):
                continue
            charge_code = str(charge.get("code") or charge.get("codigo") or "")
            if charge_code and charge_code != "06":
                continue
            service_charge = self._cr_build_service_charge(subtotal)
            amount = charge.get("amount", charge.get("monto"))
            if amount in (False, None, "") and service_charge:
                amount = service_charge["amount"]
            try:
                amount = float(amount)
            except (TypeError, ValueError):
                continue
            if amount <= 0:
                continue
            normalized.append(
                {
                    "type": str(charge.get("type") or charge.get("tipo") or charge.get("charge_type") or "01"),
                    "code": "06",
                    "amount": round(amount, 5),
                    "currency": str(charge.get("currency") or charge.get("moneda") or "CRC"),
                    "description": str(charge.get("description") or charge.get("detalle") or "Impuesto de servicio 10%"),
                    "percent": charge.get("percent", charge.get("porcentaje")) or 10,
                    "fp_is_other_charge_line": bool(charge.get("fp_is_other_charge_line")),
                }
            )
        return normalized

    def _cr_get_other_charges_payload(self):
        self.ensure_one()
        explicit_charges = self._cr_normalize_other_charges(self.cr_other_charges_json)
        if explicit_charges:
            return explicit_charges
        tip_charge = self._cr_build_service_charge_from_tip_lines()
        return [tip_charge] if tip_charge else []

    def _cr_get_tip_product(self):
        self.ensure_one()
        config = self.config_id or self.session_id.config_id
        if not config:
            return self.env["product.product"]
        for field_name in ("cr_tip_product_id", "pos_tip_product_id", "tip_product_id"):
            if field_name in config._fields and config[field_name]:
                return config[field_name]
        return self.env["product.product"]

    def _cr_is_other_charge_product(self, product):
        """Detect products flagged as Otros Cargos in compatible FE modules."""
        if not product:
            return False
        product_tmpl = product.product_tmpl_id
        candidate_markers = (
            "is_other_charge",
            "is_other_charges",
            "is_otros_cargos",
            "is_otro_cargo",
            "l10n_cr_is_other_charge",
            "l10n_cr_other_charge",
            "fp_is_other_charge",
            "fe_is_other_charge",
            "cr_is_other_charge",
        )
        for marker in candidate_markers:
            if marker in product._fields and product[marker]:
                return True
            if product_tmpl and marker in product_tmpl._fields and product_tmpl[marker]:
                return True
        return False

    def _cr_is_tip_line(self, line, tip_product=False):
        """Centralized predicate to detect native/custom POS tip lines safely."""
        if not line:
            return False
        if not tip_product:
            tip_product = self._cr_get_tip_product()
        if tip_product and line.product_id == tip_product:
            return True
        # Compatibility with custom POS modules that annotate line flags.
        for marker in ("is_tip", "is_tip_line", "cr_is_tip_line"):
            if marker in line._fields and line[marker]:
                return True
        if "cr_is_other_charge_line" in line._fields and line.cr_is_other_charge_line:
            return True
        if "fp_is_other_charge_line" in line._fields and line.fp_is_other_charge_line:
            return True
        if self._cr_is_other_charge_product(line.product_id):
            return True
        return False

    def _cr_get_tip_line_ids(self):
        self.ensure_one()
        tip_product = self._cr_get_tip_product()
        tip_line_ids = {line.id for line in self.lines if self._cr_is_tip_line(line, tip_product=tip_product)}
        if tip_line_ids:
            return tip_line_ids
        return self._cr_guess_tip_line_ids()

    def _cr_get_service_charge_percent(self):
        self.ensure_one()
        config = self.config_id or self.session_id.config_id
        percent = 10.0
        if config and "cr_service_charge_percent" in config._fields:
            percent = float(config.cr_service_charge_percent or 10.0)
        return percent if percent > 0 else 10.0

    def _cr_is_tip_name_candidate(self, line):
        if not line or not line.product_id:
            return False
        product_name = line.product_id.display_name or line.product_id.name or ""
        normalized_name = unicodedata.normalize("NFKD", product_name).encode("ascii", "ignore").decode("ascii").lower()
        tip_keywords = ("propina", "servicio", "tip", "service charge")
        return any(keyword in normalized_name for keyword in tip_keywords)

    def _cr_guess_tip_line_ids(self):
        """Fallback for sessions where tip product isn't configured on POS config.

        We only classify as tip/otros-cargos when a single candidate line matches:
        - product name strongly suggests tip/service charge, and
        - its subtotal equals configured service percentage over non-tip subtotal.
        """
        self.ensure_one()
        candidate_lines = [
            line
            for line in self.lines
            if (line.price_subtotal or 0.0) > 0 and self._cr_is_tip_name_candidate(line)
        ]
        if len(candidate_lines) != 1:
            return set()

        candidate = candidate_lines[0]
        base_subtotal = sum(
            line.price_subtotal or 0.0
            for line in self.lines
            if line.id != candidate.id and (line.price_subtotal or 0.0) > 0
        )
        if base_subtotal <= 0:
            return set()

        expected = round(base_subtotal * (self._cr_get_service_charge_percent() / 100.0), 5)
        tip_amount = round(candidate.price_subtotal or 0.0, 5)
        tolerance = 0.05
        if abs(tip_amount - expected) > tolerance:
            return set()
        return {candidate.id}

    def _cr_build_service_charge_from_tip_lines(self):
        """Derive FE OtrosCargos code 06 from native Odoo POS tip lines.

        This keeps native POS tip UX (including the Propina button) while sending
        the service amount as `OtrosCargos` instead of `DetalleServicio`.
        """
        self.ensure_one()
        tip_line_ids = self._cr_get_tip_line_ids()
        if not tip_line_ids:
            return {}

        tip_amount = sum(line.price_subtotal or 0.0 for line in self.lines if line.id in tip_line_ids)
        if tip_amount <= 0:
            return {}

        base_subtotal = sum(
            line.price_subtotal or 0.0
            for line in self.lines
            if line.id not in tip_line_ids and (line.price_subtotal or 0.0) > 0
        )
        percent = round(self._cr_get_service_charge_percent(), 5)
        percent_display = f"{percent:.5f}".rstrip("0").rstrip(".")
        return {
            "type": "01",
            "code": "06",
            "amount": round(tip_amount, 5),
            "currency": str(self.currency_id.name or "CRC"),
            "description": f"Imp. Serv {percent_display}%",
            "percent": percent,
            "fp_is_other_charge_line": True,
        }

    def action_pos_order_paid(self):
        """Trigger FE flow when the order is validated from backend POS forms.

        Para NC (refund), algunas rutas pueden crear/postear movimientos antes de que
        la referencia esté persistida en el pedido. Capturamos referencia antes del
        flujo base y repetimos al final para asegurar consistencia.
        """
        self._cr_capture_reference_on_payment()

        result = super().action_pos_order_paid()

        paid_orders = self.filtered(lambda order: order.state in ("paid", "done", "invoiced"))
        paid_orders._cr_capture_reference_on_payment()
        paid_orders._cr_process_after_payment()
        return result

    def _cr_capture_reference_on_payment(self):
        """Persist NC reference snapshot at payment time for deterministic XML build."""
        self._cr_capture_reference_snapshot()

    def _cr_prefill_reference_from_origin_order(self):
        """Copy reference fields from origin POS order as soon as a refund order exists.

        This covers refund creation flows where POS generates the refund order first
        and FE dispatch can start before additional writes happen.
        """
        for order in self:
            if not order._cr_is_credit_note_order():
                continue

            # Never overwrite manually provided values on the refund itself.
            existing_snapshot = order._cr_get_manual_reference_data()
            if all(existing_snapshot.get(key) for key in ("document_type", "number", "issue_date")) and all(
                (
                    order.cr_fe_reference_code,
                    order.cr_fe_reference_reason,
                )
            ):
                continue

            origin_order = order._cr_get_origin_order_for_refund()
            if not origin_order:
                continue

            origin_vals = origin_order.sudo().with_context(prefetch_fields=False).read(
                [
                    "cr_fe_document_type",
                    "cr_fe_clave",
                    "date_order",
                    "cr_fe_reference_document_type",
                    "cr_fe_reference_document_number",
                    "cr_fe_reference_issue_date",
                    "cr_fe_reference_code",
                    "cr_fe_reference_reason",
                ],
                load=False,
            )[0]

            resolved_document_type = origin_vals.get("cr_fe_reference_document_type")
            resolved_number = origin_vals.get("cr_fe_reference_document_number")
            resolved_issue_date = origin_vals.get("cr_fe_reference_issue_date")

            # Refund origin orders (FE/TE) usually do not store NC-style
            # `cr_fe_reference_*`; derive a deterministic snapshot from their
            # emitted FE metadata when required.
            if not resolved_document_type:
                resolved_document_type = {
                    "fe": "01",
                    "te": "04",
                    "nc": "03",
                }.get(origin_vals.get("cr_fe_document_type"), False)
            if not resolved_number:
                resolved_number = origin_vals.get("cr_fe_clave")
            if not resolved_issue_date and origin_vals.get("date_order"):
                origin_dt = order._cr_to_datetime(origin_vals.get("date_order"))
                if origin_dt:
                    resolved_issue_date = origin_dt.date()

            if not all((resolved_document_type, resolved_number, resolved_issue_date)):
                continue

            vals = {}
            if not existing_snapshot.get("document_type"):
                vals["cr_fe_reference_document_type"] = resolved_document_type
            if not existing_snapshot.get("number"):
                vals["cr_fe_reference_document_number"] = resolved_number
            if not existing_snapshot.get("issue_date"):
                vals["cr_fe_reference_issue_date"] = resolved_issue_date
            if not order.cr_fe_reference_code and origin_vals.get("cr_fe_reference_code"):
                vals["cr_fe_reference_code"] = origin_vals.get("cr_fe_reference_code")
            if not order.cr_fe_reference_reason and origin_vals.get("cr_fe_reference_reason"):
                vals["cr_fe_reference_reason"] = origin_vals.get("cr_fe_reference_reason")

            if vals:
                # Avoid unintended immediate autosend side effects while creating refunds;
                # FE dispatch will continue through the regular post-payment pipeline.
                order.sudo().with_context(cr_fe_skip_autosend_reference=True).write(vals)

    def _cr_capture_reference_snapshot(self):
        """Persist NC FE reference data as soon as the refund has enough source metadata."""
        for order in self:
            if not order._cr_is_credit_note_order():
                continue

            existing_snapshot = order._cr_get_manual_reference_data()
            required_reference_already_set = all(
                existing_snapshot.get(key) for key in ("document_type", "number", "issue_date")
            )
            if required_reference_already_set:
                # Existing/operator-provided values take precedence and must not be overwritten.
                # Still fill optional code/reason when missing so receipt/UI stays aligned
                # with XML payload defaults.
                vals = {}
                if not order.cr_fe_reference_code:
                    vals["cr_fe_reference_code"] = existing_snapshot.get("code") or "01"
                if not order.cr_fe_reference_reason:
                    vals["cr_fe_reference_reason"] = existing_snapshot.get("reason") or _("Devolución de mercadería")
                if vals:
                    order.sudo().write(vals)
                continue

            reference_data = order._cr_get_refund_reference_data()
            if not reference_data:
                continue

            vals = {}
            if not existing_snapshot.get("document_type") and reference_data.get("document_type"):
                vals["cr_fe_reference_document_type"] = reference_data.get("document_type")
            if not existing_snapshot.get("number") and reference_data.get("number"):
                vals["cr_fe_reference_document_number"] = reference_data.get("number")
            if not existing_snapshot.get("issue_date") and reference_data.get("issue_date"):
                vals["cr_fe_reference_issue_date"] = reference_data.get("issue_date")
            if not order.cr_fe_reference_code:
                vals["cr_fe_reference_code"] = reference_data.get("code") or "01"
            if not order.cr_fe_reference_reason:
                vals["cr_fe_reference_reason"] = reference_data.get("reason") or _("Devolución de mercadería")

            if vals:
                order.sudo().write(vals)

    def _cr_get_first_existing_field_value(self, record, field_names):
        """Return the first non-empty value from field names that exist on `record`."""
        if not record:
            return False
        for field_name in field_names:
            if field_name in record._fields and record[field_name]:
                return record[field_name]
        return False


    def _cr_to_datetime(self, value):
        """Best-effort conversion to a python ``datetime``.

        Why: some helpers rely on DB ``read()`` values which can be strings or
        python datetimes depending on context/load flags.
        """
        if not value:
            return False
        if isinstance(value, datetime):
            return value
        if isinstance(value, date) and not isinstance(value, datetime):
            return datetime.combine(value, time.min)
        try:
            dt = fields.Datetime.to_datetime(value)
            if dt:
                return dt
        except Exception:  # noqa: BLE001
            dt = False
        try:
            d = fields.Date.to_date(value)
            if d:
                return datetime.combine(d, time.min)
        except Exception:  # noqa: BLE001
            return False
        return False

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
        """Prevent POS invoice creation and avoid invoice PDF download in-session.

        CR FE flow for this addon is generated from ``pos.order`` (TE/FE/NC),
        therefore ``to_invoice`` must not create ``account.move`` in POS.
        """
        if self._cr_is_marked_for_invoicing():
            return self.env["account.move"]

        no_email_context = {
            "mail_notify_force_send": False,
            "mail_notify_noemail": True,
            "skip_invoice_send": True,
        }

        for key in ("send_email", "email", "mail_invoice", "send_mail"):
            if key in kwargs:
                kwargs[key] = False
        send_and_print_values = kwargs.get("send_and_print_values")
        if isinstance(send_and_print_values, dict):
            send_and_print_values.update({"send_mail": False, "send_email": False})

        explicit_context = kwargs.get("context")
        if isinstance(explicit_context, dict):
            kwargs["context"] = {**explicit_context, **no_email_context}

        return super(PosOrder, self.with_context(**no_email_context))._generate_pos_order_invoice(*args, **kwargs)

    def _cr_get_origin_order_for_refund(self):
        """Find the original POS order referenced by refunded lines."""
        self.ensure_one()
        if not self._cr_is_credit_note_order():
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
        # POS tickets (not invoiced) may keep the FE move on a dedicated field.
        # Reuse it so NC XML can set `reversed_entry_id` and inherit reference metadata.
        candidate_moves |= origin_order.mapped("cr_ticket_move_id").filtered(
            lambda move: move.move_type == "out_invoice" and move.state != "cancel"
        )
        if candidate_moves:
            return candidate_moves.sorted("invoice_date", reverse=True)[:1]
        return self.env["account.move"]

    def _cr_is_credit_note_order(self):
        """Best-effort check to identify POS refunds that must behave as NC."""
        self.ensure_one()
        if self._cr_is_refund_order_candidate():
            return True
        if self.cr_fe_document_type == "nc":
            return True
        return False

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
        fallback_doc_type = {
            "fe": "01",  # Factura Electrónica
            "te": "04",  # Tiquete Electrónico
            "nc": "03",  # Nota de Crédito
        }.get(origin_doc_type, "01")

        reference_doc_type = reference_data.get("document_type") or fallback_doc_type
        reference_number = reference_data.get("number") or (
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

        reference_date = reference_data.get("issue_date") or (
            (origin_order.date_order.date() if origin_order and origin_order.date_order else False)
            or (origin_invoice.invoice_date if origin_invoice else False)
            or fields.Date.context_today(self)
        )

        reference_datetime = reference_data.get("issue_datetime") or (
            (origin_order.date_order if origin_order and origin_order.date_order else False)
            or (datetime.combine(origin_invoice.invoice_date, time.min) if origin_invoice and origin_invoice.invoice_date else False)
        )
        if not reference_datetime and reference_date:
            reference_datetime = self._cr_to_datetime(reference_date)
        reference_code = reference_data.get("code") or (
            (getattr(origin_order, "fp_reference_code", False) if origin_order else False)
            or (getattr(origin_invoice, "fp_reference_code", False) if origin_invoice else False)
            or "01"
        )
        reference_reason = reference_data.get("reason") or (
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
            "reference_issue_date",
            "reference_document_date",
            "reference_date",
            "reversed_entry_date",
            "l10n_cr_reference_issue_date",
        ):
            if field_name in move_fields:
                values[field_name] = reference_date

        for field_name in (
            "fp_reference_issue_datetime",
            "reference_issue_datetime",
            "reference_document_datetime",
            "reference_datetime",
            "l10n_cr_reference_issue_datetime",
        ):
            if field_name in move_fields:
                values[field_name] = reference_datetime

        for field_name in (
            "fp_reference_reason",
            "reference_reason",
            "l10n_cr_reference_reason",
        ):
            if field_name in move_fields:
                values[field_name] = reference_reason

        return values

    def _cr_extract_issue_date_from_clave(self, clave):
        """Extract FE issue date from Costa Rica clave (positions 4-9: ddmmyy)."""
        self.ensure_one()
        if not clave:
            return False

        clave_text = str(clave).strip()
        if len(clave_text) < 9 or not clave_text[3:9].isdigit():
            return False

        try:
            day = int(clave_text[3:5])
            month = int(clave_text[5:7])
            year = 2000 + int(clave_text[7:9])
            return fields.Date.from_string(f"{year:04d}-{month:02d}-{day:02d}")
        except Exception:  # noqa: BLE001
            return False

    def _cr_get_refund_reference_data(self):
        """Return normalized FE reference data for refund orders."""
        self.ensure_one()
        if not self._cr_is_credit_note_order():
            return {}

        manual_reference_data = self._cr_get_manual_reference_data()
        if all(
            manual_reference_data.get(required_key)
            for required_key in ("document_type", "number", "issue_date")
        ):
            manual_reference_data.setdefault("code", "01")
            manual_reference_data.setdefault("reason", _("Devolución de mercadería"))
            if not manual_reference_data.get("issue_datetime") and manual_reference_data.get("issue_date"):
                manual_reference_data["issue_datetime"] = datetime.combine(
                    manual_reference_data["issue_date"],
                    time.min,
                )
            return manual_reference_data

        origin_order = self._cr_get_origin_order_for_refund()
        origin_invoice = self._cr_get_origin_invoice_for_refund()

        # Hacienda validates NC references against the original emitted document.
        # Do not build/send XML until the source document has a stable key + date.
        if origin_order:
            # Hard-refresh from DB to avoid stale values inside the same request.
            read_fields = [
                'cr_fe_clave',
                'cr_fe_consecutivo',
                'cr_fe_document_type',
                'date_order',
            ]
            for optional_field in ('fp_reference_code', 'fp_reference_reason'):
                if optional_field in origin_order._fields:
                    read_fields.append(optional_field)

            origin_vals = origin_order.sudo().with_context(prefetch_fields=False).read(
                read_fields,
                load=False,
            )[0]

            reference_number = origin_vals.get('cr_fe_clave') or False
            # If clave is not yet persisted but consecutivo exists, reconstruct deterministically.
            if not reference_number and origin_vals.get('cr_fe_consecutivo'):
                try:
                    reference_number = origin_order._cr_generate_fe_clave(origin_vals.get('cr_fe_consecutivo'))
                    origin_order.sudo().write({'cr_fe_clave': reference_number})
                except Exception:
                    reference_number = False
            origin_date_order = origin_vals.get('date_order')
            origin_dt = self._cr_to_datetime(origin_date_order)
            reference_datetime = origin_dt or False
            reference_date = origin_dt.date() if origin_dt else False
        else:
            reference_number = False
            reference_date = False
            reference_datetime = False

        if not reference_number and origin_invoice:
            reference_number = self._cr_get_first_existing_field_value(
                origin_invoice,
                (
                    "fp_external_id",
                    "l10n_cr_clave",
                    "l10n_cr_einvoice_key",
                    "l10n_cr_numero_consecutivo",
                    "fp_consecutive_number",
                    "fp_consecutive",
                    "name",
                    "payment_reference",
                ),
            )
        if not reference_date and origin_invoice:
            reference_date = (
                self._cr_get_first_existing_field_value(origin_invoice, ("invoice_date", "date", "create_date"))
                or False
            )
        if not reference_datetime and reference_date:
            # l10n_cr_einvoice requires a datetime (FechaEmisionIR); default midnight.
            reference_datetime = self._cr_to_datetime(reference_date)


        if not reference_date and reference_number:
            reference_date = self._cr_extract_issue_date_from_clave(reference_number)

        if not reference_date and origin_order:
            reference_date = (
                (origin_order.write_date.date() if origin_order.write_date else False)
                or (origin_order.create_date.date() if origin_order.create_date else False)
            )

        if not reference_number or not reference_date:
            return {}

        origin_doc_type = (origin_vals.get('cr_fe_document_type') if origin_order else False) or (
            "fe" if origin_invoice else False
        )
        reference_doc_type = {
            "fe": "01",  # Factura Electrónica
            "te": "04",  # Tiquete Electrónico
            "nc": "03",  # Nota de Crédito
        }.get(origin_doc_type, "01")
        reference_code = (
            (origin_vals.get("fp_reference_code") if origin_order else False)
            or (getattr(origin_invoice, "fp_reference_code", False) if origin_invoice else False)
            or "01"
        )
        reference_reason = (
            (origin_vals.get("fp_reference_reason") if origin_order else False)
            or (getattr(origin_invoice, "fp_reference_reason", False) if origin_invoice else False)
            or _("Devolución de mercadería")
        )
        return {
            "document_type": reference_doc_type,
            "number": reference_number,
            "issue_date": fields.Date.to_date(reference_date) if reference_date else False,
            "issue_datetime": reference_datetime,
            "code": reference_code,
            "reason": reference_reason,
        }

    def _cr_process_after_payment(self):
        self._cr_sync_other_charges_from_tip_lines()
        self._cr_dispatch_einvoice_flow()

    def _cr_sync_other_charges_from_tip_lines(self):
        """Persist derived OtrosCargos from tip product for reporting/audit fields."""
        for order in self:
            if order.cr_other_charges_json:
                continue
            tip_charge = order._cr_build_service_charge_from_tip_lines()
            if not tip_charge:
                continue
            order.cr_other_charges_json = json.dumps([tip_charge])

    def _cr_dispatch_einvoice_flow(self):
        for order in self:
            if order.config_id and not order.config_id.cr_fe_enabled:
                order.cr_fe_status = "not_applicable"
                continue

            if order._cr_requires_account_move_flow():
                invoice = order._cr_get_real_invoice_move()
                if invoice:
                    order._cr_sync_from_invoice_only()
                else:
                    order.write(
                        {
                            "cr_fe_status": "not_applicable",
                            "cr_fe_error_code": False,
                            "cr_fe_last_error": False,
                            "cr_fe_next_try": False,
                        }
                    )
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
                if order._cr_is_reference_pending_error(error):
                    order.write(
                        {
                            "cr_fe_status": "error_retry",
                            "cr_fe_error_code": "reference_pending",
                            "cr_fe_last_error": order._cr_build_reference_pending_message(),
                            "cr_fe_next_try": fields.Datetime.now() + timedelta(minutes=5),
                        }
                    )
                    continue
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
        if self._cr_requires_account_move_flow():
            self._cr_sync_from_invoice_only()
            return True
        return self._cr_send_pending_te_to_hacienda(force=force)

    def _cr_check_hacienda_status(self):
        self.ensure_one()
        if self._cr_requires_account_move_flow():
            self._cr_sync_from_invoice_only()
            return True
        return self._cr_check_pending_te_status()

    def _cr_prepare_te_document(self):
        self.ensure_one()
        if self._cr_requires_account_move_flow() or not self._cr_should_emit_ticket():
            return False

        self._cr_validate_before_send()
        if self._cr_is_credit_note_order():
            # Ensure reference fields are persisted before building XML.
            self._cr_prefill_reference_from_origin_order()
            self._cr_capture_reference_snapshot()
        if self._cr_should_delay_credit_note_xml():
            raise UserError(
                _(
                    "La nota electrónica requiere información de referencia. Complete "
                    "Tipo de documento, Número y Fecha de emisión del documento de referencia."
                )
            )

        idempotency_key = self._cr_get_or_create_idempotency_key()
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
            prefer_local=doc_type == "nc",
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
            "cr_fe_last_error": False,
            # Ensure cron/manual checks can send immediately once references become available.
            "cr_fe_next_try": fields.Datetime.now(),
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
        tip_line_ids = self._cr_get_tip_line_ids()
        lines = []
        for line in self.lines:
            if line.id in tip_line_ids:
                # FE CR v4.4: native tip product line must be represented in
                # `OtrosCargos` (code 06), not in `DetalleServicio`.
                continue
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

        other_charges = self._cr_get_other_charges_payload()

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
            "reference_document_date": reference_issue_date,
            "reference_date": reference_issue_date,
            "reference_code": reference_data.get("code"),
            "reference_reason": reference_data.get("reason"),
            "fp_reference_document_type": reference_data.get("document_type"),
            "fp_reference_document_number": reference_data.get("number"),
            "fp_reference_issue_date": reference_issue_date,
            "fp_reference_document_date": reference_issue_date,
            "fp_reference_date": reference_issue_date,
            "fp_reference_code": reference_data.get("code"),
            "fp_reference_reason": reference_data.get("reason"),
            "l10n_cr_reference_document_type": reference_data.get("document_type"),
            "l10n_cr_reference_document_number": reference_data.get("number"),
            "l10n_cr_reference_issue_date": reference_issue_date,
            "l10n_cr_reference_code": reference_data.get("code"),
            "l10n_cr_reference_reason": reference_data.get("reason"),
            "other_charges": other_charges,
            "otros_cargos": other_charges,
            "fp_other_charges": other_charges,
            "lines": lines,
        }

    
    # --- FE backend methods (called via _cr_call_service_method) ---


    def build_pos_xml_from_order(
        self,
        order_id,
        *,
        consecutivo,
        idempotency_key,
        clave,
        document_type,
        payload=None,
        **kwargs,
    ):
        """Build and sign TE/FE/NC XML for a POS order and store it as attachment on the order.

        Idempotency: if the order already has a signed XML attachment (or one exists with the same
        expected filename), reuse it instead of creating duplicates. This avoids double XML files when
        POS triggers prepare+send in quick succession across different transactions.
        """
        order = self.browse(order_id)
        order.ensure_one()

        if order._cr_requires_account_move_flow():
            return {"ok": False, "reason": "order_invoiced"}

        doc_prefix = {
            "te": "TE",
            "fe": "FE",
            "nc": "NC",
        }.get((document_type or order.cr_fe_document_type or order._cr_get_pos_document_type() or "").lower(), "DOC")
        expected_name = f"{doc_prefix}-{consecutivo}-firmado.xml"

        # 1) Reuse the attachment already linked on the order (most common case).
        if order.cr_fe_xml_attachment_id and order.cr_fe_xml_attachment_id.datas:
            xml_bytes = base64.b64decode(order.cr_fe_xml_attachment_id.datas)
            digest = hashlib.sha256(xml_bytes).hexdigest()
            return {"ok": True, "xml_attachment_id": order.cr_fe_xml_attachment_id.id, "digest": digest, "reused": True}

        # 2) Reuse any existing attachment with the expected name (race-safe).
        existing = (
            order.env["ir.attachment"]
            .sudo()
            .search(
                [
                    ("res_model", "=", "pos.order"),
                    ("res_id", "=", order.id),
                    ("mimetype", "=", "application/xml"),
                    ("name", "=", expected_name),
                ],
                order="id desc",
                limit=1,
            )
        )
        if existing and existing.datas:
            order.sudo().write({"cr_fe_xml_attachment_id": existing.id})
            xml_bytes = base64.b64decode(existing.datas)
            digest = hashlib.sha256(xml_bytes).hexdigest()
            return {"ok": True, "xml_attachment_id": existing.id, "digest": digest, "reused": True}

        move = order._cr_build_virtual_move(document_type=document_type, consecutivo=consecutivo, clave=clave)
        xml_text = move._fp_generate_invoice_xml(clave=clave)
        xml_text = order._cr_sanitize_ticket_receptor_activity(xml_text, document_type=document_type)
        signed_xml_text = move._fp_sign_xml(xml_text)

        xml_bytes = signed_xml_text.encode("utf-8")
        digest = hashlib.sha256(xml_bytes).hexdigest()

        attachment = order.env["ir.attachment"].create(
            {
                "name": expected_name,
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
        return {"ok": True, "xml_attachment_id": attachment.id, "digest": digest, "reused": False}

    def _cr_sanitize_ticket_receptor_activity(self, xml_text, *, document_type=None):
        """For TE, enforce omission of <CodigoActividadReceptor> in emitted XML.

        Hacienda TE documents must not include this node, even when the customer record has
        an economic activity configured. We sanitize only TE payloads and leave FE/NC intact.
        """
        self.ensure_one()
        doc_type = (document_type or self.cr_fe_document_type or self._cr_get_pos_document_type() or "").lower()
        if doc_type != "te" or not xml_text:
            return xml_text

        try:
            parser = etree.XMLParser(remove_blank_text=False, recover=True)
            root = etree.fromstring(xml_text.encode("utf-8"), parser=parser)
        except Exception:  # noqa: BLE001
            self._logger.warning("No se pudo parsear XML TE para remover CodigoActividadReceptor; se conserva el XML original.")
            return xml_text

        removed = 0
        for node in root.xpath("//*[local-name()='CodigoActividadReceptor']"):
            parent = node.getparent()
            if parent is not None:
                parent.remove(node)
                removed += 1

        if not removed:
            return xml_text

        return etree.tostring(root, encoding="unicode")


    def send_to_hacienda(self, order_id, *, document_type=None, idempotency_key=None, company_id=None, **kwargs):
        """Send the already-signed POS XML to Hacienda (Recepción v4.4)."""
        order = self.browse(order_id)
        order.ensure_one()
        if order._cr_requires_account_move_flow():
            return {"ok": False, "status": "not_applicable", "reason": "order_invoiced"}
        order._cr_get_or_create_idempotency_key()
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
        if order._cr_requires_account_move_flow():
            return {"ok": False, "status": "not_applicable", "reason": "order_invoiced"}
        order._cr_get_or_create_idempotency_key()
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

    def _cr_get_move_other_charges_field(self, move):
        """Return the account.move field used by FE XML generator for OtrosCargos."""
        self.ensure_one()
        if not move:
            return False, False

        supported_types = {"char", "text", "html", "json", "serialized"}
        preferred_names = (
            "fp_other_charges",
            "other_charges",
            "otros_cargos",
            "l10n_cr_other_charges",
            "cr_other_charges_json",
            "fp_other_charges_json",
            "other_charges_json",
            "otros_cargos_json",
            "l10n_cr_other_charges_json",
        )
        for field_name in preferred_names:
            field = move._fields.get(field_name)
            if field and field.type in supported_types:
                return field_name, field

        for field_name, field in move._fields.items():
            if field.type not in supported_types:
                continue
            normalized = field_name.lower()
            looks_like_other_charges = (
                ("other" in normalized and "charge" in normalized)
                or ("otros" in normalized and "cargos" in normalized)
                or ("otro" in normalized and "cargo" in normalized)
            )
            if looks_like_other_charges:
                return field_name, field

        return False, False

    def _cr_set_other_charges_on_virtual_move(self, move, other_charges):
        """Set OtrosCargos payload in the concrete field expected by l10n_cr_einvoice."""
        self.ensure_one()
        if not move or not other_charges:
            return False

        field_name, field = self._cr_get_move_other_charges_field(move)
        if not field_name:
            self._logger.info("No se encontró campo compatible de OtrosCargos en account.move para orden POS %s.", self.id)
            return False

        value = other_charges
        if field.type in ("char", "text", "html"):
            value = json.dumps(other_charges, ensure_ascii=False)

        try:
            move[field_name] = value
            return True
        except Exception:  # noqa: BLE001
            self._logger.warning(
                "No se pudo asignar OtrosCargos al campo %s en move virtual para orden POS %s.",
                field_name,
                self.id,
            )
            return False

    def _cr_build_virtual_move(self, *, document_type, consecutivo, clave):
        """Build a non-persisted account.move that reuses l10n_cr_einvoice XML generator for POS data."""
        self.ensure_one()
        company = self.company_id
        journal = self.env["account.journal"].with_company(company).search(
            [("type", "=", "sale"), ("company_id", "=", company.id)], limit=1
        )
        is_credit_note = (document_type or "").lower() == "nc" or self.amount_total < 0
        tip_line_ids = self._cr_get_tip_line_ids()
        line_commands = []
        for line in self.lines:
            if line.id in tip_line_ids:
                # Native tip lines are emitted as FE OtrosCargos (code 06), not DetalleServicio.
                continue
            line_quantity = abs(line.qty) if is_credit_note else line.qty
            line_commands.append(
                (
                    0,
                    0,
                    {
                        "product_id": line.product_id.id,
                        "name": line.full_product_name or line.product_id.display_name,
                        "quantity": line_quantity,
                        "price_unit": line.price_unit,
                        "discount": line.discount,
                        "tax_ids": [(6, 0, line.tax_ids_after_fiscal_position.ids)],
                        "product_uom_id": line.product_uom_id.id if line.product_uom_id else False,
                    },
                )
            )

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
        self._cr_set_other_charges_on_virtual_move(move, self._cr_get_other_charges_payload())
        # Asegura cálculo de totales para XML.
        move._compute_amount()
        return move

    def _cr_store_hacienda_response_attachment(self, response_data, *, clave, consecutivo=None):
        """Store Hacienda response XML as an attachment (idempotent)."""
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

        doc_prefix = {"te": "TE", "fe": "FE", "nc": "NC"}.get(
            (self.cr_fe_document_type or self._cr_get_pos_document_type() or "").lower(), "DOC"
        )
        file_consecutivo = consecutivo or self.cr_fe_consecutivo or clave
        expected_name = f"{doc_prefix}-{file_consecutivo}-respuesta-hacienda.xml"

        if self.cr_fe_response_attachment_id and self.cr_fe_response_attachment_id.datas:
            return self.cr_fe_response_attachment_id.id

        existing = (
            self.env["ir.attachment"]
            .sudo()
            .search(
                [
                    ("res_model", "=", "pos.order"),
                    ("res_id", "=", self.id),
                    ("mimetype", "=", "application/xml"),
                    ("name", "=", expected_name),
                ],
                order="id desc",
                limit=1,
            )
        )
        if existing and existing.datas:
            self.cr_fe_response_attachment_id = existing.id
            return existing.id

        attachment = self.env["ir.attachment"].create(
            {
                "name": expected_name,
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
        if self._cr_requires_account_move_flow():
            return False
        if self.cr_fe_status not in ("pending", "error_retry") and not force:
            return False
        try:
            if not self.cr_fe_xml_attachment_id:
                self._cr_prepare_te_document()
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
                prefer_local=(self.cr_fe_document_type or self._cr_get_pos_document_type()) == "nc",
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
            if self._cr_should_delay_credit_note_xml():
                self.write(
                    {
                        "cr_fe_status": "error_retry",
                        "cr_fe_error_code": "reference_pending",
                        "cr_fe_last_error": self._cr_build_reference_pending_message(),
                        "cr_fe_next_try": fields.Datetime.now() + timedelta(minutes=5),
                    }
                )
                return False
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
        if self._cr_requires_account_move_flow():
            return False

        status = False
        try:
            status = self._cr_call_service_method(
                ["consult_status", "check_status_from_pos_order", "check_status", "get_pos_order_status"],
                self.id,
                idempotency_key=self.cr_fe_idempotency_key,
                prefer_local=(self.cr_fe_document_type or self._cr_get_pos_document_type()) == "nc",
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
            "|",
            ("cr_fe_next_try", "=", False),
            ("cr_fe_next_try", "<=", fields.Datetime.now()),
        ]
        orders = self.search(domain, order="cr_fe_next_try asc, id asc")
        orders = orders.filtered(lambda order: not order._cr_has_real_invoice_move() and not order._cr_requires_account_move_flow())
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
        orders = orders.filtered(lambda order: not order._cr_has_real_invoice_move() and not order._cr_requires_account_move_flow())
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
