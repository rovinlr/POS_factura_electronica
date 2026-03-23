from odoo import fields, models


class PosOrderLine(models.Model):
    _inherit = "pos.order.line"

    cr_is_other_charge_line = fields.Boolean(
        string="Es Otros Cargos (FE)",
        help="Marca la línea como Otros Cargos para generar el bloque `OtrosCargos` en FE CR v4.4.",
        copy=False,
    )
