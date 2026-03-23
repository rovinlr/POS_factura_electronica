from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


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
    cr_fe_auto_email_accepted_docs = fields.Boolean(
        string="Enviar por correo comprobantes aceptados",
        default=True,
        help=(
            "Si está activo, cuando Hacienda acepte un comprobante FE/TE/NC del POS y el cliente tenga "
            "correo, se enviará automáticamente un email con XML y PDF adjuntos."
        ),
    )
    cr_fe_use_pos_flow_for_invoiced_orders = fields.Boolean(
        string="Facturar POS sin crear account.move",
        default=True,
        help=(
            "Compatibilidad: en este módulo el flujo FE de órdenes POS marcadas como "
            "'Facturar' se ejecuta desde pos.order y no crea account.move automáticamente."
        ),
    )
    cr_service_charge_percent = fields.Float(
        string="% servicio (Otros Cargos)",
        default=10.0,
        digits=(16, 5),
        help=(
            "Porcentaje usado en POS para calcular la propina/servicio cuando se aplique "
            "como Otros Cargos (código 06) en FE CR v4.4."
        ),
    )
    cr_tip_product_id = fields.Many2one(
        "product.product",
        string="Producto Propina/Otros Cargos",
        compute="_compute_cr_tip_product_id",
        inverse="_inverse_cr_tip_product_id",
        help=(
            "Alias compatible para el producto de propina del POS. "
            "Recomendado: marcar este producto como 'Otros Cargos' en l10n_cr_einvoice."
        ),
    )

    def _compute_cr_tip_product_id(self):
        for config in self:
            config.cr_tip_product_id = config._cr_get_native_tip_product()

    def _inverse_cr_tip_product_id(self):
        for config in self:
            native_tip_field = config._cr_get_native_tip_field_name()
            if native_tip_field:
                config[native_tip_field] = config.cr_tip_product_id

    def _cr_get_native_tip_field_name(self):
        self.ensure_one()
        for field_name in ("pos_tip_product_id", "tip_product_id"):
            if field_name in self._fields:
                return field_name
        return False

    def _cr_get_native_tip_product(self):
        self.ensure_one()
        native_tip_field = self._cr_get_native_tip_field_name()
        if not native_tip_field:
            return self.env["product.product"]
        return self[native_tip_field]

    @api.constrains("cr_service_charge_percent")
    def _check_cr_service_charge_percent(self):
        for config in self:
            if config.cr_service_charge_percent < 0 or config.cr_service_charge_percent > 100:
                raise ValidationError(
                    _("El porcentaje de servicio debe estar entre 0 y 100 para cumplimiento FE CR.")
                )
