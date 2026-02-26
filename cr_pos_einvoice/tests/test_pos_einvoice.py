from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase
from unittest.mock import patch


@tagged("post_install", "-at_install")
class TestPosEInvoice(TransactionCase):
    def test_status_normalization(self):
        order = self.env["pos.order"]
        self.assertTrue(hasattr(order, "_cr_normalize_hacienda_status"))

    def test_payload_builder_method_exists(self):
        order = self.env["pos.order"]
        self.assertTrue(hasattr(order, "_cr_build_pos_payload"))

    def test_general_customer_partner_is_minimal(self):
        company = self.env.company
        order = self.env["pos.order"].new({"company_id": company.id})
        partner = order._cr_get_general_customer_partner()

        self.assertEqual(partner.name, "Cliente general")
        self.assertFalse(partner.vat)
        self.assertFalse(partner.email)
        self.assertFalse(partner.phone)
        self.assertFalse(partner.street)
        self.assertFalse(getattr(partner, "fp_identification_type", False))

    def test_te_receptor_contains_only_nombre_when_no_customer(self):
        company = self.env.company
        if not company.vat:
            company.vat = "3101123456"

        pricelist = self.env["product.pricelist"].search(
            [("currency_id", "=", company.currency_id.id), "|", ("company_id", "=", company.id), ("company_id", "=", False)],
            limit=1,
        )
        if not pricelist:
            pricelist = self.env["product.pricelist"].create({"name": "Test", "currency_id": company.currency_id.id, "company_id": company.id})

        uom_unit = self.env.ref("uom.product_uom_unit")
        tax = self.env["account.tax"].create(
            {
                "name": "IVA 13",
                "amount": 13,
                "amount_type": "percent",
                "type_tax_use": "sale",
                "company_id": company.id,
            }
        )
        product = self.env["product.product"].create(
            {
                "name": "Producto Test",
                "uom_id": uom_unit.id,
                "uom_po_id": uom_unit.id,
                "lst_price": 100.0,
            }
        )

        order = self.env["pos.order"].new(
            {
                "company_id": company.id,
                "pricelist_id": pricelist.id,
                "date_order": fields.Datetime.now(),
                "lines": [
                    (
                        0,
                        0,
                        {
                            "product_id": product.id,
                            "qty": 2.0,
                            "price_unit": 100.0,
                            "discount": 0.0,
                            "tax_ids_after_fiscal_position": [(6, 0, tax.ids)],
                            "product_uom_id": uom_unit.id,
                        },
                    )
                ],
            }
        )

        consecutivo = "00100001040000000001"
        clave = "506010101" + "000000000000" + consecutivo + "1" + "00000001"
        move = order._cr_build_virtual_move(document_type="te", consecutivo=consecutivo, clave=clave)
        xml_text = move._fp_generate_invoice_xml(clave=clave)

        self.assertIn("<Receptor>", xml_text)
        self.assertIn("<Nombre>Cliente general</Nombre>", xml_text)

        # For TE, if receptor has no identification, generator must omit Identificacion + Ubicacion + Contacto
        self.assertNotIn("<Identificacion>", xml_text)
        self.assertNotIn("<Ubicacion>", xml_text)
        self.assertNotIn("<Telefono>", xml_text)
        self.assertNotIn("<CorreoElectronico>", xml_text)

        # Basic mapping sanity: quantity and unit price should exist somewhere in DetalleServicio.
        self.assertIn("<Cantidad>2", xml_text)
        self.assertIn("<PrecioUnitario>100", xml_text)

    def test_sync_last_consecutivo_in_einvoice_config_uses_service_method_when_available(self):
        order = self.env["pos.order"].new({"company_id": self.env.company.id})
        captured = {}

        class FakeService:
            def set_last_consecutivo_by_document_type(self, company_id=None, document_type=None, consecutivo=None):
                captured["company_id"] = company_id
                captured["document_type"] = document_type
                captured["consecutivo"] = consecutivo
                return True

        with patch.object(type(order), "_cr_service", lambda self: FakeService()):
            synced = order._cr_sync_last_consecutivo_in_einvoice_config("te", "00100001040000000099")

        self.assertTrue(synced)
        self.assertEqual(captured["company_id"], self.env.company.id)
        self.assertEqual(captured["document_type"], "TE")
        self.assertEqual(captured["consecutivo"], "99")

    def test_sync_last_consecutivo_in_einvoice_config_falls_back_to_company_field(self):
        order = self.env["pos.order"].new({"company_id": self.env.company.id})
        expected = "00100001040000000123"

        with patch.object(type(order), "_cr_service", lambda self: False):
            synced = order._cr_sync_last_consecutivo_in_einvoice_config("te", expected)

        self.assertTrue(synced)
        self.assertEqual(order.company_id.fp_consecutive_fe, "123")
