/** @odoo-module */

import { patch } from "@web/core/utils/patch";
import { PosOrder } from "@point_of_sale/app/models/pos_order";
import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";

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

patch(PaymentScreen.prototype, {
    async validateOrder(isForceValidate) {
        const order = this.currentOrder;
        if (
            this.pos.config.l10n_cr_enable_einvoice_from_pos &&
            order &&
            order.cr_fe_document_kind === "electronic_ticket" &&
            !order.get_partner()
        ) {
            if (typeof order.set_to_invoice === "function") {
                order.set_to_invoice(false);
            } else if (typeof order.setToInvoice === "function") {
                order.setToInvoice(false);
            } else {
                order.to_invoice = false;
            }
        }
        return super.validateOrder(...arguments);
    },
});
