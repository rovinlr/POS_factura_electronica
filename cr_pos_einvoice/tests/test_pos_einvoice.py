from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestPosEInvoice(TransactionCase):
    def test_status_normalization(self):
        order = self.env["pos.order"]
        # sanity check for method availability from bridge
        self.assertTrue(hasattr(order, "_cr_normalize_hacienda_status"))
