/** @odoo-module */

import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { AlertDialog, ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";

const DEFAULT_SERVICE_CHARGE_PERCENT = 10;
const EPSILON = 0.00001;

const toNumber = (value, fallback = 0) => {
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
};

const roundCurrency = (value) => Math.round(toNumber(value) * 100000) / 100000;

const resolveMany2oneId = (value) => {
    if (!value) return null;
    if (typeof value === "number") return value;
    if (Array.isArray(value)) return typeof value[0] === "number" ? value[0] : null;
    if (typeof value === "object" && typeof value.id === "number") return value.id;
    return null;
};

const amountsAreEquivalent = (left, right) => Math.abs(toNumber(left) - toNumber(right)) < EPSILON;

const getLineSubtotalWithoutTax = (line) =>
    toNumber(
        line?.get_price_without_tax?.() ??
            line?.getPriceWithoutTax?.() ??
            line?.price_subtotal ??
            line?.get_display_price?.() ??
            line?.getDisplayPrice?.() ??
            line?.get_unit_price?.() ??
            line?.getUnitPrice?.() ??
            0
    );

patch(PaymentScreen.prototype, {
    setup() {
        super.setup(...arguments);
        this.dialog = this.dialog || useService("dialog");

        const percent = this.getServiceChargePercent();
        const tipProductId = this.getServiceTipProductId();
        if (tipProductId && percent > 0) {
            this.pos.config.tip_product_id = false;
            this.pos.config.pos_tip_product_id = false;
        }
    },

    getServiceChargePercent() {
        const value = toNumber(this.pos?.config?.cr_service_charge_percent, DEFAULT_SERVICE_CHARGE_PERCENT);
        return value > 0 ? value : DEFAULT_SERVICE_CHARGE_PERCENT;
    },

    getServiceTipProductId() {
        return (
            resolveMany2oneId(this.pos?.config?.cr_tip_product_id) ||
            resolveMany2oneId(this.pos?.config?.tip_product_id) ||
            resolveMany2oneId(this.pos?.config?.pos_tip_product_id)
        );
    },

    getServiceChargeLabel() {
        const percent = this.getServiceChargePercent();
        return _t("Servicio %s%%", percent);
    },

    shouldShowServiceChargeButton() {
        return this.getServiceChargePercent() > 0;
    },

    getTipProduct() {
        const tipProductId = this.getServiceTipProductId();
        if (!tipProductId) return null;
        return (
            this.pos?.db?.get_product_by_id?.(tipProductId) ||
            this.pos?.models?.["product.product"]?.get?.(tipProductId) ||
            this.pos?.models?.["product.product"]?.find?.((product) => product.id === tipProductId) ||
            null
        );
    },

    getTipLines(order = null) {
        const activeOrder = order || this.currentOrder || this.pos?.get_order?.();
        if (!activeOrder) return [];
        const tipProductId = this.getServiceTipProductId();
        if (!tipProductId) return [];
        const lines = activeOrder.get_orderlines?.() || activeOrder.getOrderlines?.() || [];
        return lines.filter((line) => {
            const productId = line?.product?.id || line?.get_product?.()?.id || line?.getProduct?.()?.id;
            return productId === tipProductId;
        });
    },

    getOrderSubtotalWithoutTaxExcludingTip(order = null) {
        const activeOrder = order || this.currentOrder || this.pos?.get_order?.();
        if (!activeOrder) return 0;
        const lines = activeOrder.get_orderlines?.() || activeOrder.getOrderlines?.() || [];
        const tipProductId = this.getServiceTipProductId();
        const subtotalFromLines = lines.reduce((acc, line) => {
            const productId = line?.product?.id || line?.get_product?.()?.id || line?.getProduct?.()?.id;
            if (tipProductId && productId === tipProductId) {
                return acc;
            }
            return acc + getLineSubtotalWithoutTax(line);
        }, 0);
        if (subtotalFromLines > 0) {
            return subtotalFromLines;
        }

        const subtotalFromOrder = toNumber(
            activeOrder.get_total_without_tax?.() ?? activeOrder.getTotalWithoutTax?.() ?? 0
        );
        if (subtotalFromOrder <= 0) {
            return 0;
        }

        const tipSubtotal = this.getTipLines(activeOrder).reduce((acc, line) => acc + getLineSubtotalWithoutTax(line), 0);
        return Math.max(0, subtotalFromOrder - tipSubtotal);
    },

    getExpectedServiceAmount(order = null) {
        const base = this.getOrderSubtotalWithoutTaxExcludingTip(order);
        const percent = this.getServiceChargePercent();
        if (base <= 0 || percent <= 0) return 0;
        return roundCurrency(base * (percent / 100));
    },

    isServiceChargeApplied() {
        const order = this.currentOrder || this.pos?.get_order?.();
        if (!order) return false;
        const expectedAmount = this.getExpectedServiceAmount(order);
        if (expectedAmount <= 0) return false;
        return this.getTipLines(order).some((line) =>
            amountsAreEquivalent(line?.get_unit_price?.() ?? line?.getUnitPrice?.() ?? 0, expectedAmount)
        );
    },

    async onClickServiceChargeButton() {
        const order = this.currentOrder || this.pos?.get_order?.();
        if (!order) {
            this.dialog.add(AlertDialog, {
                title: _t("No hay orden activa"),
                body: _t("No existe una orden disponible para aplicar el cargo por servicio."),
            });
            return;
        }

        const tipProductId = this.getServiceTipProductId();
        if (!tipProductId) {
            this.dialog.add(AlertDialog, {
                title: _t("Configuración incompleta"),
                body: _t("Configure el producto de propina en el POS para aplicar Servicio %s%%.", this.getServiceChargePercent()),
            });
            return;
        }

        const tipProduct = this.getTipProduct();
        if (!tipProduct) {
            this.dialog.add(AlertDialog, {
                title: _t("Producto no disponible"),
                body: _t("El producto de propina configurado no está cargado en esta sesión POS."),
            });
            return;
        }

        const lines = order.get_orderlines?.() || order.getOrderlines?.() || [];
        if (!lines.length) {
            this.dialog.add(AlertDialog, {
                title: _t("Sin productos"),
                body: _t("Agregue al menos un producto antes de aplicar Servicio %s%%.", this.getServiceChargePercent()),
            });
            return;
        }

        const expectedAmount = this.getExpectedServiceAmount(order);
        if (expectedAmount <= 0) {
            this.dialog.add(AlertDialog, {
                title: _t("Monto inválido"),
                body: _t("No se pudo calcular el cargo de servicio porque el subtotal sin impuestos es cero."),
            });
            return;
        }

        const tipLines = this.getTipLines(order);
        const allLinesAlreadyMatch = tipLines.length > 0 && tipLines.every((line) =>
            amountsAreEquivalent(line?.get_unit_price?.() ?? line?.getUnitPrice?.() ?? 0, expectedAmount)
        );

        if (allLinesAlreadyMatch) {
            for (const line of [...tipLines]) {
                order.removeOrderline?.(line);
            }
            return;
        }

        if (tipLines.length) {
            this.dialog.add(ConfirmationDialog, {
                title: _t("Reemplazar propina actual"),
                body: _t(
                    "Ya existe una propina distinta. ¿Desea reemplazarla por Servicio %s%% (monto calculado automáticamente)?",
                    this.getServiceChargePercent()
                ),
                confirmLabel: _t("Reemplazar"),
                confirm: () => this.applyServiceChargeLine(order, tipProduct, expectedAmount),
            });
            return;
        }

        this.applyServiceChargeLine(order, tipProduct, expectedAmount);
    },

    applyServiceChargeLine(order, tipProduct, expectedAmount) {
        const tipLines = this.getTipLines(order);
        for (const line of [...tipLines]) {
            order.removeOrderline?.(line);
        }
        order.add_product?.(tipProduct, {
            quantity: 1,
            price: expectedAmount,
            extras: { price_manually_set: true },
        });
        const newTipLines = this.getTipLines(order);
        const [first, ...duplicates] = newTipLines;
        for (const duplicateLine of duplicates) {
            order.removeOrderline?.(duplicateLine);
        }
        if (first?.set_unit_price) {
            first.set_unit_price(expectedAmount);
        }
        if (first?.set_quantity) {
            first.set_quantity(1);
        }
    },
});
