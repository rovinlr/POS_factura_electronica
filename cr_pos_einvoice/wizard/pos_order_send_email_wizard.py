from odoo import _, api, fields, models
from odoo.exceptions import UserError


class CrPosOrderSendEmailWizard(models.TransientModel):
    _name = "cr.pos.order.send.email.wizard"
    _description = "Enviar/reenviar comprobante electrónico desde POS Order"

    order_id = fields.Many2one("pos.order", string="Pedido POS", required=True, readonly=True)
    email_to = fields.Char(string="Para", required=True)
    email_cc = fields.Char(string="CC")
    email_bcc = fields.Char(string="BCC")
    subject = fields.Char(string="Asunto", required=True)
    body_html = fields.Html(string="Cuerpo", sanitize=False)
    attachment_ids = fields.Many2many("ir.attachment", string="Adjuntos")

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        order = self.env["pos.order"].browse(values.get("order_id") or self.env.context.get("default_order_id") or self.env.context.get("active_id")).exists()
        if not order:
            return values

        if order._cr_normalize_hacienda_status(order.cr_fe_status) != "accepted":
            raise UserError(_("Solo se permite enviar/reenviar cuando el documento está ACEPTADO por Hacienda."))

        attachments = order._cr_get_email_attachments()
        values.update(
            {
                "order_id": order.id,
                "email_to": order._cr_get_customer_email() or "",
                "subject": order._cr_get_email_subject(),
                "body_html": order._cr_get_email_body_html(),
                "attachment_ids": [(6, 0, attachments.ids)],
            }
        )
        return values

    def action_send(self):
        self.ensure_one()
        order = self.order_id.exists()
        if not order:
            raise UserError(_("El pedido POS ya no existe."))

        if order._cr_normalize_hacienda_status(order.cr_fe_status) != "accepted":
            raise UserError(_("Solo se permite enviar/reenviar cuando el documento está ACEPTADO por Hacienda."))

        email_to = (self.email_to or "").strip()
        if not email_to or "@" not in email_to:
            raise UserError(_("Debe indicar un correo válido en 'Para'."))

        attachments = self.attachment_ids
        if not attachments:
            raise UserError(_("No hay adjuntos para enviar."))

        mail = self.env["mail.mail"].sudo().create(
            {
                "subject": self.subject or order._cr_get_email_subject(),
                "email_to": email_to,
                "email_cc": (self.email_cc or "").strip(),
                "email_bcc": (self.email_bcc or "").strip(),
                "body_html": self.body_html or order._cr_get_email_body_html(),
                "auto_delete": False,
                "model": "pos.order",
                "res_id": order.id,
                "attachment_ids": [(6, 0, attachments.ids)],
            }
        )
        try:
            mail.send()
            order._cr_mark_accepted_email_sent()
            order._cr_post_fe_event(
                title=_("Correo FE enviado (manual)"),
                body=_("Se envió el comprobante al cliente: %s") % email_to,
                attachments=attachments,
            )
        except Exception as error:  # noqa: BLE001
            order._cr_set_email_delivery_error(str(error))
            raise
        return {"type": "ir.actions.act_window_close"}
