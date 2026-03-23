/** @odoo-module */

import { patch } from "@web/core/utils/patch";
import { PosOrder } from "@point_of_sale/app/models/pos_order";

/**
 * Normalize helpers
 */
const normalizeText = (value) => {
    if (value === undefined || value === null) return null;
    const text = String(value).trim();
    return text || null;
};

const normalizeDocumentType = (value) => {
    const text = normalizeText(value);
    if (!text) return null;
    const normalized = text.toLowerCase();
    const mapping = {
        te: "te",
        fe: "fe",
        nc: "nc",
        nd: "nd",
        tiquete: "te",
        "tiquete electronico": "te",
        "tiquete electrónico": "te",
        factura: "fe",
        "factura electronica": "fe",
        "factura electrónica": "fe",
        "nota credito": "nc",
        "nota de credito": "nc",
        "nota de crédito": "nc",
        "nota debito": "nd",
        "nota de debito": "nd",
        "nota de débito": "nd",
    };
    return mapping[normalized] || normalized;
};

const normalizeDate = (value) => {
    if (value === undefined || value === null || value === false) return null;
    if (typeof value === "string") {
        // Accept "YYYY-MM-DD" or "YYYY-MM-DD HH:mm:ss"
        return value.trim().slice(0, 10) || null;
    }
    if (value instanceof Date) {
        return value.toISOString().slice(0, 10);
    }
    // Luxon DateTime
    if (typeof value?.toISODate === "function") {
        return value.toISODate();
    }
    return null;
};


const pad2 = (value) => String(value).padStart(2, "0");

const formatDateDDMMYYYY = (value) => {
    if (value === undefined || value === null || value === false) return null;

    if (typeof value?.toFormat === "function") {
        const formatted = value.toFormat("dd/MM/yyyy");
        return normalizeText(formatted);
    }

    if (value instanceof Date) {
        if (Number.isNaN(value.getTime())) return null;
        return `${pad2(value.getDate())}/${pad2(value.getMonth() + 1)}/${value.getFullYear()}`;
    }

    if (typeof value === "number") {
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return null;
        return `${pad2(date.getDate())}/${pad2(date.getMonth() + 1)}/${date.getFullYear()}`;
    }

    const raw = normalizeText(value);
    if (!raw) return null;

    const isoMatch = raw.match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (isoMatch) {
        return `${isoMatch[3]}/${isoMatch[2]}/${isoMatch[1]}`;
    }

    const dmyMatch = raw.match(/^(\d{2})\/(\d{2})\/(\d{4})/);
    if (dmyMatch) {
        return `${dmyMatch[1]}/${dmyMatch[2]}/${dmyMatch[3]}`;
    }

    const mdyMatch = raw.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})/);
    if (mdyMatch) {
        return `${pad2(mdyMatch[2])}/${pad2(mdyMatch[1])}/${mdyMatch[3]}`;
    }

    const parsed = new Date(raw);
    if (!Number.isNaN(parsed.getTime())) {
        return `${pad2(parsed.getDate())}/${pad2(parsed.getMonth() + 1)}/${parsed.getFullYear()}`;
    }

    return null;
};


const formatCrAmount = (value, currencySymbol = "") => {
    const amount = Number(value || 0);
    const formatted = new Intl.NumberFormat("es-CR", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    }).format(amount);
    return currencySymbol ? `${currencySymbol} ${formatted}` : formatted;
};

const classifyTaxBucket = (tax) => {
    const name = normalizeText(tax?.name)?.toLowerCase() || "";
    const code = normalizeText(tax?.l10n_cr_tax_code || tax?.tax_code)?.toLowerCase() || "";
    if (name.includes("exoner") || code.includes("exo")) return "Exo";
    if (name.includes("exento") || code.includes("exe")) return "Exe";
    if (name.includes("no sujeto") || name.includes("no_sujeto") || code.includes("nos")) return "NoS";

    const rate = Number(tax?.amount || 0);
    if (Number.isFinite(rate)) {
        if (Math.abs(rate - 13) < 0.0001) return "13%";
        if (Math.abs(rate) < 0.0001) return "0%";
        return `${rate}%`;
    }
    return "0%";
};

const buildTaxSummaryLines = (order, printData = {}) => {
    const baseBuckets = ["13%", "0%", "Exe", "Exo", "NoS"];
    const buckets = new Map(baseBuckets.map((label) => [label, { label, base: 0, tax: 0 }]));
    const orderLines = order?.get_orderlines?.() || [];

    for (const line of orderLines) {
        const prices = line?.get_all_prices?.() || {};
        const base = Number(prices.priceWithoutTax ?? prices.price_without_tax ?? 0);
        const taxAmount = Number(prices.tax ?? prices.taxAmount ?? 0);
        const taxes = line?.get_taxes?.() || [];

        let label = "0%";
        if (taxes.length) {
            label = classifyTaxBucket(taxes[0]);
        }
        if (!buckets.has(label)) {
            buckets.set(label, { label, base: 0, tax: 0 });
        }
        const bucket = buckets.get(label);
        bucket.base += base;
        bucket.tax += taxAmount;
    }

    const currencySymbol = printData.currency?.symbol || order?.pos?.currency?.symbol || "";

    return Array.from(buckets.values())
        .filter((line) => line.base || line.tax || baseBuckets.includes(line.label))
        .map((line) => ({
            ...line,
            formatted_base: formatCrAmount(line.base, currencySymbol),
            formatted_tax: formatCrAmount(line.tax, currencySymbol),
        }));
};

