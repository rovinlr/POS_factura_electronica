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
