from odoo import models


class PosSession(models.Model):
    _inherit = "pos.session"

    def _loader_params_pos_config(self):
        params = super()._loader_params_pos_config()
        fields_to_load = params.setdefault("search_params", {}).setdefault("fields", [])
        for field_name in ("cr_service_charge_percent", "cr_tip_product_id"):
            if field_name not in fields_to_load:
                fields_to_load.append(field_name)
        return params
