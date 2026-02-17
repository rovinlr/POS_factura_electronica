from odoo import fields, models


class PosPaymentMethod(models.Model):
    _inherit = "pos.payment.method"

    cr_fe_payment_method = fields.Selection(
        selection=[
            ("01", "01 - Efectivo"),
            ("02", "02 - Tarjeta"),
            ("03", "03 - Transferencia"),
            ("04", "04 - Crédito"),
        ],
        string="Método de pago FE",
        help="Código FE que se enviará a Hacienda para pagos realizados con este método.",
    )
    cr_fe_payment_condition = fields.Selection(
        selection=[
            ("01", "01 - Contado"),
            ("02", "02 - Crédito"),
        ],
        string="Condición de pago FE",
        default="01",
        help="Condición de venta FE por defecto al usar este método de pago en POS.",
    )
