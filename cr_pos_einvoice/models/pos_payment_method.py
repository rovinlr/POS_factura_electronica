from odoo import fields, models


DEFAULT_CR_FE_PAYMENT_METHODS = [
    ("01", "Efectivo"),
    ("02", "Tarjeta"),
    ("03", "Cheque"),
    ("04", "Transferencia - depósito bancario"),
    ("05", "Recaudado por terceros"),
    ("06", "SINPE Móvil"),
    ("07", "Plataforma digital"),
    ("08", "Otros"),
]

DEFAULT_CR_FE_PAYMENT_CONDITIONS = [
    ("01", "Contado"),
    ("02", "Crédito"),
]


class PosPaymentMethod(models.Model):
    _inherit = "pos.payment.method"

    cr_fe_enabled = fields.Boolean(
        string="Usa facturación electrónica",
        default=True,
        help="Indica si este método de pago se usará para documentos de factura electrónica.",
    )

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
            selection = field.selection(self.env) if callable(field.selection) else field.selection
            if selection:
                return selection
        return DEFAULT_CR_FE_PAYMENT_METHODS

    def _selection_l10n_cr_payment_condition(self):
        field = self.env["account.move"]._fields.get("l10n_cr_payment_condition")
        if field and field.selection:
            selection = field.selection(self.env) if callable(field.selection) else field.selection
            if selection:
                return selection
        return DEFAULT_CR_FE_PAYMENT_CONDITIONS
