/** @odoo-module */

import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { PosOrder } from "@point_of_sale/app/models/pos_order";
import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";
import { SelectionPopup } from "@point_of_sale/app/components/popups/selection_popup/selection_popup";
import { makeAwaitable } from "@point_of_sale/app/utils/make_awaitable_dialog";

patch(PosOrder.prototype, {
    setup(vals) {
        super.setup(...arguments);
        this.cr_fe_document_kind = vals.cr_fe_document_kind || "electronic_ticket";
        this.cr_fe_payment_method = vals.cr_fe_payment_method || "01";
        this.cr_fe_payment_condition = vals.cr_fe_payment_condition || "01";
    },

    serializeForORM(opts = {}) {
        const data = super.serializeForORM(opts);
        data.cr_fe_document_kind = this.cr_fe_document_kind || "electronic_ticket";
        data.cr_fe_payment_method = this.cr_fe_payment_method || "01";
        data.cr_fe_payment_condition = this.cr_fe_payment_condition || "01";
        return data;
    },
});

patch(PaymentScreen.prototype, {
    async selectCostaRicaEInvoiceData() {
        const order = this.currentOrder;
        if (!order) {
            return;
        }

        const docPayload = await makeAwaitable(this.dialog, SelectionPopup, {
            title: _t("Tipo de documento"),
            list: [
                { id: "electronic_invoice", label: _t("Factura electrónica"), item: "electronic_invoice" },
                { id: "electronic_ticket", label: _t("Tiquete electrónico"), item: "electronic_ticket" },
                { id: "credit_note", label: _t("Nota de crédito"), item: "credit_note" },
            ],
        });
        if (!docPayload) {
            return;
        }
        order.cr_fe_document_kind = docPayload;

        const methodPayload = await makeAwaitable(this.dialog, SelectionPopup, {
            title: _t("Método de pago FE"),
            list: [
                { id: "01", label: _t("01 - Efectivo"), item: "01" },
                { id: "02", label: _t("02 - Tarjeta"), item: "02" },
                { id: "03", label: _t("03 - Transferencia"), item: "03" },
                { id: "04", label: _t("04 - Crédito"), item: "04" },
            ],
        });
        if (!methodPayload) {
            return;
        }
        order.cr_fe_payment_method = methodPayload;

        const conditionPayload = await makeAwaitable(this.dialog, SelectionPopup, {
            title: _t("Condición de pago FE"),
            list: [
                { id: "01", label: _t("01 - Contado"), item: "01" },
                { id: "02", label: _t("02 - Crédito"), item: "02" },
            ],
        });
        if (!conditionPayload) {
            return;
        }
        order.cr_fe_payment_condition = conditionPayload;

        // Forzar creación de factura cuando se usan datos FE-CR.
        order.setToInvoice(true);
    },
});
