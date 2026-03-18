from odoo import fields
from odoo.exceptions import UserError
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


    def test_te_xml_sanitizer_removes_codigo_actividad_receptor(self):
        order = self.env["pos.order"].new({"company_id": self.env.company.id})
        xml_in = """<TiqueteElectronico xmlns="https://cdn.comprobanteselectronicos.go.cr/xml-schemas/v4.4/tiqueteElectronico"><Receptor><Nombre>Cliente general</Nombre><CodigoActividadReceptor>620100</CodigoActividadReceptor></Receptor></TiqueteElectronico>"""

        xml_out = order._cr_sanitize_ticket_receptor_activity(xml_in, document_type="te")

        self.assertNotIn("<CodigoActividadReceptor>", xml_out)
        self.assertIn("<Nombre>Cliente general</Nombre>", xml_out)

    def test_te_xml_sanitizer_keeps_codigo_actividad_receptor_for_fe(self):
        order = self.env["pos.order"].new({"company_id": self.env.company.id})
        xml_in = """<FacturaElectronica xmlns="https://cdn.comprobanteselectronicos.go.cr/xml-schemas/v4.4/facturaElectronica"><Receptor><Nombre>Cliente</Nombre><CodigoActividadReceptor>620100</CodigoActividadReceptor></Receptor></FacturaElectronica>"""

        xml_out = order._cr_sanitize_ticket_receptor_activity(xml_in, document_type="fe")

        self.assertIn("<CodigoActividadReceptor>620100</CodigoActividadReceptor>", xml_out)

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

    def test_sync_last_consecutivo_in_einvoice_config_does_not_rollback_company_counter(self):
        order = self.env["pos.order"].new({"company_id": self.env.company.id})
        order.company_id.fp_consecutive_fe = "44"

        with patch.object(type(order), "_cr_service", lambda self: False):
            synced = order._cr_sync_last_consecutivo_in_einvoice_config("te", "00100001040000000042")

        self.assertTrue(synced)
        self.assertEqual(order.company_id.fp_consecutive_fe, "44")

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
            "reference_issue_date",
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

    def test_should_send_accepted_email_only_for_te_nc_with_customer_email(self):
        partner = self.env["res.partner"].create({"name": "Cliente POS", "email": "cliente@example.com"})
        config = self.env["pos.config"].new(
            {
                "company_id": self.env.company.id,
                "cr_fe_enabled": True,
                "cr_fe_auto_email_accepted_docs": True,
            }
        )
        order = self.env["pos.order"].new({"company_id": self.env.company.id})
        order.partner_id = partner
        order.config_id = config
        order.cr_fe_status = "accepted"
        order.cr_fe_document_type = "te"
        order.cr_fe_email_sent = False
        self.assertTrue(order._cr_should_send_accepted_email())

        order.cr_fe_document_type = "fe"
        self.assertFalse(order._cr_should_send_accepted_email())

        order.cr_fe_document_type = "nc"
        order.partner_id.email = False
        self.assertFalse(order._cr_should_send_accepted_email())

    def test_get_email_attachments_includes_xml_response_and_pdf(self):
        order = self.env["pos.order"].new({"company_id": self.env.company.id, "name": "POS/001"})
        xml_attachment = self.env["ir.attachment"].create(
            {
                "name": "te.xml",
                "type": "binary",
                "datas": "PGZvbz5iYXI8L2Zvbz4=",
                "res_model": "pos.order",
                "res_id": 0,
                "mimetype": "application/xml",
            }
        )
        response_attachment = self.env["ir.attachment"].create(
            {
                "name": "respuesta.xml",
                "type": "binary",
                "datas": "PGZvbz5iYXI8L2Zvbz4=",
                "res_model": "pos.order",
                "res_id": 0,
                "mimetype": "application/xml",
            }
        )
        fake_pdf = self.env["ir.attachment"].create(
            {
                "name": "ticket.pdf",
                "type": "binary",
                "datas": "JVBERi0xLjQKJQ==",
                "res_model": "pos.order",
                "res_id": 0,
                "mimetype": "application/pdf",
            }
        )
        order.cr_fe_xml_attachment_id = xml_attachment
        order.cr_fe_response_attachment_id = response_attachment

        with patch.object(type(order), "_cr_get_or_create_pdf_attachment", lambda self: fake_pdf):
            attachments = order._cr_get_email_attachments()

        self.assertIn(xml_attachment, attachments)
        self.assertIn(response_attachment, attachments)
        self.assertIn(fake_pdf, attachments)

    def test_get_or_create_pdf_attachment_for_ticket_without_move_uses_pos_report(self):
        order = self.env["pos.order"].new({"company_id": self.env.company.id, "name": "POS/NOINV/001"})

        class FakePosReport:
            model = "pos.order"

            def _render_qweb_pdf(self, record_ids):
                self.record_ids = record_ids
                return b"%PDF-1.4\n%dummy", "application/pdf"

        fake_report = FakePosReport()
        with patch.object(type(order), "_cr_get_pdf_report_action", lambda self: fake_report):
            pdf_attachment = order._cr_get_or_create_pdf_attachment()

        self.assertTrue(pdf_attachment)
        self.assertEqual(pdf_attachment.mimetype, "application/pdf")
        self.assertEqual(pdf_attachment.res_model, "pos.order")

    def test_get_email_attachments_prefers_receipt_pdf_when_accepted(self):
        order = self.env["pos.order"].create(
            {
                "company_id": self.env.company.id,
                "name": "POS/MAIL/001",
                "cr_fe_status": "accepted",
                "cr_fe_consecutivo": "00100001010000000055",
            }
        )
        xml_attachment = self.env["ir.attachment"].create(
            {
                "name": "te.xml",
                "type": "binary",
                "datas": "PGZvbz5iYXI8L2Zvbz4=",
                "res_model": "pos.order",
                "res_id": order.id,
                "mimetype": "application/xml",
            }
        )
        order.cr_fe_xml_attachment_id = xml_attachment

        with patch.object(type(order), "_cr_render_receipt_pdf_content", lambda self: b"%PDF-mail"):
            attachments = order._cr_get_email_attachments()

        pdfs = attachments.filtered(lambda a: a.mimetype == "application/pdf" and a.res_model == "pos.order" and a.res_id == order.id)
        self.assertEqual(len(pdfs), 1)
        self.assertIn(xml_attachment, attachments)

    def test_generate_receipt_pdf_if_accepted_skips_pending(self):
        order = self.env["pos.order"].create(
            {
                "company_id": self.env.company.id,
                "name": "POS/PENDING/001",
                "cr_fe_status": "pending",
                "cr_fe_consecutivo": "00100001010000000001",
                "cr_receipt_html": "<div class='pos-receipt'>Pendiente</div>",
            }
        )
        order.cr_pos_generate_receipt_pdf_if_accepted([order.id])
        attachment = self.env["ir.attachment"].search(
            [("res_model", "=", "pos.order"), ("res_id", "=", order.id), ("mimetype", "=", "application/pdf")],
            limit=1,
        )
        self.assertFalse(attachment)

    def test_generate_receipt_pdf_if_accepted_is_idempotent(self):
        order = self.env["pos.order"].create(
            {
                "company_id": self.env.company.id,
                "name": "POS/ACCEPTED/001",
                "cr_fe_status": "accepted",
                "cr_fe_consecutivo": "00100001010000000002",
                "cr_receipt_html": "<div class='pos-receipt'>Aceptado</div>",
            }
        )
        with patch.object(type(order), "_cr_render_receipt_pdf_content", lambda self: b"%PDF-first"):
            order.cr_pos_generate_receipt_pdf_if_accepted([order.id])
        first = self.env["ir.attachment"].search(
            [("res_model", "=", "pos.order"), ("res_id", "=", order.id), ("mimetype", "=", "application/pdf")]
        )
        self.assertEqual(len(first), 1)
        first_id = first.id

        with patch.object(type(order), "_cr_render_receipt_pdf_content", lambda self: b"%PDF-second"):
            order.cr_pos_generate_receipt_pdf_if_accepted([order.id])
        second = self.env["ir.attachment"].search(
            [("res_model", "=", "pos.order"), ("res_id", "=", order.id), ("mimetype", "=", "application/pdf")]
        )
        self.assertEqual(len(second), 1)
        self.assertEqual(second.id, first_id)

    def test_generate_receipt_pdf_fallback_without_html(self):
        order = self.env["pos.order"].create(
            {
                "company_id": self.env.company.id,
                "name": "POS/FALLBACK/001",
                "cr_fe_status": "accepted",
                "cr_fe_consecutivo": "00100001010000000003",
            }
        )
        fallback = self.env["ir.attachment"].create(
            {
                "name": "legacy.pdf",
                "type": "binary",
                "datas": "JVBERi0xLjQKJUZha2U=",
                "res_model": "pos.order",
                "res_id": order.id,
                "mimetype": "application/pdf",
            }
        )
        with patch.object(type(order), "_cr_get_or_create_pdf_attachment", lambda self: fallback):
            order.cr_pos_generate_receipt_pdf_if_accepted([order.id])
        attachment = self.env["ir.attachment"].search(
            [("res_model", "=", "pos.order"), ("res_id", "=", order.id), ("mimetype", "=", "application/pdf")]
        )
        self.assertEqual(len(attachment), 1)
        self.assertEqual(attachment.id, fallback.id)

    def test_prefill_reference_from_origin_order_copies_reference_fields(self):
        RefundOrder = self.env["pos.order"]
        origin_order = RefundOrder.create(
            {
                "company_id": self.env.company.id,
                "name": "ORIGIN/REF/001",
                "cr_fe_reference_document_type": "01",
                "cr_fe_reference_document_number": "50601010100000000000000100001010000000001123456789",
                "cr_fe_reference_issue_date": fields.Date.today(),
                "cr_fe_reference_code": "01",
                "cr_fe_reference_reason": "Ajuste comercial",
            }
        )
        refund_order = RefundOrder.create(
            {
                "company_id": self.env.company.id,
                "name": "REFUND/REF/001",
                "amount_total": -10.0,
                "cr_fe_document_type": "nc",
            }
        )

        with patch.object(type(refund_order), "_cr_get_origin_order_for_refund", lambda self: origin_order):
            refund_order._cr_prefill_reference_from_origin_order()

        self.assertEqual(refund_order.cr_fe_reference_document_type, origin_order.cr_fe_reference_document_type)
        self.assertEqual(refund_order.cr_fe_reference_document_number, origin_order.cr_fe_reference_document_number)
        self.assertEqual(refund_order.cr_fe_reference_issue_date, origin_order.cr_fe_reference_issue_date)
        self.assertEqual(refund_order.cr_fe_reference_code, origin_order.cr_fe_reference_code)
        self.assertEqual(refund_order.cr_fe_reference_reason, origin_order.cr_fe_reference_reason)


    def test_prefill_reference_from_origin_order_derives_from_origin_fe_fields(self):
        RefundOrder = self.env["pos.order"]
        origin_order = RefundOrder.create(
            {
                "company_id": self.env.company.id,
                "name": "ORIGIN/REF/FE/001",
                "cr_fe_document_type": "te",
                "cr_fe_clave": "50601010100000000000000100001040000000001123456789",
                "date_order": fields.Datetime.now(),
            }
        )
        refund_order = RefundOrder.create(
            {
                "company_id": self.env.company.id,
                "name": "REFUND/REF/FE/001",
                "amount_total": -10.0,
                "cr_fe_document_type": "nc",
            }
        )

        with patch.object(type(refund_order), "_cr_get_origin_order_for_refund", lambda self: origin_order):
            refund_order._cr_prefill_reference_from_origin_order()

        self.assertEqual(refund_order.cr_fe_reference_document_type, "04")
        self.assertEqual(refund_order.cr_fe_reference_document_number, origin_order.cr_fe_clave)
        self.assertEqual(refund_order.cr_fe_reference_issue_date, origin_order.date_order.date())

    def test_prefill_reference_from_origin_order_enables_refund_reference_data(self):
        refund_order = self.env["pos.order"].new(
            {
                "company_id": self.env.company.id,
                "amount_total": -10.0,
                "cr_fe_document_type": "nc",
                "cr_fe_reference_document_type": "01",
                "cr_fe_reference_document_number": "50601010100000000000000100001010000000001123456789",
                "cr_fe_reference_issue_date": fields.Date.today(),
                "cr_fe_reference_code": "01",
                "cr_fe_reference_reason": "Ajuste comercial",
            }
        )

        reference_data = refund_order._cr_get_refund_reference_data()

        self.assertEqual(reference_data.get("document_type"), refund_order.cr_fe_reference_document_type)
        self.assertEqual(reference_data.get("number"), refund_order.cr_fe_reference_document_number)
        self.assertEqual(reference_data.get("issue_date"), refund_order.cr_fe_reference_issue_date)
        self.assertFalse(refund_order._cr_should_delay_credit_note_xml())

    def test_capture_reference_snapshot_fills_optional_reference_fields_when_required_are_present(self):
        refund_order = self.env["pos.order"].create(
            {
                "company_id": self.env.company.id,
                "name": "REFUND/OPTIONAL/001",
                "amount_total": -10.0,
                "cr_fe_document_type": "nc",
                "cr_fe_reference_document_type": "01",
                "cr_fe_reference_document_number": "50601010100000000000000100001010000000001123456789",
                "cr_fe_reference_issue_date": fields.Date.today(),
                "cr_fe_reference_code": False,
                "cr_fe_reference_reason": False,
            }
        )

        refund_order._cr_capture_reference_snapshot()

        self.assertEqual(refund_order.cr_fe_reference_code, "01")
        self.assertEqual(refund_order.cr_fe_reference_reason, "Devolución de mercadería")


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

    def test_should_not_emit_ticket_when_order_to_invoice_flag_is_true(self):
        order = self.env["pos.order"].new(
            {
                "company_id": self.env.company.id,
                "amount_total": 10.0,
                "state": "paid",
                "invoice_status": "no",
                "to_invoice": True,
            }
        )
        self.assertTrue(order._cr_is_marked_for_invoicing())
        self.assertFalse(order._cr_should_emit_ticket())

    def test_invoice_status_to_invoice_does_not_block_te_when_to_invoice_is_false(self):
        order = self.env["pos.order"].new(
            {
                "company_id": self.env.company.id,
                "amount_total": 10.0,
                "state": "paid",
                "invoice_status": "to invoice",
                "to_invoice": False,
            }
        )
        self.assertFalse(order._cr_is_marked_for_invoicing())
        self.assertTrue(order._cr_should_emit_ticket())


    def test_compute_cr_fe_document_type_marks_nc_for_refund_lines_before_payment(self):
        order = self.env["pos.order"].new({"company_id": self.env.company.id, "amount_total": 10.0, "state": "draft"})
        order_line = self.env["pos.order.line"].new({"order_id": order.id})
        refunded_line = self.env["pos.order.line"].new()
        order_line.refunded_orderline_id = refunded_line
        order.lines = [order_line]

        order._compute_cr_fe_document_type()

        self.assertEqual(order.cr_fe_document_type, "nc")

    def test_compute_fp_document_type_marks_nc_for_refund_lines_before_payment(self):
        order = self.env["pos.order"].new({"company_id": self.env.company.id, "amount_total": 10.0, "state": "draft"})
        order_line = self.env["pos.order.line"].new({"order_id": order.id})
        refunded_line = self.env["pos.order.line"].new()
        order_line.refunded_orderline_id = refunded_line
        order.lines = [order_line]

        order._compute_fp_pos_fe_fields()

        self.assertEqual(order.fp_document_type, "NC")


    def test_extract_issue_date_from_clave(self):
        order = self.env["pos.order"].new({"company_id": self.env.company.id})
        clave = "50627022600050393008700200010040000000381000000888"

        issue_date = order._cr_extract_issue_date_from_clave(clave)

        self.assertEqual(issue_date, fields.Date.from_string("2026-02-27"))

    def test_get_refund_reference_data_uses_clave_date_when_origin_date_missing(self):
        order = self.env["pos.order"].new({"company_id": self.env.company.id, "amount_total": -10.0})

        class FakeOriginOrder:
            _fields = {
                "cr_fe_clave": object(),
                "cr_fe_consecutivo": object(),
                "cr_fe_document_type": object(),
                "date_order": object(),
            }
            write_date = False
            create_date = False

            def sudo(self):
                return self

            def with_context(self, **kwargs):
                return self

            def read(self, fields, load=False):
                return [
                    {
                        "cr_fe_clave": "50627022600050393008700200010040000000381000000888",
                        "cr_fe_consecutivo": False,
                        "cr_fe_document_type": "te",
                        "date_order": False,
                    }
                ]

        fake_origin_order = FakeOriginOrder()

        with patch.object(type(order), "_cr_get_origin_order_for_refund", lambda self: fake_origin_order), patch.object(
            type(order), "_cr_get_origin_invoice_for_refund", lambda self: self.env["account.move"]
        ), patch.object(type(order), "_cr_is_credit_note_order", lambda self: True):
            reference_data = order._cr_get_refund_reference_data()

        self.assertEqual(reference_data.get("document_type"), "04")
        self.assertEqual(reference_data.get("issue_date"), fields.Date.from_string("2026-02-27"))

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


    def test_build_refund_reference_values_prioritizes_stored_pos_reference_fields(self):
        order = self.env["pos.order"].new({"company_id": self.env.company.id, "amount_total": -10.0})
        origin_order = self.env["pos.order"].new(
            {
                "company_id": self.env.company.id,
                "cr_fe_document_type": "fe",
                "cr_fe_clave": "50601010100000000000000100001010000000001111111111",
                "date_order": fields.Datetime.now(),
            }
        )
        origin_invoice = self.env["account.move"].new(
            {
                "move_type": "out_invoice",
                "name": "FAC-ORIG",
                "invoice_date": fields.Date.from_string("2026-01-05"),
            }
        )
        manual_issue_date = fields.Date.from_string("2026-02-27")
        reference_data = {
            "document_type": "04",
            "number": "50601010100000000000000100001040000000001123456789",
            "issue_date": manual_issue_date,
            "code": "02",
            "reason": "Anulación parcial",
        }

        with patch.object(type(order), "_cr_get_refund_reference_data", lambda self: reference_data), patch.object(
            type(order), "_cr_get_origin_order_for_refund", lambda self: origin_order
        ), patch.object(type(order), "_cr_get_origin_invoice_for_refund", lambda self: origin_invoice):
            values = order._cr_build_refund_reference_values()

        move_fields = self.env["account.move"]._fields
        for field_name in ("fp_reference_document_type", "reference_document_type", "l10n_cr_reference_document_type"):
            if field_name in move_fields:
                self.assertEqual(values.get(field_name), "04")
        for field_name in ("fp_reference_document_number", "reference_document_number", "l10n_cr_reference_document_number"):
            if field_name in move_fields:
                self.assertEqual(values.get(field_name), reference_data["number"])
        for field_name in ("fp_reference_issue_date", "reference_issue_date", "l10n_cr_reference_issue_date"):
            if field_name in move_fields:
                self.assertEqual(values.get(field_name), manual_issue_date)
        for field_name in ("fp_reference_code", "reference_code", "l10n_cr_reference_code"):
            if field_name in move_fields:
                self.assertEqual(values.get(field_name), "02")
        for field_name in ("fp_reference_reason", "reference_reason", "l10n_cr_reference_reason"):
            if field_name in move_fields:
                self.assertEqual(values.get(field_name), "Anulación parcial")


    def test_send_pending_te_marks_reference_pending_when_prepare_raises_usererror(self):
        order = self.env["pos.order"].new({"company_id": self.env.company.id, "cr_fe_status": "pending", "amount_total": -10.0})
        captured = {}

        def _fake_prepare(_self):
            raise UserError("missing reference")

        def _fake_write(_self, values):
            captured.update(values)
            return True

        with patch.object(type(order), "_cr_prepare_te_document", _fake_prepare), patch.object(
            type(order), "_cr_should_delay_credit_note_xml", lambda self: True
        ), patch.object(type(order), "write", _fake_write):
            sent = order._cr_send_pending_te_to_hacienda()

        self.assertFalse(sent)
        self.assertEqual(captured.get("cr_fe_status"), "error_retry")
        self.assertEqual(captured.get("cr_fe_error_code"), "reference_pending")

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

    def test_get_refund_reference_data_prioritizes_manual_pos_reference_fields(self):
        manual_issue_date = fields.Date.from_string("2026-02-27")
        order = self.env["pos.order"].new(
            {
                "company_id": self.env.company.id,
                "amount_total": -10.0,
                "cr_fe_document_type": "nc",
                "cr_fe_reference_document_type": "04",
                "cr_fe_reference_document_number": "50601010100000000000000100001040000000001123456789",
                "cr_fe_reference_issue_date": manual_issue_date,
                "cr_fe_reference_code": "02",
                "cr_fe_reference_reason": "Anulación parcial",
            }
        )

        reference_data = order._cr_get_refund_reference_data()

        self.assertEqual(reference_data.get("document_type"), "04")
        self.assertEqual(reference_data.get("number"), "50601010100000000000000100001040000000001123456789")
        self.assertEqual(reference_data.get("issue_date"), manual_issue_date)
        self.assertEqual(reference_data.get("code"), "02")
        self.assertEqual(reference_data.get("reason"), "Anulación parcial")


    def test_order_fields_derives_refund_reference_from_ui_refunded_lines(self):
        order_model = self.env["pos.order"]
        ui_order = {
            "data": {
                "name": "Refund UI 001",
                "amount_total": -100.0,
                "lines": [
                    [0, 0, {"refunded_orderline_id": 321}],
                ],
            }
        }
        fake_origin_date = fields.Datetime.from_string("2026-02-27 15:45:00")

        def _fake_line_search_read(_self, domain, fields_list, limit=0):
            self.assertEqual(domain, [("id", "in", [321])])
            self.assertIn("order_id", fields_list)
            return [{"id": 321, "order_id": [77, "ORIGIN/001"]}]

        def _fake_order_search_read(_self, domain, fields_list, limit=0):
            self.assertEqual(domain, [("id", "=", 77)])
            self.assertIn("cr_fe_clave", fields_list)
            return [
                {
                    "cr_fe_document_type": "te",
                    "cr_fe_clave": "50601010100000000000000100001040000000001123456789",
                    "date_order": fake_origin_date,
                    "cr_fe_reference_document_type": False,
                    "cr_fe_reference_document_number": False,
                    "cr_fe_reference_issue_date": False,
                    "cr_fe_reference_code": False,
                    "cr_fe_reference_reason": False,
                }
            ]

        with patch.object(type(self.env["pos.order.line"]), "search_read", _fake_line_search_read), patch.object(
            type(order_model), "search_read", _fake_order_search_read
        ):
            values = order_model._order_fields(ui_order)

        self.assertEqual(values.get("cr_fe_reference_document_type"), "04")
        self.assertEqual(values.get("cr_fe_reference_document_number"), "50601010100000000000000100001040000000001123456789")
        self.assertEqual(values.get("cr_fe_reference_issue_date"), fields.Date.from_string("2026-02-27"))
        self.assertEqual(values.get("cr_fe_reference_code"), "01")
        self.assertEqual(values.get("cr_fe_reference_reason"), "Devolución de mercadería")


    def test_order_fields_merges_partial_manual_reference_with_auto_defaults(self):
        order_model = self.env["pos.order"]
        ui_order = {
            "data": {
                "name": "Refund UI 002",
                "amount_total": -100.0,
                "lines": [
                    [0, 0, {"refunded_orderline_id": 654}],
                ],
                # Manual payload intentionally omits code/reason.
                "reference": {
                    "document_type": "04",
                    "number": "50601010100000000000000100001040000000001123456789",
                    "issue_date": "2026-02-27",
                },
            }
        }

        def _fake_line_search_read(_self, domain, fields_list, limit=0):
            return [{"id": 654, "order_id": [88, "ORIGIN/002"]}]

        def _fake_order_search_read(_self, domain, fields_list, limit=0):
            return [
                {
                    "cr_fe_document_type": "te",
                    "cr_fe_clave": "50601010100000000000000100001040000000001123456789",
                    "date_order": fields.Datetime.from_string("2026-02-27 10:00:00"),
                    "cr_fe_reference_document_type": False,
                    "cr_fe_reference_document_number": False,
                    "cr_fe_reference_issue_date": False,
                    "cr_fe_reference_code": False,
                    "cr_fe_reference_reason": False,
                }
            ]

        with patch.object(type(self.env["pos.order.line"]), "search_read", _fake_line_search_read), patch.object(
            type(order_model), "search_read", _fake_order_search_read
        ):
            values = order_model._order_fields(ui_order)

        self.assertEqual(values.get("cr_fe_reference_document_type"), "04")
        self.assertEqual(values.get("cr_fe_reference_document_number"), "50601010100000000000000100001040000000001123456789")
        self.assertEqual(values.get("cr_fe_reference_issue_date"), fields.Date.from_string("2026-02-27"))
        self.assertEqual(values.get("cr_fe_reference_code"), "01")
        self.assertEqual(values.get("cr_fe_reference_reason"), "Devolución de mercadería")


    def test_order_fields_imports_manual_reference_from_ui_payload(self):
        order_model = self.env["pos.order"]
        ui_order = {
            "data": {
                "name": "Order 001",
                "amount_total": -10.0,
                "reference": {
                    "document_type": "04",
                    "number": "50601010100000000000000100001040000000001123456789",
                    "issue_date": "2026-02-27",
                    "code": "01",
                    "reason": "Devolución de mercadería",
                },
            }
        }

        fields_vals = order_model._order_fields(ui_order)

        self.assertEqual(fields_vals.get("cr_fe_reference_document_type"), "04")
        self.assertEqual(
            fields_vals.get("cr_fe_reference_document_number"),
            "50601010100000000000000100001040000000001123456789",
        )
        self.assertEqual(fields_vals.get("cr_fe_reference_issue_date"), fields.Date.from_string("2026-02-27"))
        self.assertEqual(fields_vals.get("cr_fe_reference_code"), "01")
        self.assertEqual(fields_vals.get("cr_fe_reference_reason"), "Devolución de mercadería")

    def test_order_fields_ignores_literal_false_in_manual_reference(self):
        order_model = self.env["pos.order"]
        ui_order = {
            "data": {
                "name": "Order 002",
                "amount_total": -10.0,
                "reference": {
                    "document_type": "04",
                    "number": "50601010100000000000000100001040000000001123456789",
                    "issue_date": "2026-02-27",
                    "code": "false",
                    "reason": "false",
                },
            }
        }

        fields_vals = order_model._order_fields(ui_order)

        self.assertEqual(fields_vals.get("cr_fe_reference_document_type"), "04")
        self.assertEqual(
            fields_vals.get("cr_fe_reference_document_number"),
            "50601010100000000000000100001040000000001123456789",
        )
        self.assertEqual(fields_vals.get("cr_fe_reference_issue_date"), fields.Date.from_string("2026-02-27"))
        self.assertEqual(fields_vals.get("cr_fe_reference_code"), "01")
        self.assertEqual(fields_vals.get("cr_fe_reference_reason"), "Devolución de mercadería")

    def test_build_virtual_move_uses_positive_quantities_for_credit_notes(self):
        company = self.env.company
        pricelist = self.env["product.pricelist"].search(
            [("currency_id", "=", company.currency_id.id), "|", ("company_id", "=", company.id), ("company_id", "=", False)],
            limit=1,
        )
        if not pricelist:
            pricelist = self.env["product.pricelist"].create({"name": "Test", "currency_id": company.currency_id.id, "company_id": company.id})

        uom_unit = self.env.ref("uom.product_uom_unit")
        product = self.env["product.product"].create(
            {
                "name": "Servicio NC",
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
                            "qty": -1.0,
                            "price_unit": 100.0,
                            "discount": 0.0,
                            "tax_ids_after_fiscal_position": [(6, 0, [])],
                            "product_uom_id": uom_unit.id,
                        },
                    )
                ],
            }
        )

        move = order._cr_build_virtual_move(
            document_type="nc",
            consecutivo="00100001040000000999",
            clave="50601010100000000000000100001040000000999123456789",
        )

        self.assertEqual(move.move_type, "out_refund")
        self.assertEqual(move.invoice_line_ids[0].quantity, 1.0)

    def test_extract_other_charges_from_ui_and_payload_mapping(self):
        company = self.env.company
        pricelist = self.env["product.pricelist"].search(
            [("currency_id", "=", company.currency_id.id), "|", ("company_id", "=", company.id), ("company_id", "=", False)],
            limit=1,
        )
        if not pricelist:
            pricelist = self.env["product.pricelist"].create({"name": "Test", "currency_id": company.currency_id.id, "company_id": company.id})

        order = self.env["pos.order"].new(
            {
                "company_id": company.id,
                "pricelist_id": pricelist.id,
                "date_order": fields.Datetime.now(),
            }
        )

        ui_order = {
            "data": {
                "name": "Order 001",
                "other_charges": [
                    {"type": "02", "code": "99", "amount": 1200.5, "currency": "CRC", "description": "Flete"},
                    {"type": "03", "amount": -10},
                    {"type": "01", "amount": "invalido"},
                ],
            }
        }

        vals = self.env["pos.order"]._order_fields(ui_order)
        self.assertIn("cr_other_charges_json", vals)
        order.cr_other_charges_json = vals["cr_other_charges_json"]

        payload = order._cr_build_pos_payload(
            consecutivo="00100001040000000111",
            clave="50601010100000000000000100001040000000111123456789",
            document_type="te",
        )

        self.assertIn("other_charges", payload)
        self.assertEqual(len(payload["other_charges"]), 1)
        self.assertEqual(payload["other_charges"][0]["description"], "Flete")
        self.assertEqual(payload["other_charges"], payload["otros_cargos"])
