import base64
from datetime import datetime, time
from io import BytesIO

import pytz
from odoo import _, fields, models
from odoo.exceptions import ValidationError


class PosOrderFeReportWizard(models.TransientModel):
    _name = "pos.order.fe.report.wizard"
    _description = "Asistente Reporte FE POS"

    date_from = fields.Date(string="Fecha inicio", required=True, default=lambda self: fields.Date.context_today(self))
    date_to = fields.Date(string="Fecha fin", required=True, default=lambda self: fields.Date.context_today(self))
    output_format = fields.Selection(
        [("pdf", "PDF"), ("xlsx", "Excel")],
        string="Formato",
        required=True,
        default="pdf",
    )
    file_data = fields.Binary(string="Archivo", readonly=True)
    file_name = fields.Char(string="Nombre archivo", readonly=True)

    def _build_report_domain(self):
        self.ensure_one()
        # Las fechas del asistente son locales al usuario; para filtrar por
        # date_order (UTC en base de datos) convertimos los límites exactos del
        # día local usando la zona horaria configurada.
        user_tz_name = self.env.context.get("tz") or self.env.user.tz or "UTC"
        user_tz = pytz.timezone(user_tz_name)
        start_local = user_tz.localize(datetime.combine(self.date_from, time.min))
        end_local = user_tz.localize(datetime.combine(self.date_to, time.max.replace(microsecond=0)))
        start_utc = start_local.astimezone(pytz.UTC).replace(tzinfo=None)
        end_utc = end_local.astimezone(pytz.UTC).replace(tzinfo=None)

        return [
            ("state", "in", ["paid", "done", "invoiced"]),
            ("date_order", ">=", fields.Datetime.to_string(start_utc)),
            ("date_order", "<=", fields.Datetime.to_string(end_utc)),
        ]

    def _get_report_orders(self):
        self.ensure_one()
        return self.env["pos.order"].search(self._build_report_domain(), order="date_order asc, id asc")

    def _get_order_report_sign(self, order):
        """Return -1 for credit-note/refund orders so report amounts are shown as negative."""
        self.ensure_one()
        if not order:
            return 1
        if order.cr_fe_document_type == "nc" or (order.amount_total or 0.0) < 0:
            return -1
        return 1

    def _get_signed_report_amount(self, order, amount):
        self.ensure_one()
        return (amount or 0.0) * self._get_order_report_sign(order)

    def _get_report_totals(self, orders):
        self.ensure_one()
        totals = {
            "exempt": 0.0,
            "nonsubject": 0.0,
            "exonerated": 0.0,
            "taxable_1": 0.0,
            "taxable_2": 0.0,
            "taxable_4": 0.0,
            "taxable_13": 0.0,
            "tax": 0.0,
            "total": 0.0,
        }
        for order in orders:
            totals["exempt"] += self._get_signed_report_amount(order, order.cr_exempt_amount)
            totals["nonsubject"] += self._get_signed_report_amount(order, order.cr_nonsubject_amount)
            totals["exonerated"] += self._get_signed_report_amount(order, order.cr_exonerated_amount)
            totals["taxable_1"] += self._get_signed_report_amount(order, order.cr_taxable_amount_1)
            totals["taxable_2"] += self._get_signed_report_amount(order, order.cr_taxable_amount_2)
            totals["taxable_4"] += self._get_signed_report_amount(order, order.cr_taxable_amount_4)
            totals["taxable_13"] += self._get_signed_report_amount(order, order.cr_taxable_amount_13)
            totals["tax"] += self._get_signed_report_amount(order, order.amount_tax)
            totals["total"] += self._get_signed_report_amount(order, order.amount_total)
        return totals

    def action_generate_report(self):
        self.ensure_one()
        if self.date_from > self.date_to:
            raise ValidationError(_("La fecha inicio no puede ser mayor a la fecha fin."))
        if self.output_format == "xlsx":
            return self._action_generate_xlsx()
        return self.env.ref("cr_pos_einvoice.action_report_pos_order_fe_summary_wizard").report_action(self)

    def _action_generate_xlsx(self):
        self.ensure_one()
        orders = self._get_report_orders()

        output = BytesIO()
        import xlsxwriter

        workbook = xlsxwriter.Workbook(output, {"in_memory": True})
        sheet = workbook.add_worksheet(_("Reporte FE POS")[:31])

        header_format = workbook.add_format({"bold": True, "bg_color": "#D9E1F2", "border": 1})
        date_format = workbook.add_format({"num_format": "yyyy-mm-dd hh:mm", "border": 1})
        text_format = workbook.add_format({"border": 1})
        amount_format = workbook.add_format({"num_format": "#,##0.00", "border": 1})
        total_label_format = workbook.add_format({"bold": True, "bg_color": "#F2F2F2", "border": 1})
        total_amount_format = workbook.add_format({"bold": True, "bg_color": "#F2F2F2", "num_format": "#,##0.00", "border": 1})

        headers = [
            _("Fecha"),
            _("Tipo"),
            _("Documento"),
            _("Exento"),
            _("No sujeto"),
            _("Exonerado"),
            _("Gravado 1%"),
            _("Gravado 2%"),
            _("Gravado 4%"),
            _("Gravado 13%"),
            _("Importe impuesto"),
            _("Total"),
            _("Estado FE"),
        ]

        for col, header in enumerate(headers):
            sheet.write(0, col, header, header_format)

        row = 1
        totals = self._get_report_totals(orders)
        for order in orders:
            sheet.write_datetime(row, 0, fields.Datetime.from_string(order.date_order), date_format)
            sheet.write(row, 1, dict(order._fields["cr_fe_document_type"].selection).get(order.cr_fe_document_type, ""), text_format)
            sheet.write(row, 2, order.cr_fe_consecutivo or order.name or "", text_format)
            sheet.write_number(row, 3, self._get_signed_report_amount(order, order.cr_exempt_amount), amount_format)
            sheet.write_number(row, 4, self._get_signed_report_amount(order, order.cr_nonsubject_amount), amount_format)
            sheet.write_number(row, 5, self._get_signed_report_amount(order, order.cr_exonerated_amount), amount_format)
            sheet.write_number(row, 6, self._get_signed_report_amount(order, order.cr_taxable_amount_1), amount_format)
            sheet.write_number(row, 7, self._get_signed_report_amount(order, order.cr_taxable_amount_2), amount_format)
            sheet.write_number(row, 8, self._get_signed_report_amount(order, order.cr_taxable_amount_4), amount_format)
            sheet.write_number(row, 9, self._get_signed_report_amount(order, order.cr_taxable_amount_13), amount_format)
            sheet.write_number(row, 10, self._get_signed_report_amount(order, order.amount_tax), amount_format)
            sheet.write_number(row, 11, self._get_signed_report_amount(order, order.amount_total), amount_format)
            sheet.write(row, 12, dict(order._fields["cr_fe_status"].selection).get(order.cr_fe_status, ""), text_format)
            row += 1

        sheet.write(row, 0, _("Totales"), total_label_format)
        sheet.write(row, 1, "", total_label_format)
        sheet.write(row, 2, "", total_label_format)
        sheet.write_number(row, 3, totals["exempt"], total_amount_format)
        sheet.write_number(row, 4, totals["nonsubject"], total_amount_format)
        sheet.write_number(row, 5, totals["exonerated"], total_amount_format)
        sheet.write_number(row, 6, totals["taxable_1"], total_amount_format)
        sheet.write_number(row, 7, totals["taxable_2"], total_amount_format)
        sheet.write_number(row, 8, totals["taxable_4"], total_amount_format)
        sheet.write_number(row, 9, totals["taxable_13"], total_amount_format)
        sheet.write_number(row, 10, totals["tax"], total_amount_format)
        sheet.write_number(row, 11, totals["total"], total_amount_format)
        sheet.write(row, 12, "", total_label_format)

        sheet.set_column(0, 2, 22)
        sheet.set_column(3, 12, 16)

        workbook.close()
        output.seek(0)

        filename = f"reporte_fe_pos_{self.date_from}_{self.date_to}.xlsx"
        self.write({
            "file_name": filename,
            "file_data": base64.b64encode(output.read()),
        })
        return {
            "type": "ir.actions.act_url",
            "url": f"/web/content?model={self._name}&id={self.id}&field=file_data&filename_field=file_name&download=true",
            "target": "self",
        }
