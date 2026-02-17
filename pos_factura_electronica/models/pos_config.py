from odoo import fields, models


class PosConfig(models.Model):
    _inherit = "pos.config"

    l10n_cr_enable_einvoice_from_pos = fields.Boolean(
        string="Factura electrónica CR desde POS",
        help="Permite seleccionar los datos electrónicos de Costa Rica al cobrar y enviarlos a la factura.",
    )

    @classmethod
    def _load_pos_data_fields(cls, config_id):
        fields_list = super()._load_pos_data_fields(config_id)
        fields_list.append("l10n_cr_enable_einvoice_from_pos")
        return fields_list
