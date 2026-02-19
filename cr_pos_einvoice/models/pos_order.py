from odoo import _, api, fields, models
from odoo.exceptions import UserError


class PosOrder(models.Model):
    _inherit = "pos.order"

    cr_ticket_move_id = fields.Many2one("account.move", string="Movimiento FE Tiquete", copy=False, index=True)
    cr_fe_document_type = fields.Selection(
        [("ticket", "Tiquete Electrónico"), ("invoice", "Factura Electrónica")],
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

    @api.depends("account_move", "cr_ticket_move_id")
    def _compute_cr_fe_document_type(self):
        for order in self:
            order.cr_fe_document_type = "invoice" if order.account_move else "ticket" if order.cr_ticket_move_id else False

    def action_cr_send_hacienda(self):
        for order in self:
            move = order._cr_get_target_fe_move(create_if_missing=True)
            if not move:
                continue
            move._cr_pos_enqueue_for_send(force=True)
            move._cr_pos_send_to_hacienda()
        return True

    def action_cr_check_hacienda_status(self):
        for order in self:
            move = order._cr_get_target_fe_move(create_if_missing=False)
            if not move:
                raise UserError(_("No hay documento FE asociado al pedido."))
            move._cr_pos_check_hacienda_status()
        return True

    def _cr_get_target_fe_move(self, create_if_missing=False):
        self.ensure_one()
        if self.account_move:
            return self.account_move
        if self.cr_ticket_move_id:
            return self.cr_ticket_move_id
        if create_if_missing and self.state in ("paid", "done", "invoiced"):
            return self._cr_create_ticket_move_from_pos_order()
        return self.env["account.move"]

    def _cr_prepare_ticket_partner(self):
        self.ensure_one()
        partner = self.partner_id
        if partner:
            return partner
        return self.company_id.partner_id

    def _cr_prepare_ticket_move_vals(self):
        self.ensure_one()
        currency = self.pricelist_id.currency_id or self.currency_id or self.company_id.currency_id
        partner = self._cr_prepare_ticket_partner()
        move_type_selection = self.env["account.move"]._fields["move_type"].selection
        selection_keys = [item[0] for item in move_type_selection] if move_type_selection else []
        move_type = "out_receipt" if "out_receipt" in selection_keys else "out_invoice"

        line_commands = []
        for line in self.lines:
            income_account = (
                line.product_id.property_account_income_id
                or line.product_id.categ_id.property_account_income_categ_id
            )
            line_commands.append(
                (
                    0,
                    0,
                    {
                        "name": line.full_product_name or line.product_id.display_name,
                        "product_id": line.product_id.id,
                        "quantity": line.qty,
                        "price_unit": line.price_unit,
                        "discount": line.discount,
                        "tax_ids": [(6, 0, line.tax_ids.ids)],
                        "account_id": income_account.id,
                    },
                )
            )

        vals = {
            "move_type": move_type,
            "partner_id": partner.id,
            "currency_id": currency.id,
            "invoice_origin": self.name,
            "invoice_payment_term_id": False,
            "invoice_user_id": self.user_id.id,
            "invoice_date": fields.Date.context_today(self),
            "invoice_line_ids": line_commands,
            "company_id": self.company_id.id,
            "ref": f"POS Ticket {self.name}",
            "cr_pos_order_id": self.id,
            "cr_pos_document_type": "ticket",
            "cr_pos_fe_state": "to_send",
        }

        field_mapping = {
            "l10n_cr_payment_method": self._cr_pos_payment_method_code(),
            "l10n_cr_payment_condition": self._cr_pos_payment_condition_code(),
            "l10n_cr_fe_document_kind": "electronic_ticket",
        }
        for field_name, value in field_mapping.items():
            if field_name in self.env["account.move"]._fields and value:
                vals[field_name] = value
        return vals

    def _cr_pos_payment_method_code(self):
        self.ensure_one()
        method = self.payment_ids.mapped("payment_method_id")[:1]
        return method.cr_fe_payment_method if method and "cr_fe_payment_method" in method._fields else False

    def _cr_pos_payment_condition_code(self):
        self.ensure_one()
        method = self.payment_ids.mapped("payment_method_id")[:1]
        return method.cr_fe_payment_condition if method and "cr_fe_payment_condition" in method._fields else False

    def _cr_create_ticket_move_from_pos_order(self):
        self.ensure_one()
        if self.cr_ticket_move_id:
            return self.cr_ticket_move_id

        existing = self.env["account.move"].search(
            [
                ("cr_pos_order_id", "=", self.id),
                ("cr_pos_document_type", "=", "ticket"),
                ("state", "!=", "cancel"),
            ],
            limit=1,
        )
        if existing:
            self.cr_ticket_move_id = existing
            return existing

        move = self.env["account.move"].with_company(self.company_id).create(self._cr_prepare_ticket_move_vals())
        move.action_post()
        self.write(
            {
                "cr_ticket_move_id": move.id,
                "cr_fe_status": "to_send",
            }
        )
        return move

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
            if order.account_move:
                order._cr_prepare_invoice_fe_values(order.account_move)
                order.account_move._cr_pos_enqueue_for_send()
                order.cr_fe_status = order.account_move.cr_pos_fe_state
            else:
                ticket_move = order._cr_create_ticket_move_from_pos_order()
                ticket_move._cr_pos_enqueue_for_send()

    def _cr_prepare_invoice_fe_values(self, invoice):
        vals = {
            "cr_pos_order_id": self.id,
            "cr_pos_document_type": "invoice",
            "cr_pos_fe_state": "to_send",
        }
        field_mapping = {
            "l10n_cr_payment_method": self._cr_pos_payment_method_code(),
            "l10n_cr_payment_condition": self._cr_pos_payment_condition_code(),
            "l10n_cr_fe_document_kind": "electronic_invoice",
        }
        for field_name, value in field_mapping.items():
            if field_name in invoice._fields and value:
                vals[field_name] = value
        invoice.write(vals)
