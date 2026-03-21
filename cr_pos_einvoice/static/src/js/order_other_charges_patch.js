/** @odoo-module */

import { patch } from "@web/core/utils/patch";
import { PosOrder } from "@point_of_sale/app/models/pos_order";

const SERVICE_CHARGE_CODE = "06";
const SERVICE_CHARGE_RATE = 0.1;

const roundAmount = (value) => Math.round(value * 100000) / 100000;

const computeServiceCharge = (subtotal) => {
    const base = Number(subtotal);
    if (!Number.isFinite(base) || base <= 0) return null;
    return {
        type: "01",
        code: SERVICE_CHARGE_CODE,
        amount: roundAmount(base * SERVICE_CHARGE_RATE),
        currency: "CRC",
        description: "Impuesto de servicio 10%",
        percent: 10,
    };
};

const normalizeCharges = (charges, subtotal = null, options = {}) => {
    const { forceSubtotalAmount = false } = options;
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
        const code = String(item.code ?? item.codigo ?? "");
        if (code && code !== SERVICE_CHARGE_CODE) continue;
        const percent = Number(item.percent ?? item.porcentaje ?? 10);
        const computed = computeServiceCharge(subtotal);
        const amount = Number(forceSubtotalAmount ? computed?.amount : (item.amount ?? item.monto ?? computed?.amount));
        if (!Number.isFinite(amount) || amount <= 0) continue;
        normalized.push({
            type: String(item.type ?? item.tipo ?? item.charge_type ?? "99"),
            code: SERVICE_CHARGE_CODE,
            amount: roundAmount(amount),
            currency: String(item.currency ?? item.moneda ?? "CRC"),
            description: String(item.description ?? item.detalle ?? "Impuesto de servicio 10%"),
            percent: Number.isFinite(percent) ? percent : 10,
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
        const subtotal = Number(this.get_total_without_tax?.() ?? 0);
        this.cr_other_charges = normalizeCharges(charges, subtotal, { forceSubtotalAmount: false });
    },

    getOtherCharges() {
        return normalizeCharges(this.cr_other_charges);
    },

    serializeForORM(opts = {}) {
        const data = super.serializeForORM(...arguments);
        const subtotal = Number(this.get_total_without_tax?.() ?? 0);
        const charges = this.getOtherCharges();
        if (charges.length) {
            this.cr_other_charges = normalizeCharges(charges, subtotal, { forceSubtotalAmount: true });
            // Multiple keys for server-side extraction (backend checks several aliases)
            data.cr_other_charges = this.cr_other_charges;
            data.other_charges = this.cr_other_charges;
            data.otros_cargos = this.cr_other_charges;
        }
        return data;
    },
});
