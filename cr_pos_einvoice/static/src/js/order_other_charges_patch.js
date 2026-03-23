/** @odoo-module */

import { patch } from "@web/core/utils/patch";
import { PosOrder } from "@point_of_sale/app/models/pos_order";

const SERVICE_CHARGE_CODE = "06";
const DEFAULT_SERVICE_CHARGE_PERCENT = 10;

const roundAmount = (value) => Math.round(value * 100000) / 100000;

const getLinesSubtotal = (order) => {
    const lines = order?.get_orderlines?.() || order?.getOrderlines?.() || [];
    if (!Array.isArray(lines) || !lines.length) {
        return 0;
    }
    return lines.reduce((acc, line) => {
        const lineSubtotal = Number(
            line?.get_price_without_tax?.() ??
                line?.getPriceWithoutTax?.() ??
                line?.price_subtotal ??
                line?.price_subtotal_incl ??
                0
        );
        return acc + (Number.isFinite(lineSubtotal) ? lineSubtotal : 0);
    }, 0);
};

const getOrderSubtotal = (order) => {
    const subtotal = Number(order?.get_total_without_tax?.() ?? order?.getTotalWithoutTax?.() ?? 0);
    if (Number.isFinite(subtotal) && subtotal > 0) {
        return subtotal;
    }
    const linesSubtotal = Number(getLinesSubtotal(order));
    return Number.isFinite(linesSubtotal) && linesSubtotal > 0 ? linesSubtotal : 0;
};

const getServiceChargePercent = (order) => {
    const value = Number(order?.pos?.config?.cr_service_charge_percent ?? DEFAULT_SERVICE_CHARGE_PERCENT);
    if (!Number.isFinite(value) || value <= 0) {
        return DEFAULT_SERVICE_CHARGE_PERCENT;
    }
    return value;
};

const computeServiceCharge = (subtotal, percent = DEFAULT_SERVICE_CHARGE_PERCENT) => {
    const base = Number(subtotal);
    if (!Number.isFinite(base) || base <= 0) return null;
    const numericPercent = Number(percent);
    const safePercent = Number.isFinite(numericPercent) && numericPercent > 0 ? numericPercent : DEFAULT_SERVICE_CHARGE_PERCENT;
    return {
        type: "01",
        code: SERVICE_CHARGE_CODE,
        amount: roundAmount(base * (safePercent / 100)),
        currency: "CRC",
        description: `Impuesto de servicio ${safePercent}%`,
        percent: safePercent,
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
        const percent = Number(item.percent ?? item.porcentaje ?? DEFAULT_SERVICE_CHARGE_PERCENT);
        const computed = computeServiceCharge(subtotal, percent);
        const amount = Number(forceSubtotalAmount ? computed?.amount : (item.amount ?? item.monto ?? computed?.amount));
        if (!Number.isFinite(amount) || amount <= 0) continue;
        normalized.push({
            type: String(item.type ?? item.tipo ?? item.charge_type ?? "01"),
            code: SERVICE_CHARGE_CODE,
            amount: roundAmount(amount),
            currency: String(item.currency ?? item.moneda ?? "CRC"),
            description: String(item.description ?? item.detalle ?? `Impuesto de servicio ${DEFAULT_SERVICE_CHARGE_PERCENT}%`),
            percent: Number.isFinite(percent) && percent > 0 ? percent : DEFAULT_SERVICE_CHARGE_PERCENT,
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
        this.cr_other_charges = normalizeCharges(
            source.cr_other_charges ?? source.cr_other_charges_json ?? this.cr_other_charges
        );
    },

    _crNotifyOtherChargesChanged() {
        this.lastOrderChange = Date.now();
        this.updateLastOrderChange?.();
        this.trigger?.("change", this);
        this._trigger?.("change", this);
    },

    setOtherCharges(charges) {
        this.assertEditable?.();
        const subtotal = getOrderSubtotal(this);
        this.cr_other_charges = normalizeCharges(charges, subtotal, { forceSubtotalAmount: false });
        this._crNotifyOtherChargesChanged();
    },

    getOtherCharges() {
        const subtotal = getOrderSubtotal(this);
        return normalizeCharges(this.cr_other_charges, subtotal, { forceSubtotalAmount: true });
    },

    getOtherChargesTotal() {
        return computeChargesTotal(this.getOtherCharges());
    },

    hasServiceCharge10() {
        return this.getOtherCharges().some((charge) => String(charge?.code || "") === SERVICE_CHARGE_CODE);
    },

    toggleServiceCharge10() {
        this.assertEditable?.();
        if (this.hasServiceCharge10()) {
            this.setOtherCharges([]);
            return false;
        }
        const percent = getServiceChargePercent(this);
        this.setOtherCharges([
            {
                type: "01",
                code: SERVICE_CHARGE_CODE,
                percent,
                description: `Impuesto de servicio ${percent}%`,
                currency: "CRC",
            },
        ]);
        return true;
    },

    get_total_with_tax() {
        const baseTotal = Number(super.get_total_with_tax?.(...arguments) ?? 0);
        return roundAmount(baseTotal + this.getOtherChargesTotal());
    },

    getTotalWithTax() {
        if (super.getTotalWithTax) {
            const baseTotal = Number(super.getTotalWithTax(...arguments) ?? 0);
            return roundAmount(baseTotal + this.getOtherChargesTotal());
        }
        return this.get_total_with_tax(...arguments);
    },

    serializeForORM(opts = {}) {
        const data = super.serializeForORM(...arguments);
        const subtotal = getOrderSubtotal(this);
        const charges = normalizeCharges(this.cr_other_charges, subtotal, { forceSubtotalAmount: true });
        this.cr_other_charges = charges;
        data.cr_other_charges = charges;
        data.other_charges = charges;
        data.otros_cargos = charges;
        data.service_charge_10 = charges.length > 0;
        return data;
    },
});
