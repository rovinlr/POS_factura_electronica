/** @odoo-module */

import { patch } from "@web/core/utils/patch";
import { Order } from "@point_of_sale/app/store/models";

const normalizeCharge = (charge) => {
    if (!charge || typeof charge !== "object") {
        return null;
    }
    const amount = Number(charge.amount ?? charge.monto);
    if (!Number.isFinite(amount) || amount <= 0) {
        return null;
    }
    return {
        type: String(charge.type ?? charge.tipo ?? charge.charge_type ?? "99"),
        code: String(charge.code ?? charge.codigo ?? "01"),
        amount,
        currency: String(charge.currency ?? charge.moneda ?? "CRC"),
        description: String(charge.description ?? charge.detalle ?? "Cargo adicional POS"),
        percent: charge.percent ?? charge.porcentaje ?? null,
    };
};

if (Order?.prototype) {
    patch(Order.prototype, {
    setup() {
        super.setup(...arguments);
        this.cr_other_charges = Array.isArray(this.cr_other_charges)
            ? this.cr_other_charges.map(normalizeCharge).filter(Boolean)
            : [];
    },

    setOtherCharges(charges) {
        const sanitized = Array.isArray(charges) ? charges.map(normalizeCharge).filter(Boolean) : [];
        this.cr_other_charges = sanitized;
        return sanitized;
    },

    getOtherCharges() {
        return Array.isArray(this.cr_other_charges) ? [...this.cr_other_charges] : [];
    },

    export_as_JSON() {
        const json = super.export_as_JSON(...arguments);
        if (this.cr_other_charges?.length) {
            json.cr_other_charges = this.getOtherCharges();
            json.other_charges = this.getOtherCharges();
        }
        return json;
    },

    init_from_JSON(json) {
        super.init_from_JSON(...arguments);
        this.setOtherCharges(json?.cr_other_charges || json?.other_charges || []);
    },
    });
} else {
    console.warn("[cr_pos_einvoice] POS Order model not found; other charges patch was skipped.");
}
