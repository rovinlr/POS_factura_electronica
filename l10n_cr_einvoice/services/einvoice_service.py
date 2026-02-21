import base64
import json


class EInvoiceService:
    """Reusable FE service for account.move and pos.order flows."""

    FINAL_STATES = {"accepted", "rejected", "complete"}

    def __init__(self, env):
        self.env = env

    def build_payload_from_move(self, move):
        lines = []
        for line in move.invoice_line_ids:
            lines.append(
                {
                    "product_id": line.product_id.id,
                    "name": line.name,
                    "qty": line.quantity,
                    "price_unit": line.price_unit,
                    "discount": line.discount,
                    "tax_ids": line.tax_ids.ids,
                    "subtotal": line.price_subtotal,
                    "total": line.price_total,
                }
            )
        return {
            "source_model": "account.move",
            "source_id": move.id,
            "name": move.name,
            "date": str(move.invoice_date or move.date),
            "company_id": move.company_id.id,
            "partner_id": move.partner_id.id,
            "currency_id": move.currency_id.id,
            "total_untaxed": move.amount_untaxed,
            "total_tax": move.amount_tax,
            "total": move.amount_total,
            "lines": lines,
            "payments": [],
        }

    def build_payload_from_pos_order(self, order):
        """Normalize POS data into FE payload expected by the FE service.

        `pos.order` uses a different data model than `account.move`.
        This adapter converts POS lines/payments into a canonical payload so
        XML/sign/send logic can stay centralized in l10n_cr_einvoice.
        """
        lines = []
        for line in order.lines:
            lines.append(self._map_pos_line_to_fe_line(line))
        payments = []
        for payment in order.payment_ids:
            payments.append(self._map_pos_payment_to_fe_payment(payment))
        return {
            "source_model": "pos.order",
            "source_id": order.id,
            "name": order.name,
            "date": str(order.date_order),
            "company_id": order.company_id.id,
            "partner_id": order.partner_id.id,
            "currency_id": order.currency_id.id,
            "total_untaxed": order.amount_total - order.amount_tax,
            "total_tax": order.amount_tax,
            "total": order.amount_total,
            "lines": lines,
            "payments": payments,
        }

    def _map_pos_line_to_fe_line(self, line):
        """Map `pos.order.line` fields to canonical FE line fields."""
        return {
            "product_id": line.product_id.id,
            "name": line.full_product_name,
            "qty": line.qty,
            "price_unit": line.price_unit,
            "discount": line.discount,
            "tax_ids": line.tax_ids_after_fiscal_position.ids,
            "subtotal": line.price_subtotal,
            "total": line.price_subtotal_incl,
        }

    def _map_pos_payment_to_fe_payment(self, payment):
        """Map `pos.payment` fields to canonical FE payment fields."""
        method = payment.payment_method_id
        return {
            "amount": payment.amount,
            "payment_method_id": method.id,
            "fp_payment_method": getattr(method, "fp_payment_method", False),
            "fp_sale_condition": getattr(method, "fp_sale_condition", False),
        }

    def ensure_idempotency(self, record, payload):
        key = payload.get("idempotency_key")
        if key and hasattr(record, "cr_fe_idempotency_key") and record.cr_fe_idempotency_key and record.cr_fe_idempotency_key != key:
            return False, "idempotency_key_mismatch"

        clave = getattr(record, "cr_fe_clave", False)
        status = getattr(record, "cr_fe_status", False) or getattr(record, "cr_pos_fe_state", False)
        if clave or status in self.FINAL_STATES:
            return False, "already_processed"
        return True, "ok"

    def generate_xml(self, payload, doc_type):
        content = {
            "doc_type": doc_type,
            "payload": payload,
        }
        return json.dumps(content, ensure_ascii=False).encode("utf-8")

    def _json_default(self, value):
        if isinstance(value, (bytes, bytearray)):
            return value.decode("utf-8", errors="replace")
        raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")

    def sign_xml(self, xml):
        return xml

    def send_to_hacienda(self, payload, signed_xml):
        return {"status": "sent", "xml": signed_xml, "track_id": payload.get("idempotency_key")}

    def parse_hacienda_response(self, response):
        return {
            "status": response.get("status", "sent"),
            "track_id": response.get("track_id"),
        }

    def attach_xml(self, record, xml, kind="document"):
        xml_bytes = xml if isinstance(xml, (bytes, bytearray)) else str(xml).encode("utf-8")
        suffix = "document" if kind == "document" else "response"
        attachment = self.env["ir.attachment"].create(
            {
                "name": f"{record._name.replace('.', '_')}-{record.id}-{suffix}.xml",
                "res_model": record._name,
                "res_id": record.id,
                "datas": base64.b64encode(xml_bytes),
                "mimetype": "application/xml",
            }
        )
        return attachment

    def update_einvoice_fields(self, record, values_dict):
        writable = {name: value for name, value in values_dict.items() if name in record._fields}
        if writable:
            record.write(writable)

    def process_full_flow(self, record, payload, doc_type):
        allowed, reason = self.ensure_idempotency(record, payload)
        if not allowed:
            return {"ok": False, "reason": reason}

        document_xml = self.generate_xml(payload, doc_type)
        signed_xml = self.sign_xml(document_xml)
        response = self.send_to_hacienda(payload, signed_xml)
        parsed = self.parse_hacienda_response(response)

        document_attachment = self.attach_xml(record, signed_xml, kind="document")
        response_attachment = self.attach_xml(record, json.dumps(response, default=self._json_default), kind="response")
        status = parsed.get("status", "sent")
        self.update_einvoice_fields(
            record,
            {
                "cr_fe_status": status,
                "cr_pos_fe_state": status,
                "cr_fe_xml_attachment_id": document_attachment.id,
                "cr_fe_response_attachment_id": response_attachment.id,
            },
        )
        return {"ok": True, "status": status, "document_attachment": document_attachment, "response_attachment": response_attachment}
