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
    cr_fe_auto_email_accepted_docs = fields.Boolean(
        string="Enviar por correo TE/NC aceptados",
        default=True,
        help=(
            "Si está activo, cuando Hacienda acepte un TE o NC del POS y el cliente tenga "
            "correo, se enviará automáticamente un email con XML y PDF adjuntos."
        ),
    )

    cr_fe_to_invoice_emits_fe = fields.Boolean(
        string="POS: 'Facturar' emite FE sin factura contable",
        default=True,
        help=(
            "Si está activo, al marcar 'Facturar' en el POS NO se crea account.move. "
            "En su lugar el pedido POS emite una FE (Factura Electrónica) reutilizando el flujo "
            "de cr_pos_einvoice (XML virtual + envío/estado Hacienda)."
        ),
    )

    cr_fe_auto_email_include_fe = fields.Boolean(
        string="Enviar por correo FE aceptadas",
        default=False,
        help=(
            "Si está activo y la opción 'Enviar por correo' está habilitada, también se enviarán "
            "automáticamente las FE aceptadas (además de TE/NC)."
        ),
    )
