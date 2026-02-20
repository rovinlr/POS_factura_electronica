from odoo import fields, models


class PosPaymentMethod(models.Model):
    _inherit = "pos.payment.method"

    cr_fe_payment_method = fields.Selection(
        selection="_selection_l10n_cr_payment_method",
        string="CR FE Método de Pago",
        help="Código de método de pago para factura electrónica 4.4.",
    )
    cr_fe_payment_condition = fields.Selection(
        selection="_selection_l10n_cr_payment_condition",
        string="CR FE Condición de Pago",
        default="01",
        help="Código de condición de pago para factura electrónica 4.4.",
    )

    def _selection_l10n_cr_payment_method(self):
        field = self.env["account.move"]._fields.get("l10n_cr_payment_method")
        if field and field.selection:
            return field.selection
        return []

    def _selection_l10n_cr_payment_condition(self):
        field = self.env["account.move"]._fields.get("l10n_cr_payment_condition")
        if field and field.selection:
            return field.selection
        return []
