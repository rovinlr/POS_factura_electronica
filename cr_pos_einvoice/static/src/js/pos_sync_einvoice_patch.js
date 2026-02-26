/** @odoo-module */

import { patch } from "@web/core/utils/patch";
import { PosStore } from "@point_of_sale/app/services/pos_store";

const firstDefined = (...values) => values.find((value) => value !== undefined && value !== null);

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));


const isSameOrder = (order, row) => {
    const orderServerId = firstDefined(order.server_id, order.backendId, order.id);
    const rowServerId = firstDefined(row.id, row.server_id, row.backendId);
    const orderReference = firstDefined(
        order.name,
        order.pos_reference,
        order.uid,
        order.uuid,
        order.reference
    );
    const rowReference = firstDefined(row.pos_reference, row.name, row.uid, row.reference);

    if (orderServerId && rowServerId) {
        return Number(orderServerId) === Number(rowServerId);
    }
    if (orderReference && rowReference) {
        return String(orderReference) === String(rowReference);
    }
    return false;
};

const applyFeFields = (order, row) => {
    const values = {
        cr_fe_document_type: firstDefined(row.cr_fe_document_type, order.cr_fe_document_type),
        cr_fe_consecutivo: firstDefined(row.cr_fe_consecutivo, order.cr_fe_consecutivo),
        cr_fe_clave: firstDefined(row.cr_fe_clave, order.cr_fe_clave),
        cr_fe_status: firstDefined(row.cr_fe_status, order.cr_fe_status),
        fp_payment_method: firstDefined(row.fp_payment_method, order.fp_payment_method),
    };

    Object.assign(order, values);
};

const hasRequiredFeData = (values) => Boolean(values?.cr_fe_consecutivo && values?.cr_fe_clave);

const isReceiptScreen = (screen) => {
    if (!screen) {
        return false;
    }
    if (typeof screen === "string") {
        return screen === "ReceiptScreen";
    }
    return firstDefined(screen.name, screen.component?.name, screen.constructor?.name) === "ReceiptScreen";
};

const needsFeWait = (values) => {
    const documentType = firstDefined(values?.cr_fe_document_type, values?.document_type);
    if (!documentType) {
        return false;
    }
    return !hasRequiredFeData(values);
};

const getOrmService = (store) =>
    firstDefined(store.orm, store.env?.services?.orm, store.pos?.env?.services?.orm, store.data?.orm);

patch(PosStore.prototype, {
    async _crWaitForFeFields(order, row, options = {}) {
        const orm = getOrmService(this);
        if (!orm || !orm.call) {
            return false;
        }

        const timeoutMs = options.timeoutMs || 12000;
        const intervalMs = options.intervalMs || 700;
        const startedAt = Date.now();
        const orderId = firstDefined(row?.id, row?.server_id, row?.backendId, order?.server_id, order?.id);
        const orderRef = firstDefined(row?.pos_reference, row?.name, order?.name, order?.pos_reference, order?.uid);

        while (Date.now() - startedAt < timeoutMs) {
            const domain = orderId
                ? [["id", "=", Number(orderId)]]
                : orderRef
                  ? [["pos_reference", "=", String(orderRef)]]
                  : [];
            if (!domain.length) {
                return false;
            }

            const [result] = await orm.call(
                "pos.order",
                "search_read",
                [domain, ["id", "pos_reference", "cr_fe_document_type", "cr_fe_consecutivo", "cr_fe_clave", "cr_fe_status", "fp_payment_method"]],
                { limit: 1 }
            );
            if (result) {
                applyFeFields(order, result);
                if (hasRequiredFeData(result)) {
                    return true;
                }
            }
            await delay(intervalMs);
        }

        return false;
    },


    async showScreen(screen, props) {
        const activeOrder = this.get_order ? this.get_order() : this.selectedOrder;
        if (isReceiptScreen(screen) && activeOrder && needsFeWait(activeOrder)) {
            await this._crWaitForFeFields(activeOrder, activeOrder);
        }
        if (super.showScreen) {
            return super.showScreen(...arguments);
        }
        return undefined;
    },

    async postSyncAllOrders() {
        if (super.postSyncAllOrders) {
            await super.postSyncAllOrders(...arguments);
        }

        const rows = arguments[0];
        if (!Array.isArray(rows) || !rows.length) {
            return;
        }
        const orders = this.models?.["pos.order"]?.getAll?.() || [];
        if (!orders.length) {
            return;
        }

        for (const row of rows) {
            const targetOrders = orders.filter((order) => isSameOrder(order, row));
            for (const order of targetOrders) {
                applyFeFields(order, row);
                if (needsFeWait(row) || needsFeWait(order)) {
                    await this._crWaitForFeFields(order, row);
                }
            }
        }
    },
});
