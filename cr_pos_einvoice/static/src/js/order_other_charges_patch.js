/** @odoo-module */

import { patch } from "@web/core/utils/patch";
import { PosOrder } from "@point_of_sale/app/models/pos_order";

const SERVICE_CHARGE_CODE = "06";
const SERVICE_CHARGE_RATE = 0.1;

const roundAmount = (value) => Math.round((Number(value) || 0) * 100000) / 100000;

const getLinesSubtotal = (order) => {
    const lines = order?.get_orderlines?.() || order?.getOrderlines?.() || [];
    if (!Array.isArray(lines) || !lines.length) {
        return 0;
    }
    return roundAmount(
        lines.reduce((acc, line) => {
            const lineSubtotal = Number(
                line?.get_price_without_tax?.() ??
                    line?.getPriceWithoutTax?.() ??
                    line?.price_subtotal ??
                    line?.price_subtotal_incl ??
                    0
            );
            return acc + (Number.isFinite(lineSubtotal) ? lineSubtotal : 0);
        }, 0)
    );
};

const getOrderSubtotal = (order) => {
    const subtotal = Number(order?.get_total_without_tax?.() ?? order?.getTotalWithoutTax?.() ?? 0);
    if (Number.isFinite(subtotal) && subtotal > 0) {
        return roundAmount(subtotal);
    }
    return getLinesSubtotal(order);
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
        const amount = Number(
            forceSubtotalAmount ? computed?.amount : (item.amount ?? item.monto ?? computed?.amount)
        );
        if (!Number.isFinite(amount) || amount <= 0) continue;
        normalized.push({
            type: String(item.type ?? item.tipo ?? item.charge_type ?? "01"),
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

const getParentPrototype = () => Object.getPrototypeOf(PosOrder.prototype);

const callParentMethod = (instance, methodName, args = []) => {
    let proto = getParentPrototype();
    while (proto) {
        const descriptor = Object.getOwnPropertyDescriptor(proto, methodName);
        if (descriptor?.value && typeof descriptor.value === "function") {
            return descriptor.value.call(instance, ...args);
        }
        proto = Object.getPrototypeOf(proto);
    }
    return undefined;
};

const callParentGetter = (instance, getterName, fallback = 0) => {
    let proto = getParentPrototype();
    while (proto) {
        const descriptor = Object.getOwnPropertyDescriptor(proto, getterName);
        if (descriptor?.get) {
            return descriptor.get.call(instance);
        }
        proto = Object.getPrototypeOf(proto);
    }
    return fallback;
};

const markOrderAsDirty = (order) => {
    order._markDirty?.();
};

patch(PosOrder.prototype, {
    setup(vals) {
        super.setup(vals);
        const source = vals || {};
        this.cr_other_charges = normalizeCharges(
            source.cr_other_charges ?? source.cr_other_charges_json ?? this.cr_other_charges
        );
    },

    getSubtotalBeforeOtherCharges() {
        return getOrderSubtotal(this);
    },

    setOtherCharges(charges) {
        this.assertEditable?.();
        const subtotal = this.getSubtotalBeforeOtherCharges();
        this.cr_other_charges = normalizeCharges(charges, subtotal, { forceSubtotalAmount: false });
        markOrderAsDirty(this);
    },

    getOtherCharges() {
        const subtotal = this.getSubtotalBeforeOtherCharges();
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
        this.setOtherCharges([
            {
                type: "01",
                code: SERVICE_CHARGE_CODE,
                percent: 10,
                description: "Impuesto de servicio 10%",
                currency: this.currency?.name || "CRC",
            },
        ]);
        return true;
    },

    get priceIncl() {
        const basePriceIncl = Number(callParentGetter(this, "priceIncl", 0));
        return roundAmount(basePriceIncl + this.getOtherChargesTotal());
    },

    get totalDue() {
        const baseTotalDue = Number(callParentGetter(this, "totalDue", 0));
        const basePriceIncl = Number(callParentGetter(this, "priceIncl", 0));
        const adjustedPriceIncl = this.priceIncl;
        return roundAmount(baseTotalDue + (adjustedPriceIncl - basePriceIncl));
    },

    setOrderPrices() {
        callParentMethod(this, "setOrderPrices", [...arguments]);
        this.amount_total = roundAmount(Number(this.amount_total || 0) + this.getOtherChargesTotal());
    },

    export_for_printing() {
        const data = callParentMethod(this, "export_for_printing", [...arguments]) || {};
        data.cr_other_charges = this.getOtherCharges();
        data.cr_other_charges_total = this.getOtherChargesTotal();
        data.cr_total_with_other_charges = this.priceIncl;
        data.cr_subtotal_before_other_charges = this.getSubtotalBeforeOtherCharges();
        return data;
    },

    exportForPrinting() {
        const data =
            callParentMethod(this, "exportForPrinting", [...arguments]) || this.export_for_printing(...arguments);
        data.cr_other_charges = this.getOtherCharges();
        data.cr_other_charges_total = this.getOtherChargesTotal();
        data.cr_total_with_other_charges = this.priceIncl;
        data.cr_subtotal_before_other_charges = this.getSubtotalBeforeOtherCharges();
        return data;
    },

    serializeForORM(opts = {}) {
        const data = super.serializeForORM(opts);
        const subtotal = this.getSubtotalBeforeOtherCharges();
        const charges = this.getOtherCharges();

        data.amount_subtotal = subtotal;
        data.subtotal = subtotal;
        data.total_without_tax = subtotal;
        data.service_charge_10 = this.hasServiceCharge10();

        if (charges.length) {
            this.cr_other_charges = normalizeCharges(charges, subtotal, { forceSubtotalAmount: true });
            data.cr_other_charges = this.cr_other_charges;
            data.other_charges = this.cr_other_charges;
            data.otros_cargos = this.cr_other_charges;
        }

        return data;
    },
});
