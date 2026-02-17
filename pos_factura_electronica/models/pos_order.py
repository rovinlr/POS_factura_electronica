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
            move = order.account_move
            if not move:
                continue

            field_mapping = {
                "l10n_cr_fe_document_kind": "electronic_invoice",
                "l10n_cr_payment_method": order.cr_fe_payment_method,
                "l10n_cr_payment_condition": order.cr_fe_payment_condition,
            }
            for field_name, value in field_mapping.items():
                if field_name in move._fields and value:
                    move[field_name] = value

            if move.state == "draft":
                move._post()

            if hasattr(move, "action_post_sign_invoices"):
                move.action_post_sign_invoices()

        return invoices

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

        ui_data = self._extract_ui_order_data(order)
        fe_method, fe_condition = self._resolve_fe_data_from_ui_order(order)

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
        vals_to_write = {key: value for key, value in vals_to_write.items() if value is not False and value is not None}
        if vals_to_write:
            pos_order_record.write(vals_to_write)

        if pos_order_record.cr_fe_document_kind == "electronic_ticket":
            if hasattr(pos_order_record, "action_post_sign_pos_order"):
                pos_order_record.action_post_sign_pos_order()
            elif hasattr(pos_order_record, "action_post_sign_invoices"):
                pos_order_record.action_post_sign_invoices()
        return pos_order
