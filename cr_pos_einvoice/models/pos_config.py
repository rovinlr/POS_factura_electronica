from odoo import fields, models


class PosConfig(models.Model):
    _inherit = "pos.config"

    cr_fe_enabled = fields.Boolean(
        string="Usar facturación electrónica",
        default=True,
        help="Define si este punto de venta genera y envía documentos de factura electrónica.",
    )
    fp_economic_activity_id = fields.Many2one(
        "fp.economic.activity",
        string="Actividad económica FE",
        help="Actividad económica usada para documentos FE/TE originados en este POS.",
    )
