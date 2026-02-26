/** @odoo-module */

import { patch } from "@web/core/utils/patch";
import { PosStore } from "@point_of_sale/app/store/pos_store";

const firstDefined = (...values) => values.find((value) => value !== undefined && value !== null);

const isObject = (value) => value && typeof value === "object";

const normalizeOrders = (orders) => {
    if (!orders) {
        return [];
    }
    if (Array.isArray(orders)) {
        return orders.filter(Boolean);
    }
    return [orders];
};

const extractServerRows = (result) => {
    if (!result) {
        return [];
    }
    if (Array.isArray(result)) {
        return result.flatMap((entry) => extractServerRows(entry));
    }
    if (!isObject(result)) {
        return [];
    }

    const looksLikeOrderPayload = "id" in result || "server_id" in result || "pos_reference" in result;
    if (looksLikeOrderPayload) {
        return [result];
    }

    return Object.values(result).flatMap((entry) => extractServerRows(entry));
};

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

patch(PosStore.prototype, {
    async _save_to_server() {
        const ordersArg = arguments[0];
        const result = await super._save_to_server(...arguments);
        this._cr_apply_einvoice_values_from_server(ordersArg, result);
        return result;
    },

    async push_orders() {
        const ordersArg = arguments[0];
        const result = await super.push_orders(...arguments);
        this._cr_apply_einvoice_values_from_server(ordersArg, result);
        return result;
    },

    _cr_apply_einvoice_values_from_server(ordersArg, result) {
        const orders = normalizeOrders(ordersArg);
        if (!orders.length) {
            return;
        }
        const rows = extractServerRows(result);
        if (!rows.length) {
            return;
        }

        for (const order of orders) {
            const row = rows.find((entry) => isSameOrder(order, entry));
            if (row) {
                applyFeFields(order, row);
            }
        }
    },
});
