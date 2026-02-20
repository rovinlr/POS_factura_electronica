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

    fp_payment_method = fields.Selection(
        selection="_selection_fp_payment_method",
        string="CR FE Método de Pago",
        help="Código de método de pago para factura electrónica 4.4.",
    )
    fp_sale_condition = fields.Selection(
        selection="_selection_fp_sale_condition",
        string="CR FE Condición de Pago",
        default="01",
        help="Código de condición de pago para factura electrónica 4.4.",
    )
    # Backward compatibility for databases already populated with old bridge fields.
    cr_fe_payment_method = fields.Selection(related="fp_payment_method", store=True, readonly=False)
    cr_fe_payment_condition = fields.Selection(related="fp_sale_condition", store=True, readonly=False)

    def _selection_fp_payment_method(self):
        field = self.env["account.move"]._fields.get("fp_payment_method")
        if field and field.selection:
            selection = field.selection(self.env) if callable(field.selection) else field.selection
            if selection:
                return selection
        return DEFAULT_CR_FE_PAYMENT_METHODS

    def _selection_fp_sale_condition(self):
        field = self.env["account.move"]._fields.get("fp_sale_condition")
        if field and field.selection:
            selection = field.selection(self.env) if callable(field.selection) else field.selection
            if selection:
                return selection
        return DEFAULT_CR_FE_PAYMENT_CONDITIONS

    def _cr_get_fe_payment_method_code(self):
        self.ensure_one()
        return self.fp_payment_method

    def _cr_get_fe_payment_condition_code(self):
        self.ensure_one()
        return self.fp_sale_condition