const buildReferencePayload = (order) => {
    const documentType = normalizeText(order.cr_fe_reference_document_type);
    const number = normalizeText(order.cr_fe_reference_document_number);
    const issueDate = normalizeDate(order.cr_fe_reference_issue_date);
    const code = normalizeText(order.cr_fe_reference_code);
    const reason = normalizeText(order.cr_fe_reference_reason);

    if (!documentType && !number && !issueDate && !code && !reason) return null;

    return {
        cr_fe_reference_document_type: documentType,
        cr_fe_reference_document_number: number,
        cr_fe_reference_issue_date: issueDate,
        cr_fe_reference_code: code,
        cr_fe_reference_reason: reason,
        // Backward/compat-friendly nested object (server can ignore)
        reference: {
            document_type: documentType,
            number,
            issue_date: issueDate,
            code,
            reason,
        },
    };
};

patch(PosOrder.prototype, {
    setup(vals) {
        super.setup(vals);

        const source = vals || {};
        this.cr_fe_document_type = normalizeDocumentType(
            source.cr_fe_document_type ?? this.cr_fe_document_type
        );
        this.cr_fe_consecutivo = normalizeText(source.cr_fe_consecutivo ?? this.cr_fe_consecutivo);
        this.cr_fe_clave = normalizeText(source.cr_fe_clave ?? this.cr_fe_clave);
        this.cr_fe_status = normalizeText(source.cr_fe_status ?? this.cr_fe_status);

        this.fp_payment_method = normalizeText(source.fp_payment_method ?? this.fp_payment_method);

        this.cr_fe_emisor_name = normalizeText(source.cr_fe_emisor_name ?? this.cr_fe_emisor_name);
        this.cr_fe_emisor_vat = normalizeText(source.cr_fe_emisor_vat ?? this.cr_fe_emisor_vat);
        this.cr_fe_emisor_email = normalizeText(source.cr_fe_emisor_email ?? this.cr_fe_emisor_email);
        this.cr_fe_emisor_phone = normalizeText(source.cr_fe_emisor_phone ?? this.cr_fe_emisor_phone);
        this.cr_fe_emisor_address = normalizeText(source.cr_fe_emisor_address ?? this.cr_fe_emisor_address);

        this.cr_fe_receptor_name = normalizeText(source.cr_fe_receptor_name ?? this.cr_fe_receptor_name);
        this.cr_fe_receptor_vat = normalizeText(source.cr_fe_receptor_vat ?? this.cr_fe_receptor_vat);
        this.cr_fe_receptor_email = normalizeText(source.cr_fe_receptor_email ?? this.cr_fe_receptor_email);
        this.cr_fe_receptor_phone = normalizeText(source.cr_fe_receptor_phone ?? this.cr_fe_receptor_phone);
        this.cr_fe_receptor_address = normalizeText(source.cr_fe_receptor_address ?? this.cr_fe_receptor_address);

        // Refund reference fields (NC)
        this.cr_fe_reference_document_type = normalizeText(
            source.cr_fe_reference_document_type ?? this.cr_fe_reference_document_type
        );
        this.cr_fe_reference_document_number = normalizeText(
            source.cr_fe_reference_document_number ?? this.cr_fe_reference_document_number
        );
        this.cr_fe_reference_issue_date = normalizeDate(
            source.cr_fe_reference_issue_date ?? this.cr_fe_reference_issue_date
        );
        this.cr_fe_reference_code = normalizeText(
            source.cr_fe_reference_code ?? this.cr_fe_reference_code
        );
        this.cr_fe_reference_reason = normalizeText(
            source.cr_fe_reference_reason ?? this.cr_fe_reference_reason
        );
    },

    export_for_printing() {
        const parent = Object.getPrototypeOf(PosOrder.prototype);
        const data = parent.export_for_printing
            ? parent.export_for_printing.call(this, ...arguments)
            : {};
        // Avoid duplicated customer block in printed ticket header.
        // We render customer data in our dedicated "Datos del cliente" section.
        data.partner = null;
        data.cr_order_date_ddmmyyyy =
            formatDateDDMMYYYY(data.date?.local) ||
            formatDateDDMMYYYY(data.date_order) ||
            formatDateDDMMYYYY(data.date) ||
            null;
        data.cr_tax_summary_lines = buildTaxSummaryLines(this, data);
        return data;
    },

    exportForPrinting() {
        const parent = Object.getPrototypeOf(PosOrder.prototype);
        const data = parent.exportForPrinting
            ? parent.exportForPrinting.call(this, ...arguments)
            : this.export_for_printing(...arguments);
        // Avoid duplicated customer block in printed ticket header.
        // We render customer data in our dedicated "Datos del cliente" section.
        data.partner = null;
        data.cr_order_date_ddmmyyyy =
            formatDateDDMMYYYY(data.date?.local) ||
            formatDateDDMMYYYY(data.date_order) ||
            formatDateDDMMYYYY(data.date) ||
            null;
        data.cr_tax_summary_lines = buildTaxSummaryLines(this, data);
        return data;
    },

    serializeForORM(opts = {}) {
        const data = super.serializeForORM(...arguments);

        // Send reference data for NC (even if server auto-derives it, this allows manual overrides).
        const referencePayload = buildReferencePayload(this);
        if (referencePayload) {
            for (const [key, value] of Object.entries(referencePayload)) {
                if (value === undefined) continue;
                if (value === null) continue;
                if (value === false) continue;
                data[key] = value;
            }
        }

        return data;
    },
});
