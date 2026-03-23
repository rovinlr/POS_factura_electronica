from odoo import api, fields, models


class PosOrderLine(models.Model):
    _inherit = "pos.order.line"

    cr_is_other_charge_line = fields.Boolean(
        string="Es Otros Cargos (FE)",
        help="Marca la línea como Otros Cargos para generar el bloque `OtrosCargos` en FE CR v4.4.",
        copy=False,
    )

    @api.onchange("product_id")
    def _onchange_cr_other_charge_line_from_product(self):
        for line in self:
            line.cr_is_other_charge_line = bool(line.product_id.product_tmpl_id.cr_is_other_charge_line)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._cr_apply_other_charge_flag_from_product(vals)
        return super().create(vals_list)

    def write(self, vals):
        self._cr_apply_other_charge_flag_from_product(vals)
        return super().write(vals)

    def _cr_apply_other_charge_flag_from_product(self, vals):
        """Autofill line marker when product carries the FE other-charge flag.

        Rules:
        - Respect explicit client intent if `cr_is_other_charge_line` is present.
        - Sync automatically when `product_id` is provided/changed.
        """
        if not isinstance(vals, dict) or "cr_is_other_charge_line" in vals:
            return

        product_id = vals.get("product_id")
        if isinstance(product_id, (list, tuple)):
            product_id = product_id[0] if product_id else False
        if not product_id:
            return

        product = self.env["product.product"].browse(product_id).exists()
        vals["cr_is_other_charge_line"] = bool(product.product_tmpl_id.cr_is_other_charge_line)
