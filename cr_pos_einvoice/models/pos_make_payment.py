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
        """Guarantee NC references exist before and after POS payment finalization.

        En algunos flujos (especialmente reembolsos), el posteo del `account.move`
        puede ocurrir dentro de `super().check()`. Capturamos referencias antes
        y luego las propagamos al move (si ya existe) para evitar NC sin XML.
        """
        for wizard in self:
            orders = wizard._cr_get_payment_target_orders().filtered(lambda order: order._cr_is_credit_note_order())
            if orders:
                orders._cr_capture_reference_on_payment()

        result = super().check()

        for wizard in self:
            paid_orders = wizard._cr_get_payment_target_orders().filtered(
                lambda order: order.state in ("paid", "done", "invoiced")
            )
            if not paid_orders:
                continue

            paid_orders._cr_capture_reference_on_payment()
            for order in paid_orders:
                invoice = order._cr_get_real_invoice_move()
                if invoice:
                    order._cr_apply_refund_reference_to_invoice(invoice)
                    order._cr_prepare_invoice_fe_values(invoice)

            paid_orders._cr_process_after_payment()

        return result
