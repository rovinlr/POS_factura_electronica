import inspect

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class PosOrder(models.Model):
    _inherit = "pos.order"

    _CR_INTERNAL_ACTION_METHODS = {
        "action_cr_send_hacienda",
        "action_cr_check_hacienda_status",
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
            ("error", "Con error"),
        ],
        string="Estado FE",
        default="not_applicable",
        copy=False,
    )
    cr_fe_xml_attachment_id = fields.Many2one("ir.attachment", string="XML FE", copy=False)

    @api.depends("account_move", "state")
    def _compute_cr_fe_document_type(self):
        for order in self:
            if order.account_move:
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

    def _cr_get_target_fe_move(self):
        self.ensure_one()
        if self.account_move:
            return self.account_move
        if self.cr_ticket_move_id and self.cr_ticket_move_id.state != "cancel":
            return self.cr_ticket_move_id
        return self.env["account.move"]

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
            "l10n_cr_payment_method": self._cr_pos_payment_method_code(),
            "l10n_cr_payment_condition": self._cr_pos_payment_condition_code(),
            "l10n_cr_fe_document_kind": "electronic_ticket" if document_type == "te" else "electronic_invoice",
            "fp_document_type": "TE" if document_type == "te" else "FE",
        }
        return mapping

    def _cr_pos_payment_method_code(self):
        self.ensure_one()
        if self.config_id and not self.config_id.cr_fe_enabled:
            return False
        method = self.payment_ids.mapped("payment_method_id")[:1]
        if not method:
            return False
        if hasattr(method, "_cr_get_fe_payment_method_code"):
            return method._cr_get_fe_payment_method_code()
        return method.cr_fe_payment_method if "cr_fe_payment_method" in method._fields else False

    def _cr_pos_payment_condition_code(self):
        self.ensure_one()
        if self.config_id and not self.config_id.cr_fe_enabled:
            return False
        method = self.payment_ids.mapped("payment_method_id")[:1]
        if not method:
            return False
        if hasattr(method, "_cr_get_fe_payment_condition_code"):
            return method._cr_get_fe_payment_condition_code()
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
            if order.account_move:
                order._cr_prepare_invoice_fe_values(order.account_move)
                order.account_move._cr_pos_enqueue_for_send()
                order.cr_fe_status = order.account_move.cr_pos_fe_state
            else:
                order._cr_send_ticket_from_order()

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
        if self.state not in ("paid", "done", "invoiced"):
            return False

        self.write({"cr_fe_status": "sending"})
        try:
            self._cr_call_order_send_method()
            self._cr_sync_fe_data_from_order(default_status="sent")
            return True
        except Exception as error:  # noqa: BLE001
            self.write({"cr_fe_status": "error"})
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

        discovered = self._cr_discover_order_methods(required_keywords=("send", "hacienda"), optional_keywords=("xml", "elect", "tribut"))
        if self._cr_run_first_available_method(discovered):
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
        raise UserError(_("No se encontró método público para consultar estado FE del pedido POS."))

    def _cr_run_first_available_method(self, method_names):
        self.ensure_one()
        for method_name in method_names:
            if method_name in self._CR_INTERNAL_ACTION_METHODS:
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

    def _cr_discover_order_methods(self, required_keywords=(), optional_keywords=()):
        self.ensure_one()
        matches = []
        for method_name in dir(type(self)):
            normalized = method_name.lower()
            if not normalized.startswith(("action_", "button_")):
                continue
            if required_keywords and not all(keyword in normalized for keyword in required_keywords):
                continue
            score = sum(1 for keyword in optional_keywords if keyword in normalized)
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
        for candidate in (
            "l10n_cr_clave",
            "l10n_cr_einvoice_key",
            "l10n_cr_numero_consecutivo",
            "number_electronic",
        ):
            if candidate in self._fields and self[candidate]:
                clave = self[candidate]
                break

        xml_attachment = self.env["ir.attachment"].search(
            [
                ("res_model", "=", "pos.order"),
                ("res_id", "=", self.id),
                "|",
                ("mimetype", "in", ["application/xml", "text/xml"]),
                ("name", "ilike", ".xml"),
            ],
            order="id desc",
            limit=1,
        )

        allowed_statuses = {item[0] for item in self._fields["cr_fe_status"].selection}
        normalized_status = status if status in allowed_statuses else ("sent" if default_status else self.cr_fe_status)

        self.write(
            {
                "cr_fe_status": normalized_status,
                "cr_fe_clave": clave,
                "cr_fe_xml_attachment_id": xml_attachment.id or False,
            }
        )
