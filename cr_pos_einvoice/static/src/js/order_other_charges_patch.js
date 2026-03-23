/** @odoo-module */

import { patch } from "@web/core/utils/patch";
import { PosOrder } from "@point_of_sale/app/models/pos_order";
import { ControlButtons } from "@point_of_sale/app/screens/product_screen/control_buttons/control_buttons";

const SERVICE_CHARGE_CODE = "06";
const SERVICE_CHARGE_RATE = 0.1;

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
        this.setOtherCharges([
            {
                type: "01",
                code: SERVICE_CHARGE_CODE,
                percent: 10,
                description: "Impuesto de servicio 10%",
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

patch(ControlButtons.prototype, {
    get currentOrder() {
        return this.pos?.get_order?.() || null;
    },

    get serviceChargeButtonLabel() {
        return this.currentOrder?.hasServiceCharge10?.() ? "Servicio 10%: ON" : "Servicio 10%: OFF";
    },

    get serviceChargeButtonTitle() {
        return this.currentOrder?.hasServiceCharge10?.()
            ? "Quitar impuesto de servicio 10%"
            : "Aplicar impuesto de servicio 10%";
    },

    get isServiceCharge10Active() {
        return Boolean(this.currentOrder?.hasServiceCharge10?.());
    },

    onClickServiceCharge10() {
        const order = this.currentOrder;
        if (!order) {
            return;
        }
        order.toggleServiceCharge10?.();
        this.render();
    },
});
