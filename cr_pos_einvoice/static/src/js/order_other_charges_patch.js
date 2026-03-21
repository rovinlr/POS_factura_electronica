/** @odoo-module */

import { patch } from "@web/core/utils/patch";
import { PosOrder } from "@point_of_sale/app/models/pos_order";

const SERVICE_CHARGE_CODE = "06";
const SERVICE_CHARGE_RATE = 0.1;

const roundAmount = (value) => Math.round(value * 100000) / 100000;

const getOrderSubtotal = (order) => {
    const subtotal = Number(order?.get_total_without_tax?.() ?? order?.getTotalWithoutTax?.() ?? 0);
    return Number.isFinite(subtotal) ? subtotal : 0;
};

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

const computeChargesTotal = (charges) =>
    roundAmount(charges.reduce((acc, item) => acc + Number(item?.amount || 0), 0));

patch(PosOrder.prototype, {
    setup(vals) {
        super.setup(vals);
        const source = vals || {};
        this.cr_other_charges = normalizeCharges(source.cr_other_charges ?? source.cr_other_charges_json ?? this.cr_other_charges);
    },

    setOtherCharges(charges) {
        this.assertEditable?.();
        const subtotal = getOrderSubtotal(this);
        this.cr_other_charges = normalizeCharges(charges, subtotal, { forceSubtotalAmount: false });
    },

    getOtherCharges() {
        const subtotal = getOrderSubtotal(this);
        return normalizeCharges(this.cr_other_charges, subtotal, { forceSubtotalAmount: true });
    },

    getOtherChargesTotal() {
        return computeChargesTotal(this.getOtherCharges());
    },

    get_total_with_tax() {
        const baseTotal = Number(super.get_total_with_tax?.(...arguments) ?? 0);
        return roundAmount(baseTotal + this.getOtherChargesTotal());
    },

    getTotalWithTax() {
        const baseTotal = Number(super.getTotalWithTax?.(...arguments) ?? this.get_total_with_tax());
        return roundAmount(baseTotal + this.getOtherChargesTotal());
    },


    serializeForORM(opts = {}) {
        const data = super.serializeForORM(...arguments);
        const subtotal = getOrderSubtotal(this);
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
