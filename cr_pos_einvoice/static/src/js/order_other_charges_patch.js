/** @odoo-module */

import { patch } from "@web/core/utils/patch";
import { PosOrder } from "@point_of_sale/app/models/pos_order";

const normalizeCharges = (charges) => {
    if (!charges) return [];
    if (typeof charges === "string") {
        try {
            charges = JSON.parse(charges);
        } catch {
            return [];
        }
    }
    if (!Array.isArray(charges)) return [];
    const normalized = [];
    for (const item of charges) {
        if (!item || typeof item !== "object") continue;
        const amount = Number(item.amount ?? item.monto);
        if (!Number.isFinite(amount) || amount <= 0) continue;
        normalized.push({
            type: String(item.type ?? item.tipo ?? item.charge_type ?? "99"),
            code: String(item.code ?? item.codigo ?? "01"),
            amount,
            currency: String(item.currency ?? item.moneda ?? "CRC"),
            description: String(item.description ?? item.detalle ?? "Cargo adicional POS"),
            percent: item.percent ?? item.porcentaje ?? null,
        });
    }
    return normalized;
};

patch(PosOrder.prototype, {
    setup(vals) {
        super.setup(vals);
        const source = vals || {};
        this.cr_other_charges = normalizeCharges(source.cr_other_charges ?? source.cr_other_charges_json ?? this.cr_other_charges);
    },

    setOtherCharges(charges) {
        this.assertEditable?.();
        this.cr_other_charges = normalizeCharges(charges);
    },

    getOtherCharges() {
        return normalizeCharges(this.cr_other_charges);
    },

    serializeForORM(opts = {}) {
        const data = super.serializeForORM(...arguments);
        const charges = this.getOtherCharges();
        if (charges.length) {
            // Multiple keys for server-side extraction (backend checks several aliases)
            data.cr_other_charges = charges;
            data.other_charges = charges;
            data.otros_cargos = charges;
        }
        return data;
    },
});
