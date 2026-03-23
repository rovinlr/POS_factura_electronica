/** @odoo-module */

import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";
import { AlertDialog, ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { NumberPopup } from "@point_of_sale/app/components/popups/number_popup/number_popup";
import { ask, makeAwaitable } from "@point_of_sale/app/utils/make_awaitable_dialog";
import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";

const DEFAULT_SERVICE_CHARGE_PERCENT = 10;
const EPSILON = 0.00001;

const toNumber = (value, fallback = 0) => {
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
};

const getAllPrices = (line) => line?.get_all_prices?.() || line?.getAllPrices?.() || null;

const getQty = (line) =>
    toNumber(
        line?.get_quantity?.() ?? line?.getQuantity?.() ?? line?.quantity ?? line?.qty ?? 1,
        1
    );

const getDiscount = (line) =>
    toNumber(line?.get_discount?.() ?? line?.getDiscount?.() ?? line?.discount ?? 0, 0);

const getUnitPrice = (line) =>
    toNumber(
        line?.get_unit_price?.() ??
            line?.getUnitPrice?.() ??
            line?.price_unit ??
            line?.price ??
            line?.unit_price ??
            0,
        0
    );

const roundCurrency = (value) => Math.round(toNumber(value) * 100000) / 100000;

const resolveMany2oneId = (value) => {
    if (!value) return null;
    if (typeof value === "number") return value;
    if (Array.isArray(value)) return typeof value[0] === "number" ? value[0] : null;
    if (typeof value === "object" && typeof value.id === "number") return value.id;
    return null;
};

const amountsAreEquivalent = (left, right) => Math.abs(toNumber(left) - toNumber(right)) < EPSILON;

const getLineSubtotalWithoutTax = (line) => {
    const subtotalFromMethod = toNumber(
        line?.get_price_without_tax?.() ?? line?.getPriceWithoutTax?.(),
        NaN
    );
    if (Number.isFinite(subtotalFromMethod)) {
        return subtotalFromMethod;
    }

    const allPrices = getAllPrices(line);
    if (allPrices && typeof allPrices === "object") {
        const subtotalFromAllPrices = toNumber(
            allPrices.priceWithoutTax ??
                allPrices.price_without_tax ??
                allPrices.total_without_tax ??
                allPrices.subtotal_without_tax ??
                allPrices.subtotalWithoutTax,
            NaN
        );
        if (Number.isFinite(subtotalFromAllPrices)) {
            return subtotalFromAllPrices;
        }
    }

    const subtotalFromFields = toNumber(
        line?.price_subtotal ?? line?.priceSubtotal ?? line?.subtotal,
        NaN
    );
    if (Number.isFinite(subtotalFromFields)) {
        return subtotalFromFields;
    }

    const qty = getQty(line);
    const unitPrice = getUnitPrice(line);
    const discount = getDiscount(line);
    const subtotalFromFormula = unitPrice * qty * (1 - discount / 100);
    if (Number.isFinite(subtotalFromFormula)) {
        return subtotalFromFormula;
    }

    const subtotalFromDisplay = toNumber(
        line?.get_display_price?.() ??
            line?.getDisplayPrice?.() ??
            line?.price_subtotal_incl ??
            line?.priceSubtotalIncl,
        NaN
    );
    return Number.isFinite(subtotalFromDisplay) ? subtotalFromDisplay : 0;
};

patch(PaymentScreen.prototype, {
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

    getCurrentTipAmount(order = null) {
        const activeOrder = order || this.currentOrder || this.pos?.get_order?.();
        if (!activeOrder) return 0;
        return toNumber(
            activeOrder?.get_tip?.() ??
                activeOrder?.getTip?.() ??
                activeOrder?.tip_amount ??
                activeOrder?.tip ??
                0,
            0
        );
    },

    async addTipProductCompat(order, tipProduct, options) {
        const failures = [];
        const baselineTipLineCount = this.getTipLines(order).length;
        const expectedAmount = toNumber(options?.price, 0);
        const baselineTipAmount = this.getCurrentTipAmount(order);

        const tipWasApplied = () =>
            this.getTipLines(order).length > baselineTipLineCount ||
            amountsAreEquivalent(this.getCurrentTipAmount(order), expectedAmount) ||
            (!amountsAreEquivalent(baselineTipAmount, expectedAmount) &&
                this.getCurrentTipAmount(order) > 0 &&
                amountsAreEquivalent(this.getCurrentTipAmount(order), expectedAmount));

        const tryCall = async (label, fn) => {
            try {
                await fn();
                if (tipWasApplied()) {
                    return true;
                }
                failures.push(_t("%s no agregó la propina esperada.", label));
                return false;
            } catch (error) {
                failures.push(`${label}: ${error?.message || error}`);
                return false;
            }
        };

        if (order?.set_tip) {
            if (await tryCall("order.set_tip(amount)", async () => order.set_tip(toNumber(options?.price, 0)))) {
                return;
            }
        }
        if (order?.setTip) {
            if (await tryCall("order.setTip(amount)", async () => order.setTip(toNumber(options?.price, 0)))) {
                return;
            }
        }
        if (order?.add_product) {
            if (await tryCall("order.add_product(product, options)", async () => order.add_product(tipProduct, options))) {
                return;
            }
        }
        if (order?.addProduct) {
            if (await tryCall("order.addProduct(product, options)", async () => order.addProduct(tipProduct, options))) {
                return;
            }
        }

        throw new Error(
            _t(
                "No se pudo agregar la línea de propina. Detalle técnico: %s",
                failures.length ? failures.join(" | ") : _t("No existe un método compatible para agregar propina en esta versión de POS.")
            )
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

        const tipSubtotal = this.getTipLines(activeOrder).reduce((acc, line) => {
            const lineSubtotal = getLineSubtotalWithoutTax(line);
            return acc + (Number.isFinite(lineSubtotal) ? lineSubtotal : 0);
        }, 0);

        let subtotalBase = toNumber(
            activeOrder.get_total_without_tax?.() ?? activeOrder.getTotalWithoutTax?.(),
            0
        );

        if (subtotalBase <= 0) {
            const totalWithTax = toNumber(
                activeOrder.get_total_with_tax?.() ?? activeOrder.getTotalWithTax?.(),
                NaN
            );
            const totalTax = toNumber(activeOrder.get_total_tax?.() ?? activeOrder.getTotalTax?.(), NaN);
            if (Number.isFinite(totalWithTax) && Number.isFinite(totalTax) && totalWithTax > 0) {
                subtotalBase = totalWithTax - totalTax;
            }
        }

        if (subtotalBase <= 0) {
            const tipProductId = this.getServiceTipProductId();
            subtotalBase = lines.reduce((acc, line) => {
                const productId = line?.product?.id || line?.get_product?.()?.id || line?.getProduct?.()?.id;
                if (tipProductId && productId === tipProductId) {
                    return acc;
                }
                const lineSubtotal = getLineSubtotalWithoutTax(line);
                if (!Number.isFinite(lineSubtotal)) {
                    return acc;
                }
                return acc + lineSubtotal;
            }, 0);
            return Math.max(0, subtotalBase);
        }

        return Math.max(0, subtotalBase - tipSubtotal);
    },

    getExpectedServiceAmount(order = null) {
        const base = this.getOrderSubtotalWithoutTaxExcludingTip(order);
        const percent = this.getServiceChargePercent();
        if (base <= 0 || percent <= 0) return 0;
        return roundCurrency(base * (percent / 100));
    },

    async askServiceChargeAmount(order, suggestedAmount) {
        const subtotal = this.getOrderSubtotalWithoutTaxExcludingTip(order);
        const payload = await makeAwaitable(this.dialog, NumberPopup, {
            title: _t(
                "%s (Subtotal sin impuestos: %s)",
                this.getServiceChargeLabel(),
                roundCurrency(subtotal).toFixed(2)
            ),
            startingValue: String(suggestedAmount),
        });
        if (payload === undefined) return null;
        const amount = roundCurrency(toNumber(payload, 0));
        return amount > 0 ? amount : null;
    },

    isServiceChargeApplied() {
        const order = this.currentOrder || this.pos?.get_order?.();
        if (!order) return false;
        const expectedAmount = this.getExpectedServiceAmount(order);
        if (expectedAmount <= 0) return false;
        if (amountsAreEquivalent(this.getCurrentTipAmount(order), expectedAmount)) {
            return true;
        }
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

        const suggestedAmount = this.getExpectedServiceAmount(order);
        if (suggestedAmount <= 0) {
            this.dialog.add(AlertDialog, {
                title: _t("Monto inválido"),
                body: _t("No se pudo calcular el cargo de servicio porque el subtotal sin impuestos es cero."),
            });
            return;
        }
        const expectedAmount = await this.askServiceChargeAmount(order, suggestedAmount);
        if (!expectedAmount) {
            return;
        }

        const tipLines = this.getTipLines(order);
        const allLinesAlreadyMatch = tipLines.length > 0 && tipLines.every((line) =>
            amountsAreEquivalent(line?.get_unit_price?.() ?? line?.getUnitPrice?.() ?? 0, expectedAmount)
        );

        if (allLinesAlreadyMatch) {
            await this.applyServiceChargeLine(order, tipProduct, expectedAmount);
            return;
        }

        if (tipLines.length) {
            const confirmed = await ask(this.dialog, {
                title: _t("Reemplazar propina actual"),
                body: _t(
                    "Ya existe una propina distinta. ¿Desea reemplazarla por Servicio %s%% (monto calculado automáticamente)?",
                    this.getServiceChargePercent()
                ),
                confirmText: _t("Reemplazar"),
                cancelText: _t("Cancelar"),
            }, {}, ConfirmationDialog);
            if (!confirmed) {
                return;
            }
        }


        try {
            await this.applyServiceChargeLine(order, tipProduct, expectedAmount);
        } catch (error) {
            this.dialog.add(AlertDialog, {
                title: _t("No se pudo aplicar el servicio"),
                body: error?.message || _t("Ocurrió un error inesperado al agregar la línea de propina."),
            });
        }
    },

    async applyServiceChargeLine(order, tipProduct, expectedAmount) {
        if (order?.set_tip) {
            order.set_tip(0);
        } else if (order?.setTip) {
            order.setTip(0);
        }

        const tipLines = this.getTipLines(order);
        for (const line of [...tipLines]) {
            order.removeOrderline?.(line);
        }

        await this.addTipProductCompat(order, tipProduct, {
            quantity: 1,
            price: expectedAmount,
        });

        const newTipLines = this.getTipLines(order);
        if (!newTipLines.length && amountsAreEquivalent(this.getCurrentTipAmount(order), expectedAmount)) {
            return;
        }
        if (!newTipLines.length) {
            throw new Error(
                _t(
                    "El POS no devolvió ninguna línea de propina después de agregar el producto configurado."
                )
            );
        }
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

/*
Manual checklist (Odoo v19 + POS Restaurant):
1) Orden con 1 producto sin impuestos especiales: subtotal sin impuestos > 0, Servicio 10% > 0, sin error.
2) Orden con 2 productos (uno con descuento 10%): Servicio 10% sobre subtotal neto sin impuestos.
3) Orden con impuestos: base sin impuestos = total_with_tax - total_tax cuando get_total_without_tax falla.
4) POS Restaurant (mesa), enviar a cocina y pagar: Servicio 10% funciona sin diferencias.
5) Orden vacía o subtotal real 0: se mantiene error de "Monto inválido" (comportamiento esperado).
*/
