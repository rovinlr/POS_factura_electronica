from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class AccountMove(models.Model):
    _inherit = "account.move"

    cr_pos_order_id = fields.Many2one("pos.order", string="Pedido POS FE", index=True, copy=False)
    cr_pos_document_type = fields.Selection(
        [("te", "Tiquete Electrónico"), ("fe", "Factura Electrónica")],
        string="Documento FE POS",
        copy=False,
    )
    cr_pos_fe_state = fields.Selection(
        [
            ("not_applicable", "No aplica"),
            ("to_send", "Pendiente de envío"),
            ("sending", "Enviando"),
            ("sent", "Enviado"),
            ("complete", "Completo"),
            ("accepted", "Aceptado"),
            ("rejected", "Rechazado"),
            ("error", "Con error"),
        ],
        default="not_applicable",
        string="Estado FE POS",
        copy=False,
        tracking=True,
    )
    cr_pos_fe_retry_count = fields.Integer(string="Reintentos FE POS", default=0, copy=False)
    cr_pos_fe_next_try = fields.Datetime(string="Próximo intento FE POS", copy=False)
    cr_pos_fe_last_error = fields.Text(string="Último error FE POS", copy=False)
    cr_pos_fe_last_send_date = fields.Datetime(string="Último envío FE POS", copy=False)

    def action_cr_pos_send_hacienda(self):
        for move in self:
            move._cr_pos_enqueue_for_send(force=True)
            move._cr_pos_send_to_hacienda()
        return True

    def action_cr_pos_check_hacienda_status(self):
        for move in self:
            move._cr_pos_check_hacienda_status()
        return True

    def _cr_pos_enqueue_for_send(self, force=False):
        now = fields.Datetime.now()
        for move in self:
            if move.state != "posted" or move.move_type not in ("out_invoice", "out_refund"):
                continue
            if move.cr_pos_fe_state == "sent" and not force:
                continue
            move.write(
                {
                    "cr_pos_fe_state": "to_send",
                    "cr_pos_fe_next_try": now,
                    "cr_pos_fe_last_error": False,
                }
            )

    def _cr_pos_send_to_hacienda(self):
        self.ensure_one()
        if self.cr_pos_fe_state == "sent":
            return True
        if self.state != "posted" or self.move_type not in ("out_invoice", "out_refund"):
            raise UserError(_("Solo se pueden enviar facturas de cliente publicadas."))

        self.write({"cr_pos_fe_state": "sending"})
        try:
            self._cr_pos_call_send_method()
            self.write(
                {
                    "cr_pos_fe_state": "sent",
                    "cr_pos_fe_last_error": False,
                    "cr_pos_fe_last_send_date": fields.Datetime.now(),
                    "cr_pos_fe_next_try": False,
                }
            )
            self._cr_pos_sync_order_fe_data()
            return True
        except Exception as error:  # noqa: BLE001
            retries = self.cr_pos_fe_retry_count + 1
            next_try = fields.Datetime.now() + timedelta(minutes=min(60, retries * 5))
            self.write(
                {
                    "cr_pos_fe_state": "error",
                    "cr_pos_fe_retry_count": retries,
                    "cr_pos_fe_last_error": str(error),
                    "cr_pos_fe_next_try": next_try,
                }
            )
            return False

    def _cr_pos_call_send_method(self):
        self.ensure_one()
        send_methods = [
            "action_sign_and_send",
            "action_post_sign_invoices",
            "action_send_to_hacienda",
            "action_sign_xml",
            "action_post_sign_pos_order",
        ]
        for method_name in send_methods:
            if hasattr(self, method_name):
                getattr(self, method_name)()
                return
        raise UserError(_("No se encontró un método público de envío FE en l10n_cr_einvoice."))

    def _cr_pos_check_hacienda_status(self):
        self.ensure_one()
        status_methods = [
            "action_check_hacienda_status",
            "action_consult_hacienda",
            "action_get_hacienda_status",
            "action_refresh_hacienda_status",
        ]
        for method_name in status_methods:
            if hasattr(self, method_name):
                getattr(self, method_name)()
                self._cr_pos_sync_order_fe_data()
                return True
        raise UserError(_("No se encontró método público para consultar estado FE."))

    def _cr_pos_sync_order_fe_data(self):
        for move in self:
            order = move.cr_pos_order_id
            if not order:
                continue
            status = move.cr_pos_fe_state
            for candidate in ("l10n_cr_hacienda_status", "l10n_cr_state_tributacion", "l10n_cr_status", "state_tributacion"):
                if candidate in move._fields and move[candidate]:
                    status = move[candidate]
                    break

            status = order._cr_normalize_hacienda_status(status, default_status=(status == "sent"))
            clave = False
            for candidate in ("l10n_cr_clave", "l10n_cr_einvoice_key", "l10n_cr_numero_consecutivo"):
                if candidate in move._fields and move[candidate]:
                    clave = move[candidate]
                    break

            consecutivo = False
            for candidate in ("l10n_cr_numero_consecutivo", "l10n_latam_document_number"):
                if candidate in move._fields and move[candidate]:
                    consecutivo = move[candidate]
                    break
            xml_attachment = self.env["ir.attachment"].search(
                [
                    ("res_model", "=", "account.move"),
                    ("res_id", "=", move.id),
                    ("mimetype", "in", ["application/xml", "text/xml"]),
                ],
                order="id desc",
                limit=1,
            )
            order.write(
                {
                    "cr_fe_status": status,
                    "cr_fe_clave": clave,
                    "cr_fe_consecutivo": consecutivo,
                    "cr_fe_xml_attachment_id": xml_attachment.id or False,
                }
            )

    @api.model
    def _cr_einvoice_get_send_targets(self, limit=50):
        targets = list(super()._cr_einvoice_get_send_targets(limit=limit))
        pending_tickets = self.env["pos.order"]._cr_get_pending_send_ticket_targets(limit=limit)
        return targets + pending_tickets

    @api.model
    def _cr_einvoice_get_status_targets(self, limit=50):
        targets = list(super()._cr_einvoice_get_status_targets(limit=limit))
        pending_tickets = self.env["pos.order"]._cr_get_pending_status_ticket_targets(limit=limit)
        return targets + pending_tickets

    @api.model
    def _cr_einvoice_process_send_target(self, target, target_type):
        if target_type == "pos_ticket":
            return target._cr_send_ticket_from_order()
        return super()._cr_einvoice_process_send_target(target, target_type)

    @api.model
    def _cr_einvoice_process_status_target(self, target, target_type):
        if target_type == "pos_ticket":
            return target._cr_check_ticket_status_from_order()
        return super()._cr_einvoice_process_status_target(target, target_type)
