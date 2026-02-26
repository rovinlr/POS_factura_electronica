/** @odoo-module */

import { patch } from "@web/core/utils/patch";
import { Order } from "@point_of_sale/app/store/models";

const firstDefined = (...values) => values.find((value) => value !== undefined && value !== null);

const normalizeText = (value) => {
    if (value === undefined || value === null) {
        return null;
    }
    const text = String(value).trim();
    return text || null;
};

const pickPartner = (order) =>
    firstDefined(
        order.getPartner && order.getPartner(),
        order.get_partner && order.get_partner(),
        order.partner,
        order.partner_id
    ) || null;

patch(Order.prototype, {
    setup(vals) {
        if (super.setup) {
            super.setup(vals);
        }
        const source = vals || {};
        this.cr_fe_document_type = firstDefined(source.cr_fe_document_type, this.cr_fe_document_type) || null;
        this.cr_fe_consecutivo = firstDefined(source.cr_fe_consecutivo, this.cr_fe_consecutivo) || null;
        this.cr_fe_clave = firstDefined(source.cr_fe_clave, this.cr_fe_clave) || null;
        this.cr_fe_status = firstDefined(source.cr_fe_status, this.cr_fe_status) || null;
        this.fp_payment_method = firstDefined(source.fp_payment_method, this.fp_payment_method) || null;
    },

    export_as_JSON() {
        const json = super.export_as_JSON ? super.export_as_JSON(...arguments) : {};
        json.cr_fe_document_type = this.cr_fe_document_type || null;
        json.cr_fe_consecutivo = this.cr_fe_consecutivo || null;
        json.cr_fe_clave = this.cr_fe_clave || null;
        json.cr_fe_status = this.cr_fe_status || null;
        json.fp_payment_method = this.fp_payment_method || null;
        return json;
    },

    init_from_JSON(json) {
        if (super.init_from_JSON) {
            super.init_from_JSON(...arguments);
        }
        this.cr_fe_document_type = json.cr_fe_document_type || null;
        this.cr_fe_consecutivo = json.cr_fe_consecutivo || null;
        this.cr_fe_clave = json.cr_fe_clave || null;
        this.cr_fe_status = json.cr_fe_status || null;
        this.fp_payment_method = json.fp_payment_method || null;
    },

    export_for_printing() {
        const receipt = super.export_for_printing ? super.export_for_printing(...arguments) : {};
        const partner = pickPartner(this) || {};
        const receptorId = normalizeText(
            firstDefined(receipt.einvoice && receipt.einvoice.receptor_id, partner.vat, partner.identification_id)
        );

        receipt.einvoice = {
            ...(receipt.einvoice || {}),
            document_type: firstDefined(receipt.einvoice && receipt.einvoice.document_type, this.cr_fe_document_type) || null,
            consecutivo: firstDefined(receipt.einvoice && receipt.einvoice.consecutivo, this.cr_fe_consecutivo) || null,
            clave: firstDefined(receipt.einvoice && receipt.einvoice.clave, this.cr_fe_clave) || null,
            status: firstDefined(receipt.einvoice && receipt.einvoice.status, this.cr_fe_status) || null,
            payment_method:
                firstDefined(receipt.einvoice && receipt.einvoice.payment_method, this.fp_payment_method) || null,
            receptor_id: receptorId || null,
        };

        return receipt;
    },
});
