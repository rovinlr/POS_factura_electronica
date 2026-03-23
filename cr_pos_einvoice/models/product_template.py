from odoo import fields, models


class ProductTemplate(models.Model):
    _inherit = "product.template"

    cr_is_other_charge_line = fields.Boolean(
        string="Otros cargos (línea POS)",
        default=False,
        help="Marca el producto para que cada línea de POS se etiquete automáticamente "
        "como otro cargo al generar la Factura Electrónica de Costa Rica.",
    )
