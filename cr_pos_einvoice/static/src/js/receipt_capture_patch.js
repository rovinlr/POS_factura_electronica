/** @odoo-module */

import { patch } from "@web/core/utils/patch";
import { ReceiptScreen } from "@point_of_sale/app/screens/receipt_screen/receipt_screen";

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const findReceiptHtml = () => {
    const receiptNode =
        document.querySelector(".receipt-screen .pos-receipt") ||
        document.querySelector(".pos-receipt-container .pos-receipt") ||
        document.querySelector(".pos-receipt");
    return receiptNode?.outerHTML || null;
};

patch(ReceiptScreen.prototype, {
    setup() {
        super.setup(...arguments);
        this._crReceiptHtmlCaptured = false;
    },

    async onMounted() {
        await super.onMounted?.(...arguments);
        await this._crCaptureReceiptHtml();
    },

    async _crCaptureReceiptHtml() {
        if (this._crReceiptHtmlCaptured) return;
        const order = this.pos?.get_order?.();
        if (!order) return;

        let orderId = Number(order.id);
        for (let i = 0; i < 12 && !Number.isInteger(orderId); i++) {
            await delay(250);
            orderId = Number(order.id);
        }
        if (!Number.isInteger(orderId)) return;

        let html = findReceiptHtml();
        if (!html) {
            await delay(150);
            html = findReceiptHtml();
        }
        if (!html) return;

        try {
            await this.pos.data.call("pos.order", "cr_pos_store_receipt_html", [orderId, html]);
            this._crReceiptHtmlCaptured = true;
        } catch {
            // Best effort: FE PDF can still fallback to backend report rendering.
        }
    },
});

