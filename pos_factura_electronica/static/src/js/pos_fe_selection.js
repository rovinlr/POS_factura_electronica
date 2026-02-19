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

        const isToInvoice =
            typeof this.is_to_invoice === "function"
                ? this.is_to_invoice()
                : typeof this.isToInvoice === "function"
                  ? this.isToInvoice()
                  : !!this.to_invoice;

        // Si se marca "Facturar", usar Factura Electrónica; si no, Tiquete Electrónico.
        data.cr_fe_document_kind = isToInvoice ? "electronic_invoice" : "electronic_ticket";

        // No forzar facturación cuando el documento FE es tiquete.
        if (data.cr_fe_document_kind === "electronic_ticket") {
            data.to_invoice = false;
        }

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
    _getOrderInvoicingState(order) {
        return typeof order?.is_to_invoice === "function"
            ? order.is_to_invoice()
            : typeof order?.isToInvoice === "function"
              ? order.isToInvoice()
              : !!order?.to_invoice;
    },

    _setOrderInvoicingState(order, value) {
        if (!order) {
            return;
        }
        if (typeof order.set_to_invoice === "function") {
            order.set_to_invoice(value);
            return;
        }
        if (typeof order.setToInvoice === "function") {
            order.setToInvoice(value);
            return;
        }
        order.to_invoice = !!value;
    },

    _normalizeToTicketWithoutPartner(order) {
        const isToInvoice = this._getOrderInvoicingState(order);
        const partner =
            typeof order?.get_partner === "function"
                ? order.get_partner()
                : typeof order?.getPartner === "function"
                  ? order.getPartner()
                  : order?.partner_id || false;
        const shouldTreatAsTicket = !isToInvoice || order?.cr_fe_document_kind === "electronic_ticket";

        if (
            this.pos.config.l10n_cr_enable_einvoice_from_pos &&
            order &&
            shouldTreatAsTicket &&
            !partner
        ) {
            this._setOrderInvoicingState(order, false);
            order.cr_fe_document_kind = "electronic_ticket";
            return true;
        }
        return false;
    },

    _normalizeToTicketByInvoicingSelection(order) {
        if (!this.pos.config.l10n_cr_enable_einvoice_from_pos || !order) {
            return false;
        }
        if (this._getOrderInvoicingState(order)) {
            order.cr_fe_document_kind = "electronic_invoice";
            return false;
        }
        this._setOrderInvoicingState(order, false);
        order.cr_fe_document_kind = "electronic_ticket";
        return true;
    },

    async _isOrderValid(isForceValidate) {
        this._normalizeToTicketWithoutPartner(this.currentOrder);
        this._normalizeToTicketByInvoicingSelection(this.currentOrder);
        return super._isOrderValid(...arguments);
    },

    async validateOrder(isForceValidate) {
        this._normalizeToTicketWithoutPartner(this.currentOrder);
        this._normalizeToTicketByInvoicingSelection(this.currentOrder);
        return super.validateOrder(...arguments);
    },
});
