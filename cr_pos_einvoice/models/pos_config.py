from odoo import fields, models


class PosConfig(models.Model):
    _inherit = "pos.config"

    cr_fe_enabled = fields.Boolean(
        string="Usar facturación electrónica",
        default=True,
        help="Define si este punto de venta genera y envía documentos de factura electrónica.",
    )
