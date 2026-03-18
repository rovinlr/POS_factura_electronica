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

    cr_fe_auto_send_on_reference = fields.Boolean(
        string="Generar XML al completar referencia (NC)",
        default=True,
        help=(
            "Si una nota de crédito queda en estado 'reference_pending', al completarse la "
            "referencia (tipo/número/fecha) el sistema intentará generar el XML "
            "inmediatamente, sin esperar el cron. El envío a Hacienda queda a cargo del cron."
        ),
    )
