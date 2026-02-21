from odoo import api, fields, models


class AccountMove(models.Model):
    _inherit = "account.move"

    @api.model
    def _cr_einvoice_get_send_targets(self, limit=50):
        """Extension hook for send cron targets.

        Base behavior only processes account.move records and remains unchanged.
        """
        domain = [
            ("state", "=", "posted"),
            ("move_type", "in", ["out_invoice", "out_refund"]),
            ("cr_fe_status", "in", ["to_send", "error"]),
            "|",
            ("cr_fe_next_try", "=", False),
            ("cr_fe_next_try", "<=", fields.Datetime.now()),
        ]
        moves = self.search(domain, limit=limit, order="cr_fe_next_try asc, id asc")
        return [(move, "move") for move in moves]

    @api.model
    def _cr_einvoice_get_status_targets(self, limit=50):
        """Extension hook for status cron targets.

        Base behavior only processes account.move records and remains unchanged.
        """
        domain = [
            ("state", "=", "posted"),
            ("move_type", "in", ["out_invoice", "out_refund"]),
            ("cr_fe_status", "in", ["sent"]),
            "|",
            ("cr_fe_next_try", "=", False),
            ("cr_fe_next_try", "<=", fields.Datetime.now()),
        ]
        moves = self.search(domain, limit=limit, order="cr_fe_next_try asc, id asc")
        return [(move, "move") for move in moves]

    @api.model
    def _cr_einvoice_process_send_target(self, target, target_type):
        """Process one send target. Hookable by optional modules."""
        if target_type == "move":
            return target.action_send_to_hacienda()
        return False

    @api.model
    def _cr_einvoice_process_status_target(self, target, target_type):
        """Process one status target. Hookable by optional modules."""
        if target_type == "move":
            methods = [
                "action_check_hacienda_status",
                "action_consult_hacienda",
                "action_get_hacienda_status",
                "action_refresh_hacienda_status",
            ]
            for method_name in methods:
                if hasattr(target, method_name):
                    getattr(target, method_name)()
                    break
            return True
        return False

    @api.model
    def _cron_cr_einvoice_send_pending_documents(self, limit=50):
        for target, target_type in self._cr_einvoice_get_send_targets(limit=limit):
            self._cr_einvoice_process_send_target(target, target_type)
        return True

    @api.model
    def _cron_cr_einvoice_check_pending_status(self, limit=50):
        for target, target_type in self._cr_einvoice_get_status_targets(limit=limit):
            self._cr_einvoice_process_status_target(target, target_type)
        return True
