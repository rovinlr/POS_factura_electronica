/** @odoo-module */

import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";
import { Component } from "@odoo/owl";
import { ProductScreen } from "@point_of_sale/app/screens/product_screen/product_screen";

export class ServiceChargeButton extends Component {
    static template = "cr_pos_einvoice.ServiceChargeButton";

    setup() {
        // Avoid hard dependency on pos_hook (can vary by POS asset version).
        this.pos = this.env?.services?.pos || this.env?.pos || null;
        this.notification = useService("notification");
    }

    get currentOrder() {
        return this.pos.get_order();
    }

    get isActive() {
        return Boolean(this.currentOrder?.getOtherCharges?.().length);
    }

    onClick() {
        const order = this.currentOrder;
        if (!order) return;

        if (this.isActive) {
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
    }
}

ProductScreen.addControlButton({
    component: ServiceChargeButton,
    condition() {
        return true;
    },
});

