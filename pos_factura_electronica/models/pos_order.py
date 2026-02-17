from odoo import fields, models


class PosOrder(models.Model):
    _inherit = "pos.order"

    cr_fe_document_kind = fields.Selection(
        selection=[
            ("electronic_invoice", "Factura electrónica"),
            ("electronic_ticket", "Tiquete electrónico"),
            ("credit_note", "Nota de crédito"),
        ],
        string="Tipo de documento FE",
        default="electronic_ticket",
    )
    cr_fe_payment_method = fields.Char(string="Método de pago FE")
    cr_fe_payment_condition = fields.Char(string="Condición de pago FE")

    def _resolve_fe_data_from_ui_order(self, ui_order):
        """Obtiene método/condición FE desde los métodos de pago usados en el POS."""
        statement_ids = ui_order.get("statement_ids") or []
        payment_method_ids = []
        for statement in statement_ids:
            if isinstance(statement, (list, tuple)) and len(statement) > 2:
                vals = statement[2] or {}
                payment_method_id = vals.get("payment_method_id")
                if payment_method_id:
                    payment_method_ids.append(payment_method_id)

        methods = self.env["pos.payment.method"].browse(payment_method_ids).exists()
        fe_method = next((m.cr_fe_payment_method for m in methods if m.cr_fe_payment_method), False)
        fe_condition = next((m.cr_fe_payment_condition for m in methods if m.cr_fe_payment_condition), False)
        return fe_method, fe_condition

    def _order_fields(self, ui_order):
        vals = super()._order_fields(ui_order)
        fe_method, fe_condition = self._resolve_fe_data_from_ui_order(ui_order)
        document_kind = "electronic_invoice" if ui_order.get("to_invoice") else "electronic_ticket"
        vals.update(
            {
                "cr_fe_document_kind": document_kind,
                "cr_fe_payment_method": fe_method,
                "cr_fe_payment_condition": fe_condition,
            }
        )
        return vals

    def _prepare_invoice_vals(self):
        vals = super()._prepare_invoice_vals()
        if not self.config_id.l10n_cr_enable_einvoice_from_pos:
            return vals

        vals["invoice_origin"] = self.name
        vals["ref"] = vals.get("ref") or f"POS {self.name}"
        vals["narration"] = (
            (vals.get("narration") or "")
            + "\nDocumento generado desde Punto de Venta con l10n_cr_einvoice."
        ).strip()
        return vals

    def _generate_pos_order_invoice(self):
        invoices = super()._generate_pos_order_invoice()

        for order in self:
            if not order.config_id.l10n_cr_enable_einvoice_from_pos:
                continue
            move = order.account_move
            if not move:
                continue

            field_mapping = {
                "l10n_cr_fe_document_kind": "electronic_invoice",
                "l10n_cr_payment_method": order.cr_fe_payment_method,
                "l10n_cr_payment_condition": order.cr_fe_payment_condition,
            }
            for field_name, value in field_mapping.items():
                if field_name in move._fields and value:
                    move[field_name] = value

            if move.state == "draft":
                move._post()

            if hasattr(move, "action_post_sign_invoices"):
                move.action_post_sign_invoices()

        return invoices

    def _process_order(self, order, draft, existing_order):
        pos_order = super()._process_order(order, draft, existing_order)
        if not pos_order or not pos_order.config_id.l10n_cr_enable_einvoice_from_pos:
            return pos_order

        if pos_order.cr_fe_document_kind == "electronic_ticket":
            if hasattr(pos_order, "action_post_sign_pos_order"):
                pos_order.action_post_sign_pos_order()
            elif hasattr(pos_order, "action_post_sign_invoices"):
                pos_order.action_post_sign_invoices()
        return pos_order
