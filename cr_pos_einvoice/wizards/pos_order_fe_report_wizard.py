import base64
from datetime import datetime, time
from io import BytesIO

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
        start_dt = datetime.combine(self.date_from, time.min)
        end_dt = datetime.combine(self.date_to, time.max)
        return [
            ("state", "in", ["paid", "done", "invoiced"]),
            ("date_order", ">=", fields.Datetime.to_string(start_dt)),
            ("date_order", "<=", fields.Datetime.to_string(end_dt)),
        ]

    def _get_report_orders(self):
        self.ensure_one()
        return self.env["pos.order"].search(self._build_report_domain(), order="date_order asc, id asc")

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
        for order in orders:
            sheet.write_datetime(row, 0, fields.Datetime.from_string(order.date_order), date_format)
            sheet.write(row, 1, dict(order._fields["cr_fe_document_type"].selection).get(order.cr_fe_document_type, ""), text_format)
            sheet.write(row, 2, order.cr_fe_consecutivo or order.name or "", text_format)
            sheet.write_number(row, 3, order.cr_exempt_amount or 0.0, amount_format)
            sheet.write_number(row, 4, order.cr_nonsubject_amount or 0.0, amount_format)
            sheet.write_number(row, 5, order.cr_exonerated_amount or 0.0, amount_format)
            sheet.write_number(row, 6, order.cr_taxable_amount_1 or 0.0, amount_format)
            sheet.write_number(row, 7, order.cr_taxable_amount_2 or 0.0, amount_format)
            sheet.write_number(row, 8, order.cr_taxable_amount_4 or 0.0, amount_format)
            sheet.write_number(row, 9, order.cr_taxable_amount_13 or 0.0, amount_format)
            sheet.write_number(row, 10, order.amount_tax or 0.0, amount_format)
            sheet.write_number(row, 11, order.amount_total or 0.0, amount_format)
            sheet.write(row, 12, dict(order._fields["cr_fe_status"].selection).get(order.cr_fe_status, ""), text_format)
            row += 1

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
