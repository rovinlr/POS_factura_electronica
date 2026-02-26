/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { Order } from "@point_of_sale/app/store/models";

/**
 * Why: Make FE fields + tax amount per line available to the receipt.
 */
patch(Order.prototype, {
    export_as_JSON() {
        const json = super.export_as_JSON(...arguments);
        json.cr_fe_document_type = this.cr_fe_document_type || null;
        json.cr_fe_consecutivo = this.cr_fe_consecutivo || null;
        json.cr_fe_clave = this.cr_fe_clave || null;
        json.cr_fe_status = this.cr_fe_status || null;
        json.fp_payment_method = this.fp_payment_method || null;
        return json;
    },
    init_from_JSON(json) {
        super.init_from_JSON(...arguments);
        this.cr_fe_document_type = json.cr_fe_document_type || null;
        this.cr_fe_consecutivo = json.cr_fe_consecutivo || null;
        this.cr_fe_clave = json.cr_fe_clave || null;
        this.cr_fe_status = json.cr_fe_status || null;
        this.fp_payment_method = json.fp_payment_method || null;
    },
    export_for_printing() {
        const receipt = super.export_for_printing(...arguments);

        // Per-line tax amount (numeric). Template will format.
        const orderlines = this.get_orderlines ? this.get_orderlines() : [];
        if (Array.isArray(receipt.orderlines)) {
            receipt.orderlines = receipt.orderlines.map((line, idx) => {
                const ol = orderlines[idx];
                if (!ol || !ol.get_all_prices) {
                    return { ...line, tax_amount: null };
                }
                const prices = ol.get_all_prices();
                const taxAmount = (prices && typeof prices.tax === "number") ? prices.tax : 0;
                return { ...line, tax_amount: taxAmount };
            });
        }

        receipt.einvoice = {
            document_type: this.cr_fe_document_type || null,
            consecutivo: this.cr_fe_consecutivo || null,
            clave: this.cr_fe_clave || null,
            status: this.cr_fe_status || null,
            payment_method: this.fp_payment_method || null,
            receptor_id: (this.get_partner && this.get_partner() && (this.get_partner().vat || null)) || null,
        };
        return receipt;
    },
});
