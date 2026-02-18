from odoo import api, fields, models


class PosOrder(models.Model):
    _inherit = "pos.order"

    cr_fe_generated_move_ids = fields.Many2many(
        comodel_name="account.move",
        compute="_compute_cr_fe_generated_move_ids",
        string="Documentos electrónicos generados",
    )

    cr_fe_document_kind = fields.Selection(
        selection=[
            ("electronic_invoice", "Factura electrónica"),
            ("electronic_ticket", "Tiquete electrónico"),
            ("credit_note", "Nota de crédito"),
        ],
        string="Tipo de documento FE",
        default="electronic_ticket",
    )
    cr_fe_payment_method = fields.Char(string="Método de pago FE")
    cr_fe_payment_condition = fields.Char(string="Condición de pago FE")

    @staticmethod
    def _extract_ui_order_data(ui_order):
        """Normaliza el payload del POS para distintas versiones."""
        if not isinstance(ui_order, dict):
            return {}
        data = ui_order.get("data")
        return data if isinstance(data, dict) else ui_order

    def _resolve_fe_data_from_ui_order(self, ui_order):
        """Obtiene método/condición FE desde los métodos de pago usados en el POS."""
        ui_data = self._extract_ui_order_data(ui_order)
        statement_ids = ui_data.get("statement_ids") or []
        payment_method_ids = []
        for statement in statement_ids:
            if isinstance(statement, (list, tuple)) and len(statement) > 2:
                vals = statement[2] or {}
                payment_method_id = vals.get("payment_method_id")
                if payment_method_id:
                    payment_method_ids.append(payment_method_id)

        methods = self.env["pos.payment.method"].browse(payment_method_ids).exists()
        fe_method = next((m.cr_fe_payment_method for m in methods if m.cr_fe_payment_method), False)
        fe_condition = next((m.cr_fe_payment_condition for m in methods if m.cr_fe_payment_condition), False)
        return fe_method, fe_condition

    def _order_fields(self, ui_order):
        vals = super()._order_fields(ui_order)
        ui_data = self._extract_ui_order_data(ui_order)
        fe_method, fe_condition = self._resolve_fe_data_from_ui_order(ui_order)
        document_kind = (
            ui_data.get("cr_fe_document_kind")
            or ("electronic_invoice" if ui_data.get("to_invoice") else "electronic_ticket")
        )
        vals.update(
            {
                "cr_fe_document_kind": document_kind,
                "cr_fe_payment_method": ui_data.get("cr_fe_payment_method") or fe_method,
                "cr_fe_payment_condition": ui_data.get("cr_fe_payment_condition") or fe_condition,
            }
        )
        return vals

    def _prepare_invoice_vals(self):
        vals = super()._prepare_invoice_vals()
        if not self.config_id.l10n_cr_enable_einvoice_from_pos:
            return vals

        vals["invoice_origin"] = self.name
        vals["ref"] = vals.get("ref") or f"POS {self.name}"
        vals["narration"] = (
            (vals.get("narration") or "")
            + "\nDocumento generado desde Punto de Venta con l10n_cr_einvoice."
        ).strip()
        return vals

    @api.depends("account_move", "name", "pos_reference")
    def _compute_cr_fe_generated_move_ids(self):
        for order in self:
            origin_values = [value for value in [order.name, order.pos_reference] if value]
            domain = [
                ("move_type", "in", ("out_invoice", "out_refund")),
                "|",
                "|",
                ("id", "=", order.account_move.id),
                ("invoice_origin", "in", origin_values),
                ("ref", "ilike", order.name or ""),
            ]
            order.cr_fe_generated_move_ids = self.env["account.move"].search(domain)

    def _generate_pos_order_invoice(self):
        invoices = super()._generate_pos_order_invoice()

        for order in self:
            if not order.config_id.l10n_cr_enable_einvoice_from_pos:
                continue
            move = order.account_move.sudo()
            if not move:
                continue

            document_kind = (
                "credit_note" if move.move_type == "out_refund" else order.cr_fe_document_kind or "electronic_invoice"
            )
            field_mapping = {
                "l10n_cr_fe_document_kind": document_kind,
                "l10n_cr_payment_method": order.cr_fe_payment_method,
                "l10n_cr_payment_condition": order.cr_fe_payment_condition,
            }
            for field_name, value in field_mapping.items():
                if field_name in move._fields and value:
                    move[field_name] = value

            if move.state == "draft":
                move._post()

            self._trigger_fe_signature(move)

        return invoices

    @staticmethod
    def _trigger_fe_signature(move):
        """Compatibilidad para disparar firmado/envío FE según versión instalada."""
        sign_methods = (
            "action_post_sign_pos_order",
            "action_post_sign_invoices",
            "action_sign_and_send",
            "action_sign_xml",
        )
        for method_name in sign_methods:
            if hasattr(move, method_name):
                getattr(move, method_name)()
                return True

        if getattr(move, "_name", "") == "pos.order" and getattr(move, "account_move", False):
            return PosOrder._trigger_fe_signature(move.account_move.sudo())
        return False

    def _ensure_ticket_invoice_and_sign(self, pos_order_record):
        """Para tiquete electrónico, genera factura POS y dispara firmado/envío."""
        if not pos_order_record or pos_order_record.cr_fe_document_kind != "electronic_ticket":
            return

        if pos_order_record.state not in ("paid", "done", "invoiced"):
            return

        if not pos_order_record.account_move:
            pos_order_record.sudo()._generate_pos_order_invoice()
            pos_order_record.flush_recordset(["account_move"])

        if pos_order_record.account_move:
            self._trigger_fe_signature(pos_order_record.account_move.sudo())

    def _sync_fe_values_from_order_payload(self, pos_order_record, ui_order):
        """Sincroniza los valores FE enviados por el frontend al pedido POS."""
        if not pos_order_record or not pos_order_record.config_id.l10n_cr_enable_einvoice_from_pos:
            return

        ui_data = self._extract_ui_order_data(ui_order)
        fe_method, fe_condition = self._resolve_fe_data_from_ui_order(ui_order)

        payment_methods = pos_order_record.payment_ids.mapped("payment_method_id")
        if not fe_method:
            fe_method = next(
                (method.cr_fe_payment_method for method in payment_methods if method.cr_fe_payment_method),
                False,
            )
        if not fe_condition:
            fe_condition = next(
                (
                    method.cr_fe_payment_condition
                    for method in payment_methods
                    if method.cr_fe_payment_condition
                ),
                False,
            )

        document_kind = (
            ui_data.get("cr_fe_document_kind")
            or ("electronic_invoice" if ui_data.get("to_invoice") else "electronic_ticket")
        )
        vals_to_write = {
            "cr_fe_document_kind": document_kind,
            "cr_fe_payment_method": ui_data.get("cr_fe_payment_method") or fe_method,
            "cr_fe_payment_condition": ui_data.get("cr_fe_payment_condition") or fe_condition,
        }
        vals_to_write = {
            key: value
            for key, value in vals_to_write.items()
            if value is not False and value is not None
        }
        if vals_to_write:
            pos_order_record.write(vals_to_write)

    @api.model
    def create_from_ui(self, orders, draft=False):
        """Asegura que los tiquetes FE se procesen al cerrar la creación del pedido.

        En algunos flujos, `_process_order` corre antes de que el pedido quede en
        estado `paid/done`, por lo que no se genera la factura para tiquete.
        """
        pos_references_to_payload = {}
        for ui_order in orders or []:
            ui_data = self._extract_ui_order_data(ui_order)
            pos_reference = ui_data.get("name")
            if pos_reference:
                pos_references_to_payload[pos_reference] = ui_order

        order_ids = super().create_from_ui(orders, draft=draft)

        if draft or not pos_references_to_payload:
            return order_ids

        created_orders = self.search([("pos_reference", "in", list(pos_references_to_payload.keys()))])
        for order in created_orders:
            ui_order = pos_references_to_payload.get(order.pos_reference)
            if not ui_order:
                continue
            self._sync_fe_values_from_order_payload(order, ui_order)
            self._ensure_ticket_invoice_and_sign(order)
        return order_ids

    def _process_order(self, order, draft, existing_order=False, **kwargs):
        """Compatibilidad entre versiones de Odoo para el flujo POS.

        En algunas versiones ``super()._process_order`` recibe
        ``(order, draft, existing_order)`` y en otras solo
        ``(order, draft)``.
        """
        try:
            pos_order = super()._process_order(order, draft, existing_order, **kwargs)
        except TypeError:
            pos_order = super()._process_order(order, draft, **kwargs)

        if not pos_order:
            return pos_order

        pos_order_record = (
            self.browse(pos_order).exists() if isinstance(pos_order, int) else pos_order.exists()
        )
        if not pos_order_record or not pos_order_record.config_id.l10n_cr_enable_einvoice_from_pos:
            return pos_order
        self._sync_fe_values_from_order_payload(pos_order_record, order)

        if pos_order_record.cr_fe_document_kind == "electronic_ticket":
            self._ensure_ticket_invoice_and_sign(pos_order_record)
        return pos_order
