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
        default="electronic_invoice",
    )
    cr_fe_payment_method = fields.Char(string="Método de pago FE")
    cr_fe_payment_condition = fields.Char(string="Condición de pago FE")

    def _order_fields(self, ui_order):
        vals = super()._order_fields(ui_order)
        vals.update(
            {
                "cr_fe_document_kind": ui_order.get("cr_fe_document_kind") or "electronic_invoice",
                "cr_fe_payment_method": ui_order.get("cr_fe_payment_method") or False,
                "cr_fe_payment_condition": ui_order.get("cr_fe_payment_condition") or False,
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
                "l10n_cr_fe_document_kind": order.cr_fe_document_kind,
                "l10n_cr_payment_method": order.cr_fe_payment_method,
                "l10n_cr_payment_condition": order.cr_fe_payment_condition,
            }
            for field_name, value in field_mapping.items():
                if field_name in move._fields and value:
                    move[field_name] = value

            if order.cr_fe_document_kind == "credit_note" and "move_type" in move._fields:
                move.move_type = "out_refund"

            if move.state == "draft":
                move._post()

            if hasattr(move, "action_post_sign_invoices"):
                move.action_post_sign_invoices()

        return invoices
