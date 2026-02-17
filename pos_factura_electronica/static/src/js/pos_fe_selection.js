/** @odoo-module */

import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { Order } from "@point_of_sale/app/store/models";
import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";
import { SelectionPopup } from "@point_of_sale/app/utils/input_popups/selection_popup";

patch(Order.prototype, {
    setup() {
        const result = super.setup(...arguments);
        this.cr_fe_document_kind = this.cr_fe_document_kind || "electronic_invoice";
        this.cr_fe_payment_method = this.cr_fe_payment_method || "01";
        this.cr_fe_payment_condition = this.cr_fe_payment_condition || "01";
        return result;
    },
    export_as_JSON() {
        const json = super.export_as_JSON(...arguments);
        json.cr_fe_document_kind = this.cr_fe_document_kind;
        json.cr_fe_payment_method = this.cr_fe_payment_method;
        json.cr_fe_payment_condition = this.cr_fe_payment_condition;
        return json;
    },
    init_from_JSON(json) {
        super.init_from_JSON(...arguments);
        this.cr_fe_document_kind = json.cr_fe_document_kind || "electronic_invoice";
        this.cr_fe_payment_method = json.cr_fe_payment_method || "01";
        this.cr_fe_payment_condition = json.cr_fe_payment_condition || "01";
    },
});

patch(PaymentScreen.prototype, {
    async selectCostaRicaEInvoiceData() {
        const order = this.currentOrder;
        if (!order) {
            return;
        }

        const docResult = await this.popup.add(SelectionPopup, {
            title: _t("Tipo de documento"),
            list: [
                { id: "electronic_invoice", label: _t("Factura electrónica"), item: "electronic_invoice" },
                { id: "electronic_ticket", label: _t("Tiquete electrónico"), item: "electronic_ticket" },
                { id: "credit_note", label: _t("Nota de crédito"), item: "credit_note" },
            ],
        });
        if (!docResult?.confirmed) {
            return;
        }
        order.cr_fe_document_kind = docResult.payload;

        const methodResult = await this.popup.add(SelectionPopup, {
            title: _t("Método de pago FE"),
            list: [
                { id: "01", label: _t("01 - Efectivo"), item: "01" },
                { id: "02", label: _t("02 - Tarjeta"), item: "02" },
                { id: "03", label: _t("03 - Transferencia"), item: "03" },
                { id: "04", label: _t("04 - Crédito"), item: "04" },
            ],
        });
        if (!methodResult?.confirmed) {
            return;
        }
        order.cr_fe_payment_method = methodResult.payload;

        const conditionResult = await this.popup.add(SelectionPopup, {
            title: _t("Condición de pago FE"),
            list: [
                { id: "01", label: _t("01 - Contado"), item: "01" },
                { id: "02", label: _t("02 - Crédito"), item: "02" },
            ],
        });
        if (!conditionResult?.confirmed) {
            return;
        }
        order.cr_fe_payment_condition = conditionResult.payload;
    },
});
