from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestPosEInvoice(TransactionCase):
    def test_status_normalization(self):
        order = self.env["pos.order"]
        # sanity check for method availability from bridge
        self.assertTrue(hasattr(order, "_cr_normalize_hacienda_status"))

    def test_payload_builder_method_exists(self):
        order = self.env["pos.order"]
        self.assertTrue(hasattr(order, "_cr_build_pos_payload"))
