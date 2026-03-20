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
        """Persistir referencias NC antes y después del pago.

        En POS (incluyendo flujos con pagos en línea), el `account.move` puede
        crearse/postear dentro de `super().check()`. Capturamos referencias antes
        y re-disparamos el flujo FE al final para mantener consistencia.
        """
        for wizard in self:
            orders = wizard._cr_get_payment_target_orders().filtered(lambda o: o._cr_is_credit_note_order())
            if orders:
                orders._cr_capture_reference_on_payment()

        result = super().check()

        for wizard in self:
            paid_orders = wizard._cr_get_payment_target_orders().filtered(lambda o: o.state in ("paid", "done", "invoiced"))
            if paid_orders:
                paid_orders._cr_capture_reference_on_payment()
                paid_orders._cr_process_after_payment()

        return result
