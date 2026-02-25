import base64
from xml.etree.ElementTree import Element, SubElement, tostring


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
        """Build canonical payload + explicit TE 4.4 sections from `pos.order`."""
        lines = [self._map_pos_line_to_fe_line(line) for line in order.lines]
        payments = [self._map_pos_payment_to_fe_payment(payment) for payment in order.payment_ids]
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
            "te44": self._build_te44_payload_from_pos_order(order, lines=lines, payments=payments),
        }

    def _map_pos_line_to_fe_line(self, line):
        taxes = []
        for tax in line.tax_ids_after_fiscal_position:
            taxes.append(
                {
                    "id": tax.id,
                    "code": getattr(tax, "tax_code", False),
                    "rate": tax.amount,
                    "name": tax.name,
                }
            )
        return {
            "product_id": line.product_id.id,
            "default_code": line.product_id.default_code,
            "name": line.full_product_name,
            "qty": line.qty,
            "uom_name": line.product_uom_id.name if line.product_uom_id else "Unid",
            "price_unit": line.price_unit,
            "discount": line.discount,
            "taxes": taxes,
            "tax_ids": line.tax_ids_after_fiscal_position.ids,
            "subtotal": line.price_subtotal,
            "total": line.price_subtotal_incl,
            "total_tax": line.price_subtotal_incl - line.price_subtotal,
        }

    def _map_pos_payment_to_fe_payment(self, payment):
        method = payment.payment_method_id
        return {
            "amount": payment.amount,
            "payment_method_id": method.id,
            "fp_payment_method": getattr(method, "fp_payment_method", False),
            "fp_sale_condition": getattr(method, "fp_sale_condition", False),
            "name": method.name,
        }

    def _build_partner_payload(self, partner):
        if not partner:
            return {}
        return {
            "name": partner.name,
            "vat": partner.vat,
            "email": partner.email,
            "phone": partner.phone,
            "country_code": partner.country_id.code if partner.country_id else False,
            "state": partner.state_id.name if partner.state_id else False,
            "county": partner.county_id.name if "county_id" in partner._fields and partner.county_id else False,
            "district": partner.district_id.name if "district_id" in partner._fields and partner.district_id else False,
            "neighborhood": partner.neighborhood_id.name if "neighborhood_id" in partner._fields and partner.neighborhood_id else False,
            "street": partner.street,
        }

    def _build_company_payload(self, company):
        partner = company.partner_id
        return {
            "name": company.name,
            "vat": company.vat,
            "email": company.email,
            "phone": company.phone,
            "branch": getattr(company, "fp_branch_code", False),
            "terminal": getattr(company, "fp_terminal_code", False),
            "economic_activity": getattr(company, "fp_economic_activity_id", False).code if getattr(company, "fp_economic_activity_id", False) else False,
            "address": self._build_partner_payload(partner),
        }

    def _build_te44_payload_from_pos_order(self, order, lines=None, payments=None):
        lines = lines or []
        payments = payments or []
        detalle = []
        for index, line in enumerate(lines, start=1):
            detalle.append(
                {
                    "numero_linea": index,
                    "codigo": line.get("default_code") or line.get("product_id"),
                    "detalle": line.get("name"),
                    "cantidad": line.get("qty"),
                    "unidad_medida": line.get("uom_name") or "Unid",
                    "precio_unitario": line.get("price_unit"),
                    "monto_total": line.get("qty", 0.0) * line.get("price_unit", 0.0),
                    "monto_descuento": (line.get("qty", 0.0) * line.get("price_unit", 0.0)) - line.get("subtotal", 0.0),
                    "subtotal": line.get("subtotal"),
                    "impuesto": line.get("total_tax"),
                    "monto_total_linea": line.get("total"),
                }
            )

        sale_condition = "01"
        payment_codes = []
        for payment in payments:
            if payment.get("fp_sale_condition"):
                sale_condition = payment["fp_sale_condition"]
            payment_codes.append(payment.get("fp_payment_method") or "01")

        return {
            "documento": "TE",
            "fecha_emision": str(order.date_order),
            "emisor": self._build_company_payload(order.company_id),
            "receptor": self._build_partner_payload(order.partner_id),
            "condicion_venta": sale_condition,
            "medio_pago": payment_codes or ["01"],
            "detalle_servicio": detalle,
            "resumen_factura": {
                "codigo_moneda": order.currency_id.name,
                "tipo_cambio": 1,
                "total_serv_gravados": 0,
                "total_serv_exentos": 0,
                "total_mercancias_gravadas": order.amount_total - order.amount_tax,
                "total_mercancias_exentas": 0,
                "total_gravado": order.amount_total - order.amount_tax,
                "total_exento": 0,
                "total_venta": order.amount_total - order.amount_tax,
                "total_descuentos": sum(item.get("monto_descuento", 0.0) for item in detalle),
                "total_venta_neta": order.amount_total - order.amount_tax,
                "total_impuesto": order.amount_tax,
                "total_comprobante": order.amount_total,
            },
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
        if doc_type == "te" and payload.get("source_model") == "pos.order" and payload.get("te44"):
            return self._generate_te44_xml(payload)

        root = Element("ElectronicDocument")
        root.set("doc_type", str(doc_type or ""))
        self._append_value(root, "payload", payload)
        return tostring(root, encoding="utf-8", xml_declaration=True)

    def _generate_te44_xml(self, payload):
        te44 = payload["te44"]
        root = Element("TiqueteElectronico")
        self._append_value(root, "Clave", payload.get("clave"))
        self._append_value(root, "NumeroConsecutivo", payload.get("consecutivo"))
        self._append_value(root, "FechaEmision", te44.get("fecha_emision"))

        emisor = SubElement(root, "Emisor")
        self._append_value(emisor, "Nombre", te44.get("emisor", {}).get("name"))
        self._append_value(emisor, "Identificacion", te44.get("emisor", {}).get("vat"))
        self._append_value(emisor, "CorreoElectronico", te44.get("emisor", {}).get("email"))

        receptor_payload = te44.get("receptor")
        if receptor_payload:
            receptor = SubElement(root, "Receptor")
            self._append_value(receptor, "Nombre", receptor_payload.get("name"))
            self._append_value(receptor, "Identificacion", receptor_payload.get("vat"))
            self._append_value(receptor, "CorreoElectronico", receptor_payload.get("email"))

        self._append_value(root, "CondicionVenta", te44.get("condicion_venta"))
        for medio_pago in te44.get("medio_pago", []):
            self._append_value(root, "MedioPago", medio_pago)

        detalle_servicio = SubElement(root, "DetalleServicio")
        for line in te44.get("detalle_servicio", []):
            linea = SubElement(detalle_servicio, "LineaDetalle")
            self._append_value(linea, "NumeroLinea", line.get("numero_linea"))
            self._append_value(linea, "Codigo", line.get("codigo"))
            self._append_value(linea, "Detalle", line.get("detalle"))
            self._append_value(linea, "Cantidad", line.get("cantidad"))
            self._append_value(linea, "UnidadMedida", line.get("unidad_medida"))
            self._append_value(linea, "PrecioUnitario", line.get("precio_unitario"))
            self._append_value(linea, "MontoTotal", line.get("monto_total"))
            self._append_value(linea, "MontoDescuento", line.get("monto_descuento"))
            self._append_value(linea, "SubTotal", line.get("subtotal"))
            self._append_value(linea, "Impuesto", line.get("impuesto"))
            self._append_value(linea, "MontoTotalLinea", line.get("monto_total_linea"))

        resumen = SubElement(root, "ResumenFactura")
        for key, value in te44.get("resumen_factura", {}).items():
            self._append_value(resumen, key, value)

        return tostring(root, encoding="utf-8", xml_declaration=True)

    def _append_value(self, parent, key, value):
        tag_name = self._safe_tag_name(key)
        if isinstance(value, dict):
            node = SubElement(parent, tag_name)
            for child_key, child_value in value.items():
                self._append_value(node, child_key, child_value)
            return
        if isinstance(value, list):
            node = SubElement(parent, tag_name)
            for item in value:
                self._append_value(node, "item", item)
            return
        node = SubElement(parent, tag_name)
        node.text = "" if value in (None, False) else str(value)

    def _safe_tag_name(self, value):
        cleaned = "".join(char if (char.isalnum() or char == "_") else "_" for char in str(value or "value"))
        if not cleaned:
            return "value"
        if cleaned[0].isdigit():
            cleaned = f"n_{cleaned}"
        return cleaned

    def _json_default(self, value):
        if isinstance(value, (bytes, bytearray)):
            return value.decode("utf-8", errors="replace")
        raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")

    def sign_xml(self, xml):
        return xml

    def send_to_hacienda(self, payload, signed_xml):
        return {"status": "sent", "xml": signed_xml, "track_id": payload.get("idempotency_key")}

    def parse_hacienda_response(self, response):
        if isinstance(response, dict):
            return {
                "status": response.get("status", "sent"),
                "track_id": response.get("track_id"),
            }
        return {"status": "sent", "track_id": False}

    def _extract_xml_blob(self, content):
        if isinstance(content, (bytes, bytearray)):
            return bytes(content)
        if isinstance(content, str) and content.strip():
            return content.encode("utf-8")
        return False

    def build_document_xml(self, signed_xml, response):
        xml_blob = self._extract_xml_blob(signed_xml)
        if isinstance(response, dict):
            for key in ("document_xml", "signed_xml", "xml_document", "xml"):
                from_response = self._extract_xml_blob(response.get(key))
                if from_response:
                    return from_response
        return xml_blob or b""

    def build_hacienda_response_xml(self, response, parsed):
        if isinstance(response, (bytes, bytearray)):
            return bytes(response)
        if isinstance(response, str):
            return response.encode("utf-8")
        if isinstance(response, dict):
            for key in ("xml_response", "response_xml", "acuse_xml", "acuse"):
                xml_text = response.get(key)
                if isinstance(xml_text, (bytes, bytearray)):
                    return bytes(xml_text)
                if isinstance(xml_text, str) and xml_text.strip():
                    return xml_text.encode("utf-8")

        root = Element("HaciendaResponse")
        status_node = SubElement(root, "status")
        status_node.text = str(parsed.get("status") or "sent")
        track_node = SubElement(root, "track_id")
        track_node.text = str(parsed.get("track_id") or "")
        return tostring(root, encoding="utf-8", xml_declaration=True)

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

        document_xml_to_attach = self.build_document_xml(signed_xml, response)
        document_attachment = self.attach_xml(record, document_xml_to_attach, kind="document")
        response_xml = self.build_hacienda_response_xml(response, parsed)
        response_attachment = self.attach_xml(record, response_xml, kind="response")
        status = parsed.get("status", "sent")
        self.update_einvoice_fields(
            record,
            {
                "cr_fe_status": status,
                "cr_pos_fe_state": status,
                "cr_fe_consecutivo": payload.get("consecutivo"),
                "cr_fe_document_type": doc_type,
                "cr_fe_xml_attachment_id": document_attachment.id,
                "cr_fe_response_attachment_id": response_attachment.id,
            },
        )
        return {"ok": True, "status": status, "document_attachment": document_attachment, "response_attachment": response_attachment}
