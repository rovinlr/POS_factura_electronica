import inspect
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class PosOrder(models.Model):
    _inherit = "pos.order"

    _CR_INVOICE_MOVE_TYPES = ("out_invoice", "out_refund")
    _CR_HACIENDA_STATUS_MAP = {
        "accepted": "accepted",
        "aceptado": "accepted",
        "complete": "complete",
        "completo": "complete",
        "completed": "complete",
        "rechazado": "rejected",
        "rejected": "rejected",
        "error": "error",
        "sent": "sent",
        "enviado": "sent",
        "sending": "sending",
        "enviando": "sending",
        "to_send": "to_send",
        "pendiente": "to_send",
    }

    _CR_INTERNAL_ACTION_METHODS = {
        "action_cr_send_hacienda",
        "action_cr_check_hacienda_status",
    }
    _CR_EXCLUDED_DISCOVERY_METHODS = {
        "action_archive",
        "action_unarchive",
    }

    cr_ticket_move_id = fields.Many2one("account.move", string="Movimiento FE Tiquete", copy=False, index=True)
    cr_fe_document_type = fields.Selection(
        [("te", "Tiquete Electrónico"), ("fe", "Factura Electrónica")],
        string="Tipo documento FE",
        compute="_compute_cr_fe_document_type",
        store=True,
    )
    cr_fe_clave = fields.Char(string="Clave FE", copy=False)
    cr_fe_status = fields.Selection(
        [
            ("not_applicable", "No aplica"),
            ("to_send", "Pendiente de envío"),
            ("sending", "Enviando"),
            ("sent", "Enviado"),
            ("complete", "Completo"),
            ("accepted", "Aceptado"),
            ("rejected", "Rechazado"),
            ("error", "Con error"),
        ],
        string="Estado FE",
        default="not_applicable",
        copy=False,
    )
    cr_fe_xml_attachment_id = fields.Many2one("ir.attachment", string="XML FE", copy=False)
    cr_fe_consecutivo = fields.Char(string="Consecutivo FE", copy=False)
    cr_fe_attachment_ids = fields.Many2many(
        "ir.attachment",
        string="Adjuntos FE",
        compute="_compute_cr_fe_attachment_ids",
    )
    cr_fe_retry_count = fields.Integer(string="Reintentos FE", default=0, copy=False)
    cr_fe_next_try = fields.Datetime(string="Próximo intento FE", copy=False)
    cr_fe_last_error = fields.Text(string="Último error FE", copy=False)
    cr_fe_last_send_date = fields.Datetime(string="Último envío FE", copy=False)

    @api.depends("account_move", "state")
    def _compute_cr_fe_document_type(self):
        for order in self:
            if order.account_move and order.account_move.move_type in self._CR_INVOICE_MOVE_TYPES:
                order.cr_fe_document_type = "fe"
            elif order.state in ("paid", "done", "invoiced"):
                order.cr_fe_document_type = "te"
            else:
                order.cr_fe_document_type = False

    def action_cr_open_fe_document(self):
        self.ensure_one()
        move = self._cr_get_target_fe_move()
        if not move:
            raise UserError(_("El tiquete FE se genera desde el pedido POS y no crea movimiento contable."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Documento Electrónico"),
            "res_model": "account.move",
            "view_mode": "form",
            "res_id": move.id,
            "target": "current",
        }

    def action_cr_send_hacienda(self):
        for order in self:
            order._cr_send_to_hacienda()
        return True

    def action_cr_check_hacienda_status(self):
        for order in self:
            order._cr_check_hacienda_status()
        return True

    def _compute_cr_fe_attachment_ids(self):
        attachment_model = self.env["ir.attachment"]
        for order in self:
            domains = [[("res_model", "=", "pos.order"), ("res_id", "=", order.id)]]
            for move in (order.account_move, order.cr_ticket_move_id):
                if move:
                    domains.append([("res_model", "=", "account.move"), ("res_id", "=", move.id)])

            domain = ["|"] * (len(domains) - 1)
            for item in domains:
                domain += item

            order.cr_fe_attachment_ids = attachment_model.search(domain, order="id desc")

    def _cr_get_real_invoice_move(self):
        self.ensure_one()
        move = self.account_move
        if move and move.move_type in self._CR_INVOICE_MOVE_TYPES and move.state != "cancel":
            return move
        return self.env["account.move"]

    def _cr_get_target_fe_move(self):
        self.ensure_one()
        real_invoice = self._cr_get_real_invoice_move()
        if real_invoice:
            return real_invoice
        if self.cr_ticket_move_id and self.cr_ticket_move_id.state != "cancel":
            return self.cr_ticket_move_id
        return self.env["account.move"]

    def _cr_is_ticket_applicable(self):
        self.ensure_one()
        return not bool(self._cr_get_real_invoice_move())

    def _cr_send_to_hacienda(self, force=True):
        self.ensure_one()
        move = self._cr_get_target_fe_move()
        if move:
            move._cr_pos_enqueue_for_send(force=force)
            move._cr_pos_send_to_hacienda()
            return
        self._cr_send_ticket_from_order()

    def _cr_check_hacienda_status(self):
        self.ensure_one()
        move = self._cr_get_target_fe_move()
        if move:
            move._cr_pos_check_hacienda_status()
            return
        self._cr_check_ticket_status_from_order()

    def _cr_get_fe_field_mapping(self, document_type):
        self.ensure_one()
        mapping = {
            "fp_payment_method": self._cr_pos_payment_method_code(),
            "fp_sale_condition": self._cr_pos_payment_condition_code(),
            "l10n_cr_fe_document_kind": "electronic_ticket" if document_type == "te" else "electronic_invoice",
            "fp_document_type": "TE" if document_type == "te" else "FE",
        }
        return mapping

    def _cr_get_primary_payment_method(self):
        self.ensure_one()
        if not self.payment_ids:
            return self.env["pos.payment.method"]
        payment = self.payment_ids.sorted(key=lambda p: (-abs(p.amount), p.id))[0]
        return payment.payment_method_id

    def _cr_pos_payment_method_code(self):
        self.ensure_one()
        if self.config_id and not self.config_id.cr_fe_enabled:
            return False
        method = self._cr_get_primary_payment_method()
        if not method:
            return False
        if hasattr(method, "_cr_get_fe_payment_method_code"):
            return method._cr_get_fe_payment_method_code()
        if "fp_payment_method" in method._fields:
            return method.fp_payment_method
        return method.cr_fe_payment_method if "cr_fe_payment_method" in method._fields else False

    def _cr_pos_payment_condition_code(self):
        self.ensure_one()
        if self.config_id and not self.config_id.cr_fe_enabled:
            return False
        method = self._cr_get_primary_payment_method()
        if not method:
            return False
        if hasattr(method, "_cr_get_fe_payment_condition_code"):
            return method._cr_get_fe_payment_condition_code()
        if "fp_sale_condition" in method._fields:
            return method.fp_sale_condition
        return method.cr_fe_payment_condition if "cr_fe_payment_condition" in method._fields else False

    @api.model
    def create_from_ui(self, orders, draft=False):
        order_ids = super().create_from_ui(orders, draft=draft)
        if draft:
            return order_ids

        order_records = self.browse([oid.get("id") if isinstance(oid, dict) else oid for oid in order_ids]).exists()
        for order in order_records:
            order._cr_process_after_payment()
        return order_ids

    def _process_order(self, order, draft, existing_order=False, **kwargs):
        try:
            result = super()._process_order(order, draft, existing_order, **kwargs)
        except TypeError:
            result = super()._process_order(order, draft, **kwargs)

        if draft or not result:
            return result

        pos_order = self.browse(result).exists() if isinstance(result, int) else result
        if pos_order:
            pos_order._cr_process_after_payment()
        return result

    def _cr_process_after_payment(self):
        for order in self:
            if order.state not in ("paid", "done", "invoiced"):
                continue
            if order.config_id and not order.config_id.cr_fe_enabled:
                order.cr_fe_status = "not_applicable"
                continue

            invoice = order._cr_get_real_invoice_move()
            if invoice:
                order._cr_prepare_invoice_fe_values(invoice)
                order.cr_fe_status = invoice.cr_pos_fe_state
                continue

            if order.cr_fe_status == "sent":
                continue
            order._cr_enqueue_ticket_for_send()

    def _cr_enqueue_ticket_for_send(self, force=False):
        now = fields.Datetime.now()
        for order in self:
            if not order._cr_is_ticket_applicable():
                continue
            if order.cr_fe_status == "sent" and not force:
                continue
            order.write({
                "cr_fe_status": "to_send",
                "cr_fe_next_try": now,
                "cr_fe_last_error": False,
            })

    def _cr_prepare_invoice_fe_values(self, invoice):
        vals = {
            "cr_pos_order_id": self.id,
            "cr_pos_document_type": "fe",
            "cr_pos_fe_state": "to_send",
        }
        field_mapping = self._cr_get_fe_field_mapping("fe")
        for field_name, value in field_mapping.items():
            if field_name in invoice._fields and value:
                vals[field_name] = value
        invoice.write(vals)

    def _cr_send_ticket_from_order(self):
        self.ensure_one()
        if not self._cr_is_ticket_applicable():
            raise UserError(_("La orden ya tiene factura de cliente: la FE debe emitirse únicamente desde account.move."))
        if self.state not in ("paid", "done", "invoiced"):
            return False
        if self.cr_fe_status == "sent":
            return True

        self.write({"cr_fe_status": "sending"})
        try:
            self._cr_call_order_send_method()
            self._cr_sync_fe_data_from_order(default_status="sent")
            self.write({
                "cr_fe_retry_count": 0,
                "cr_fe_last_error": False,
                "cr_fe_next_try": False,
                "cr_fe_last_send_date": fields.Datetime.now(),
            })
            return True
        except Exception as error:  # noqa: BLE001
            retries = self.cr_fe_retry_count + 1
            next_try = fields.Datetime.now() + timedelta(minutes=min(60, retries * 5))
            self.write({
                "cr_fe_status": "error",
                "cr_fe_retry_count": retries,
                "cr_fe_last_error": str(error),
                "cr_fe_next_try": next_try,
            })
            message_post = getattr(self, "message_post", False)
            if message_post:
                message_post(body=_("Error al enviar tiquete FE a Hacienda: %s") % str(error))
            return False

    def _cr_check_ticket_status_from_order(self):
        self.ensure_one()
        self._cr_call_order_status_method()
        self._cr_sync_fe_data_from_order()
        return True

    def _cr_call_order_send_method(self):
        self.ensure_one()
        send_methods = [
            "action_post_sign_pos_order",
            "action_sign_and_send",
            "action_send_to_hacienda",
            "action_sign_xml",
            "action_generate_xml",
            "action_generate_fe_xml",
            "action_create_xml",
            "action_create_electronic_document",
            "action_post_and_send",
            "action_send_xml",
        ]
        if self._cr_run_first_available_method(send_methods):
            return

        # First try: strict lookup for explicit send + hacienda methods.
        discovered = self._cr_discover_order_methods(required_keywords=("send", "hacienda"), optional_keywords=("xml", "elect", "tribut"))
        if self._cr_run_first_available_method(discovered):
            return

        # Fallback: some localizations expose only sign/xml/electronic actions.
        broad_discovered = self._cr_discover_order_methods(
            required_keywords=(),
            optional_keywords=("send", "enviar", "sign", "xml", "hacienda", "elect", "tribut"),
            excluded_keywords=("status", "estado", "check", "consult", "refresh", "get"),
        )
        if self._cr_run_first_available_method(broad_discovered):
            return
        raise UserError(_("No se encontró un método público de envío FE para pedidos POS."))

    def _cr_call_order_status_method(self):
        self.ensure_one()
        status_methods = [
            "action_check_hacienda_status",
            "action_consult_hacienda",
            "action_get_hacienda_status",
            "action_refresh_hacienda_status",
            "action_check_xml_status",
            "action_get_xml_status",
        ]
        if self._cr_run_first_available_method(status_methods):
            return

        discovered = self._cr_discover_order_methods(required_keywords=("status",), optional_keywords=("hacienda", "tribut", "elect"))
        if self._cr_run_first_available_method(discovered):
            return

        alt_discovered = self._cr_discover_order_methods(
            required_keywords=(),
            optional_keywords=("status", "estado", "consult", "hacienda", "xml", "tribut", "elect"),
            excluded_keywords=("send", "enviar", "sign", "post"),
        )
        if self._cr_run_first_available_method(alt_discovered):
            return
        raise UserError(_("No se encontró método público para consultar estado FE del pedido POS."))

    def _cr_run_first_available_method(self, method_names):
        self.ensure_one()
        for method_name in method_names:
            if method_name in self._CR_INTERNAL_ACTION_METHODS or method_name in self._CR_EXCLUDED_DISCOVERY_METHODS:
                continue
            if not hasattr(self, method_name):
                continue
            method = getattr(self, method_name)
            if not callable(method):
                continue
            if self._cr_method_accepts_no_arguments(method):
                method()
                return True
        return False

    def _cr_method_accepts_no_arguments(self, method):
        signature = inspect.signature(method)
        for parameter in signature.parameters.values():
            if parameter.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
                continue
            if parameter.default is inspect._empty:
                return False
        return True

    def _cr_discover_order_methods(self, required_keywords=(), optional_keywords=(), excluded_keywords=()):
        self.ensure_one()
        matches = []
        for method_name in dir(type(self)):
            normalized = method_name.lower()
            if method_name in self._CR_INTERNAL_ACTION_METHODS or method_name in self._CR_EXCLUDED_DISCOVERY_METHODS:
                continue
            if method_name.startswith("_"):
                continue
            if required_keywords and not all(keyword in normalized for keyword in required_keywords):
                continue
            if excluded_keywords and any(keyword in normalized for keyword in excluded_keywords):
                continue
            if not callable(getattr(self, method_name, None)):
                continue

            keyword_score = sum(1 for keyword in optional_keywords if keyword in normalized)
            if not required_keywords and optional_keywords and keyword_score == 0:
                continue

            score = keyword_score
            if normalized.startswith(("action_", "button_")):
                score += 100
            matches.append((score, method_name))

        ordered = [name for _score, name in sorted(matches, key=lambda item: (-item[0], item[1]))]
        return ordered

    def _cr_sync_fe_data_from_order(self, default_status=False):
        self.ensure_one()
        status = default_status or self.cr_fe_status
        for candidate in (
            "l10n_cr_hacienda_status",
            "l10n_cr_state_tributacion",
            "l10n_cr_status",
            "state_tributacion",
        ):
            if candidate in self._fields and self[candidate]:
                status = self[candidate]
                break

        clave = self.cr_fe_clave
        consecutivo = self.cr_fe_consecutivo
        for candidate in (
            "l10n_cr_clave",
            "l10n_cr_einvoice_key",
            "number_electronic",
        ):
            if candidate in self._fields and self[candidate]:
                clave = self[candidate]
                break

        for candidate in ("l10n_cr_numero_consecutivo", "l10n_latam_document_number"):
            if candidate in self._fields and self[candidate]:
                consecutivo = self[candidate]
                break

        xml_attachment = self._cr_find_latest_xml_attachment()

        normalized_status = self._cr_normalize_hacienda_status(status, default_status=default_status)

        self.write(
            {
                "cr_fe_status": normalized_status,
                "cr_fe_clave": clave,
                "cr_fe_consecutivo": consecutivo,
                "cr_fe_xml_attachment_id": xml_attachment.id or False,
            }
        )

    def _cr_normalize_hacienda_status(self, status, default_status=False):
        normalized = (status or "").strip().lower()
        if normalized in self._CR_HACIENDA_STATUS_MAP:
            return self._CR_HACIENDA_STATUS_MAP[normalized]

        allowed_statuses = {item[0] for item in self._fields["cr_fe_status"].selection}
        if status in allowed_statuses:
            return status
        return "sent" if default_status else self.cr_fe_status

    def _cr_find_latest_xml_attachment(self):
        self.ensure_one()
        domain_blocks = [[("res_model", "=", "pos.order"), ("res_id", "=", self.id)]]
        for move in (self.account_move, self.cr_ticket_move_id):
            if move:
                domain_blocks.append([("res_model", "=", "account.move"), ("res_id", "=", move.id)])

        relation_domain = ["|"] * (len(domain_blocks) - 1)
        for block in domain_blocks:
            relation_domain += block

        xml_domain = ["|", ("mimetype", "in", ["application/xml", "text/xml"]), ("name", "ilike", ".xml")]
        full_domain = relation_domain + xml_domain
        return self.env["ir.attachment"].search(full_domain, order="id desc", limit=1)

    @api.model
    def _cron_cr_pos_send_pending_tickets(self, limit=50):
        domain = [
            ("state", "in", ["paid", "done", "invoiced"]),
            ("cr_fe_status", "in", ["to_send", "error"]),
            "|",
            ("cr_fe_next_try", "=", False),
            ("cr_fe_next_try", "<=", fields.Datetime.now()),
        ]
        to_send = self.search(domain, limit=limit, order="cr_fe_next_try asc, id asc")
        for order in to_send:
            order._cr_send_ticket_from_order()
        return True
