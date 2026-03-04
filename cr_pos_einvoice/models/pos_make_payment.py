from odoo import models


class PosMakePayment(models.TransientModel):
    _inherit = "pos.make.payment"

    def _cr_get_payment_target_orders(self):
        """Resolve pos.order records targeted by the payment wizard context."""
        self.ensure_one()
        if self.env.context.get("active_model") != "pos.order":
            return self.env["pos.order"]

        order_ids = self.env.context.get("active_ids") or []
        if not order_ids and self.env.context.get("active_id"):
            order_ids = [self.env.context["active_id"]]
        return self.env["pos.order"].browse(order_ids).exists()

    def check(self):
        """Guarantee NC references are persisted before order payment finalization."""
        for wizard in self:
            orders = wizard._cr_get_payment_target_orders().filtered(lambda order: order._cr_is_credit_note_order())
            if orders:
                orders._cr_capture_reference_on_payment()

        return super().check()
