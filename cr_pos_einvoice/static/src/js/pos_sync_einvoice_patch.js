/** @odoo-module */

import { patch } from "@web/core/utils/patch";
import { PosStore } from "@point_of_sale/app/services/pos_store";

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const normalizeText = (value) => {
    if (value === undefined || value === null) return null;
    const text = String(value).trim();
    return text || null;
};

const normalizeDocumentType = (value) => {
    const text = normalizeText(value);
    if (!text) return null;
    const normalized = text.toLowerCase();
    const mapping = { te: "te", fe: "fe", nc: "nc" };
    return mapping[normalized] || normalized;
};

const hasRequiredFeData = (order) => {
    return Boolean(
        normalizeDocumentType(order?.cr_fe_document_type) &&
            normalizeText(order?.cr_fe_consecutivo) &&
            normalizeText(order?.cr_fe_clave)
    );
};

const applyFeFields = (order, payload) => {
    if (!order || !payload) return false;

    const docType = normalizeDocumentType(payload.cr_fe_document_type ?? payload.document_type);
    if (docType) order.cr_fe_document_type = docType;

    const consecutivo = normalizeText(payload.cr_fe_consecutivo ?? payload.consecutivo);
    if (consecutivo) order.cr_fe_consecutivo = consecutivo;

    const clave = normalizeText(payload.cr_fe_clave ?? payload.clave);
    if (clave) order.cr_fe_clave = clave;

    const status = normalizeText(payload.cr_fe_status ?? payload.status);
    if (status) order.cr_fe_status = status;

    const paymentMethod = normalizeText(payload.fp_payment_method ?? payload.payment_method);
    if (paymentMethod) order.fp_payment_method = paymentMethod;
    const emisorName = normalizeText(payload.cr_fe_emisor_name);
    if (emisorName) order.cr_fe_emisor_name = emisorName;
    const emisorVat = normalizeText(payload.cr_fe_emisor_vat);
    if (emisorVat) order.cr_fe_emisor_vat = emisorVat;
    const emisorEmail = normalizeText(payload.cr_fe_emisor_email);
    if (emisorEmail) order.cr_fe_emisor_email = emisorEmail;
    const emisorPhone = normalizeText(payload.cr_fe_emisor_phone);
    if (emisorPhone) order.cr_fe_emisor_phone = emisorPhone;
    const emisorAddress = normalizeText(payload.cr_fe_emisor_address);
    if (emisorAddress) order.cr_fe_emisor_address = emisorAddress;

    const receptorName = normalizeText(payload.cr_fe_receptor_name);
    if (receptorName) order.cr_fe_receptor_name = receptorName;
    const receptorVat = normalizeText(payload.cr_fe_receptor_vat);
    if (receptorVat) order.cr_fe_receptor_vat = receptorVat;
    const receptorEmail = normalizeText(payload.cr_fe_receptor_email);
    if (receptorEmail) order.cr_fe_receptor_email = receptorEmail;
    const receptorPhone = normalizeText(payload.cr_fe_receptor_phone);
    if (receptorPhone) order.cr_fe_receptor_phone = receptorPhone;
    const receptorAddress = normalizeText(payload.cr_fe_receptor_address);
    if (receptorAddress) order.cr_fe_receptor_address = receptorAddress;

    // NC references (optional)
    order.cr_fe_reference_document_type = normalizeText(payload.cr_fe_reference_document_type) ?? order.cr_fe_reference_document_type;
    order.cr_fe_reference_document_number = normalizeText(payload.cr_fe_reference_document_number) ?? order.cr_fe_reference_document_number;
    order.cr_fe_reference_issue_date = payload.cr_fe_reference_issue_date ?? order.cr_fe_reference_issue_date;
    order.cr_fe_reference_code = normalizeText(payload.cr_fe_reference_code) ?? order.cr_fe_reference_code;
    order.cr_fe_reference_reason = normalizeText(payload.cr_fe_reference_reason) ?? order.cr_fe_reference_reason;

    return true;
};

const getOrderLookupRefs = (order) => {
    const refs = [];
    const posRef = normalizeText(order?.pos_reference);
    const name = normalizeText(order?.name);
    if (posRef) refs.push(posRef);
    if (name && name !== posRef) refs.push(name);
    return refs;
};

const findOrderInStore = (store, routeParams) => {
    const uuid = routeParams?.orderUuid || routeParams?.order_uuid;
    if (uuid) {
        const byUuid = store.models?.["pos.order"]?.find?.((o) => o.uuid === uuid);
        if (byUuid) return byUuid;
    }
    return store.getOrder?.() || store.selectedOrder || null;
};

patch(PosStore.prototype, {
    async postSyncAllOrders(serverOrders) {
        if (super.postSyncAllOrders) {
            await super.postSyncAllOrders(...arguments);
        }
        if (!Array.isArray(serverOrders) || !this.models?.["pos.order"]) return;

        // serverOrders are already PosOrder instances (connected data); normalize fields for safety.
        for (const row of serverOrders) {
            applyFeFields(row, row);
        }
    },

    navigate(routeName, routeParams = {}) {
        const res = super.navigate(...arguments);

        if (routeName !== "ReceiptScreen") return res;

        const order = findOrderInStore(this, routeParams);
        if (!order || hasRequiredFeData(order)) return res;

        // Do not block navigation; fetch and hydrate FE fields ASAP.
        (async () => {
            const orderId = typeof order.id === "number" ? order.id : null;
            const refs = getOrderLookupRefs(order);
            try {
                // Server method signature: (order_id=None, references=None)
                const payload = await this.data.call(
                    "pos.order",
                    "cr_pos_get_order_fe_for_receipt",
                    [orderId, refs],
                    { context: this.getSyncAllOrdersContext?.([order]) || {} }
                );
                applyFeFields(order, payload);
            } catch {
                // Best-effort: do nothing (receipt will show placeholders).
            }

            // Small retry window for race conditions (e.g., async identifiers).
            const maxAttempts = 12;
            for (let i = 0; i < maxAttempts && !hasRequiredFeData(order); i++) {
                await delay(250);
                try {
                    const payload = await this.data.call(
                        "pos.order",
                        "cr_pos_get_order_fe_for_receipt",
                        [orderId, refs],
                        { context: this.getSyncAllOrdersContext?.([order]) || {} }
                    );
                    applyFeFields(order, payload);
                } catch {
                    break;
                }
            }
        })();

        return res;
    },
});
