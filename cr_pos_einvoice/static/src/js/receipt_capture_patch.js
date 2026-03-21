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

const resolveOrderId = async (screen) => {
    const order = screen.currentOrder || screen.pos?.get_order?.();
    if (!order) return null;
    let orderId = Number(order.id);
    for (let i = 0; i < 12 && !Number.isInteger(orderId); i++) {
        await delay(250);
        orderId = Number(order.id);
    }
    return Number.isInteger(orderId) ? orderId : null;
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
        const orderId = await resolveOrderId(this);
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

    /**
     * Replace POS native backend-invoice flow for "Facturar" in ReceiptScreen.
     * In this module, FE is produced from pos.order and no account.move should
     * be created from the POS button.
     */
    async _crHandleInvoiceButton() {
        const orderId = await resolveOrderId(this);
        if (!orderId) return false;
        try {
            const action = await this.pos.data.call("pos.order", "action_cr_generate_pdf_attachment", [[orderId]]);
            if (action?.url) {
                window.open(action.url, "_blank", "noopener,noreferrer");
            }
            return true;
        } catch {
            return false;
        }
    },

    async onInvoiceOrder() {
        const handled = await this._crHandleInvoiceButton();
        if (handled) return;
        if (super.onInvoiceOrder) {
            await super.onInvoiceOrder(...arguments);
        }
    },

    async _onClickInvoice() {
        const handled = await this._crHandleInvoiceButton();
        if (handled) return;
        if (super._onClickInvoice) {
            await super._onClickInvoice(...arguments);
        }
    },

    async invoiceOrder() {
        const handled = await this._crHandleInvoiceButton();
        if (handled) return;
        if (super.invoiceOrder) {
            await super.invoiceOrder(...arguments);
        }
    },
});
