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

    def test_build_refund_reference_values_sets_reference_fields_when_available(self):
        order = self.env["pos.order"].new({"company_id": self.env.company.id, "amount_total": -10.0})
        origin_order = self.env["pos.order"].new(
            {
                "company_id": self.env.company.id,
                "cr_fe_document_type": "te",
                "cr_fe_clave": "50601010100000000000000100001010000000001123456789",
                "date_order": fields.Datetime.now(),
            }
        )
        origin_invoice = self.env["account.move"].new(
            {
                "move_type": "out_invoice",
                "name": "FAC-001",
                "invoice_date": fields.Date.today(),
            }
        )

        with patch.object(type(order), "_cr_get_origin_order_for_refund", lambda self: origin_order), patch.object(
            type(order), "_cr_get_origin_invoice_for_refund", lambda self: origin_invoice
        ):
            values = order._cr_build_refund_reference_values()

        move_fields = self.env["account.move"]._fields
        type_candidates = [
            "fp_reference_document_type",
            "fp_reference_doc_type",
            "reference_document_type",
            "l10n_cr_reference_document_type",
        ]
        code_candidates = [
            "fp_reference_document_code",
            "fp_reference_code",
            "reference_document_code",
            "reference_code",
            "l10n_cr_reference_code",
        ]
        number_candidates = [
            "fp_reference_document_number",
            "fp_reference_number",
            "reference_document_number",
            "reference_number",
            "reversed_entry_number",
            "l10n_cr_reference_document_number",
        ]
        date_candidates = [
            "fp_reference_issue_date",
            "fp_reference_document_date",
            "fp_reference_date",
            "reference_document_date",
            "reference_date",
            "reversed_entry_date",
            "l10n_cr_reference_issue_date",
        ]
        reason_candidates = [
            "fp_reference_reason",
            "reference_reason",
            "l10n_cr_reference_reason",
        ]

        for field_name in type_candidates:
            if field_name in move_fields:
                self.assertEqual(values.get(field_name), "04")
        for field_name in code_candidates:
            if field_name in move_fields:
                self.assertEqual(values.get(field_name), "01")
        for field_name in number_candidates:
            if field_name in move_fields:
                self.assertEqual(values.get(field_name), origin_order.cr_fe_clave)
        for field_name in date_candidates:
            if field_name in move_fields:
                self.assertEqual(values.get(field_name), origin_order.date_order.date())
        for field_name in reason_candidates:
            if field_name in move_fields:
                self.assertEqual(values.get(field_name), "Devolución de mercadería")


    def test_get_origin_invoice_for_refund_uses_origin_ticket_move_when_not_invoiced(self):
        order = self.env["pos.order"].new({"company_id": self.env.company.id, "amount_total": -10.0})
        origin_order = self.env["pos.order"].new({"company_id": self.env.company.id})
        ticket_move = self.env["account.move"].new({"move_type": "out_invoice"})
        origin_order.cr_ticket_move_id = ticket_move

        with patch.object(type(order), "_cr_get_origin_order_for_refund", lambda self: origin_order):
            origin_invoice = order._cr_get_origin_invoice_for_refund()

        self.assertEqual(origin_invoice, ticket_move)

    def test_get_refund_reference_data_detects_nc_by_refunded_lines(self):
        order = self.env["pos.order"].new({"company_id": self.env.company.id, "amount_total": 10.0})
        order_line = self.env["pos.order.line"].new({"order_id": order.id})
        origin_order = self.env["pos.order"].new(
            {
                "company_id": self.env.company.id,
                "cr_fe_document_type": "te",
                "cr_fe_clave": "50601010100000000000000100001010000000001123456789",
                "date_order": fields.Datetime.now(),
            }
        )
        refunded_line = self.env["pos.order.line"].new({"order_id": origin_order.id})
        order_line.refunded_orderline_id = refunded_line
        order.lines = [order_line]

        with patch.object(type(order), "_cr_get_origin_order_for_refund", lambda self: origin_order), patch.object(
            type(order), "_cr_get_origin_invoice_for_refund", lambda self: self.env["account.move"]
        ):
            reference_data = order._cr_get_refund_reference_data()

        self.assertEqual(reference_data.get("document_type"), "04")
        self.assertEqual(reference_data.get("number"), origin_order.cr_fe_clave)

    def test_get_pos_document_type_returns_nc_for_refund_lines_before_payment(self):
        order = self.env["pos.order"].new({"company_id": self.env.company.id, "amount_total": 10.0})
        order_line = self.env["pos.order.line"].new({"order_id": order.id})
        refunded_line = self.env["pos.order.line"].new()
        order_line.refunded_orderline_id = refunded_line
        order.lines = [order_line]

        self.assertEqual(order._cr_get_pos_document_type(), "nc")

    def test_get_pos_document_type_returns_te_for_regular_positive_order(self):
        order = self.env["pos.order"].new({"company_id": self.env.company.id, "amount_total": 10.0})
        self.assertEqual(order._cr_get_pos_document_type(), "te")

    def test_get_refund_reference_data_skips_missing_optional_reference_fields(self):
        order = self.env["pos.order"].new({"company_id": self.env.company.id, "amount_total": -10.0})

        class FakeOriginOrder:
            _fields = {
                "cr_fe_clave": object(),
                "cr_fe_consecutivo": object(),
                "cr_fe_document_type": object(),
                "date_order": object(),
            }

            def sudo(self):
                return self

            def with_context(self, **kwargs):
                return self

            def read(self, fields, load=False):
                assert "fp_reference_code" not in fields
                assert "fp_reference_reason" not in fields
                return [
                    {
                        "cr_fe_clave": "50601010100000000000000100001010000000001123456789",
                        "cr_fe_consecutivo": "00100001010000000001",
                        "cr_fe_document_type": "te",
                        "date_order": fields_module.Datetime.now(),
                    }
                ]

        fields_module = fields
        fake_origin_order = FakeOriginOrder()

        with patch.object(type(order), "_cr_get_origin_order_for_refund", lambda self: fake_origin_order), patch.object(
            type(order), "_cr_get_origin_invoice_for_refund", lambda self: self.env["account.move"]
        ), patch.object(type(order), "_cr_is_credit_note_order", lambda self: True):
            reference_data = order._cr_get_refund_reference_data()

        self.assertEqual(reference_data.get("document_type"), "04")
        self.assertEqual(reference_data.get("code"), "01")
        self.assertEqual(reference_data.get("reason"), "Devolución de mercadería")

    def test_build_pos_payload_for_nc_includes_reference_aliases(self):
        order = self.env["pos.order"].new({"company_id": self.env.company.id, "amount_total": -10.0})
        reference_date = fields.Date.today()
        reference_data = {
            "document_type": "04",
            "number": "50601010100000000000000100001010000000001123456789",
            "issue_date": reference_date,
            "code": "01",
            "reason": "Devolución de mercadería",
        }

        with patch.object(type(order), "_cr_get_refund_reference_data", lambda self: reference_data):
            payload = order._cr_build_pos_payload(
                consecutivo="00100001030000000001",
                clave="50601010100000000000000100001030000000001123456789",
                document_type="nc",
            )

        self.assertEqual(payload.get("reference", {}).get("document_type"), "04")
        self.assertEqual(payload.get("reference", {}).get("number"), reference_data["number"])
        self.assertEqual(payload.get("reference", {}).get("issue_date"), fields.Date.to_string(reference_date))
        self.assertEqual(payload.get("reference_document_type"), "04")
        self.assertEqual(payload.get("reference_document_number"), reference_data["number"])
        self.assertEqual(payload.get("reference_issue_date"), fields.Date.to_string(reference_date))
        self.assertEqual(payload.get("fp_reference_document_type"), "04")
        self.assertEqual(payload.get("fp_reference_document_number"), reference_data["number"])
        self.assertEqual(payload.get("fp_reference_issue_date"), fields.Date.to_string(reference_date))

    def test_build_virtual_move_for_nc_includes_reference_fields(self):
        order = self.env["pos.order"].new({"company_id": self.env.company.id, "amount_total": -10.0})
        origin_invoice = self.env["account.move"].new({"move_type": "out_invoice", "name": "FAC-001"})
        reference_date = fields.Date.today()
        reference_values = {
            "fp_reference_document_type": "04",
            "fp_reference_code": "01",
            "fp_reference_document_number": "50601010100000000000000100001010000000001123456789",
            "fp_reference_issue_date": reference_date,
            "fp_reference_reason": "Devolución de mercadería",
        }

        with patch.object(type(order), "_cr_build_refund_reference_values", lambda self: reference_values), patch.object(
            type(order), "_cr_get_origin_invoice_for_refund", lambda self: origin_invoice
        ):
            move = order._cr_build_virtual_move(
                document_type="nc",
                consecutivo="00100001030000000001",
                clave="50601010100000000000000100001030000000001123456789",
            )

        self.assertEqual(move.move_type, "out_refund")
        for field_name, expected in reference_values.items():
            if field_name in move._fields:
                self.assertEqual(move[field_name], expected)
        if "reversed_entry_id" in move._fields:
            self.assertEqual(move.reversed_entry_id, origin_invoice)

    def test_should_delay_credit_note_xml_when_reference_is_incomplete(self):
        order = self.env["pos.order"].new({"company_id": self.env.company.id, "amount_total": -10.0})

        with patch.object(type(order), "_cr_get_refund_reference_data", lambda self: {"document_type": "04"}):
            self.assertTrue(order._cr_should_delay_credit_note_xml())

    def test_should_not_delay_credit_note_xml_when_reference_is_complete(self):
        order = self.env["pos.order"].new({"company_id": self.env.company.id, "amount_total": -10.0})
        reference_data = {
            "document_type": "04",
            "number": "50601010100000000000000100001010000000001123456789",
            "issue_date": fields.Date.today(),
            "code": "01",
            "reason": "Devolución de mercadería",
        }

        with patch.object(type(order), "_cr_get_refund_reference_data", lambda self: reference_data):
            self.assertFalse(order._cr_should_delay_credit_note_xml())

    def test_get_refund_reference_data_requires_emitted_reference_key(self):
        order = self.env["pos.order"].new({"company_id": self.env.company.id, "amount_total": -10.0})
        origin_order = self.env["pos.order"].new(
            {
                "company_id": self.env.company.id,
                "cr_fe_document_type": "te",
                "date_order": fields.Datetime.now(),
            }
        )

        with patch.object(type(order), "_cr_get_origin_order_for_refund", lambda self: origin_order), patch.object(
            type(order), "_cr_get_origin_invoice_for_refund", lambda self: self.env["account.move"]
        ):
            reference_data = order._cr_get_refund_reference_data()

        self.assertEqual(reference_data, {})

    def test_get_refund_reference_data_requires_reference_issue_date(self):
        order = self.env["pos.order"].new({"company_id": self.env.company.id, "amount_total": -10.0})
        origin_order = self.env["pos.order"].new(
            {
                "company_id": self.env.company.id,
                "cr_fe_document_type": "te",
                "cr_fe_clave": "50601010100000000000000100001010000000001123456789",
            }
        )

        with patch.object(type(order), "_cr_get_origin_order_for_refund", lambda self: origin_order), patch.object(
            type(order), "_cr_get_origin_invoice_for_refund", lambda self: self.env["account.move"]
        ):
            reference_data = order._cr_get_refund_reference_data()

        self.assertEqual(reference_data, {})
