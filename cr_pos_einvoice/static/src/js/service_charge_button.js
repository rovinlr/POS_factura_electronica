/** @odoo-module */

import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { ControlButtons } from "@point_of_sale/app/screens/product_screen/control_buttons/control_buttons";

patch(ControlButtons.prototype, {
    setup() {
        super.setup(...arguments);
        this.pos = this.env?.services?.pos || this.env?.pos || null;
        this.notification = useService("notification");
    },

    get currentOrder() {
        return this.pos?.get_order?.() || null;
    },

    get isServiceChargeActive() {
        return Boolean(this.currentOrder?.getOtherCharges?.().length);
    },

    onClickServiceCharge() {
        const order = this.currentOrder;
        if (!order) {
            return;
        }

        if (this.isServiceChargeActive) {
            order.setOtherCharges([]);
            this.notification.add(_t("Impuesto de servicio removido."), { type: "warning" });
            return;
        }

        order.setOtherCharges([
            {
                type: "01",
                code: "06",
                percent: 10,
                description: _t("Impuesto de servicio 10%"),
                currency: "CRC",
            },
        ]);
        this.notification.add(_t("Impuesto de servicio aplicado (10%)."), { type: "success" });
    },
});
