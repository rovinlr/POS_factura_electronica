/** @odoo-module */

import { patch } from "@web/core/utils/patch";
import { PosOrder } from "@point_of_sale/app/models/pos_order";

patch(PosOrder.prototype, {
    setup(vals) {
        super.setup(...arguments);
        this.cr_fe_document_kind = vals.cr_fe_document_kind || "electronic_ticket";
        this.cr_fe_payment_method = vals.cr_fe_payment_method || false;
        this.cr_fe_payment_condition = vals.cr_fe_payment_condition || false;
    },

    serializeForORM(opts = {}) {
        const data = super.serializeForORM(opts);

        // Si se marca "Facturar", usar Factura Electrónica; si no, Tiquete Electrónico.
        data.cr_fe_document_kind = this.to_invoice ? "electronic_invoice" : "electronic_ticket";

        // Método y condición FE se resuelven según los métodos de pago del POS (backend).
        if (this.cr_fe_payment_method) {
            data.cr_fe_payment_method = this.cr_fe_payment_method;
        }
        if (this.cr_fe_payment_condition) {
            data.cr_fe_payment_condition = this.cr_fe_payment_condition;
        }

        return data;
    },
});
