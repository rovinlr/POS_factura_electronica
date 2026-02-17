from odoo import fields, models


class PosConfig(models.Model):
    _inherit = "pos.config"

    l10n_cr_enable_einvoice_from_pos = fields.Boolean(
        string="Factura electrónica CR desde POS",
        help="Permite seleccionar los datos electrónicos de Costa Rica al cobrar y enviarlos a la factura.",
    )

    def _load_pos_data_fields(self, config):
        fields_list = super()._load_pos_data_fields(config)
        if "use_pricelist" not in fields_list:
            fields_list.append("use_pricelist")
        fields_list.append("l10n_cr_enable_einvoice_from_pos")
        return fields_list

    def _load_pos_data_read(self, records, config):
        loaded_records = super()._load_pos_data_read(records, config)
        for record in loaded_records:
            record.setdefault("use_pricelist", False)
        return loaded_records
