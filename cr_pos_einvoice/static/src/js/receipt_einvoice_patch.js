odoo.define("cr_pos_einvoice.receipt_einvoice_patch", [], function (require) {
    "use strict";

    const { patch } = require("@web/core/utils/patch");

    const loadPosOrder = () => {
        const candidates = [
            "@point_of_sale/app/store/models",
            "@point_of_sale/app/models/pos_order",
        ];

        for (const moduleName of candidates) {
            try {
                const mod = require(moduleName);
                if (mod.PosOrder) {
                    return mod.PosOrder;
                }
                if (mod.Order) {
                    return mod.Order;
                }
                if (mod.default) {
                    return mod.default;
                }
            } catch (_err) {
                // Continue with the next candidate for cross-version POS compatibility.
            }
        }
        return null;
    };

    const PosOrder = loadPosOrder();
    if (!PosOrder) {
        return;
    }

    const firstDefined = (...values) => values.find((value) => value !== undefined && value !== null);
    const normalizeText = (value) => {
        if (value === undefined || value === null) {
            return null;
        }
        const text = String(value).trim();
        return text || null;
    };

    const pickPartner = (order) =>
        firstDefined(
            order.getPartner && order.getPartner(),
            order.get_partner && order.get_partner(),
            order.partner,
            order.partner_id
        ) || null;

    const buildCompanyData = (order, receipt) => {
        const company =
            firstDefined(
                receipt.company,
                receipt.headerData && receipt.headerData.company,
                order.pos && order.pos.company,
                order.company,
                order.company_id
            ) || {};

        return {
            ...company,
            name: normalizeText(firstDefined(company.name, company.company_name, company.display_name)),
            vat: normalizeText(firstDefined(company.vat, company.company_registry, company.identification_id)),
            phone: normalizeText(firstDefined(company.phone, company.mobile)),
            email: normalizeText(company.email),
        };
    };

    const buildPartnerData = (order, receipt) => {
        const partner = firstDefined(receipt.partner, receipt.client, pickPartner(order)) || {};
        return {
            ...partner,
            name: normalizeText(firstDefined(partner.name, partner.display_name)),
            vat: normalizeText(firstDefined(partner.vat, partner.identification_id)),
            email: normalizeText(partner.email),
            phone: normalizeText(firstDefined(partner.phone, partner.mobile)),
        };
    };

    /**
     * Why: Make FE fields + tax amount per line available to the receipt.
     */
    patch(PosOrder.prototype, {
        export_as_JSON() {
            const json = super.export_as_JSON ? super.export_as_JSON(...arguments) : {};
            json.cr_fe_document_type = this.cr_fe_document_type || null;
            json.cr_fe_consecutivo = this.cr_fe_consecutivo || null;
            json.cr_fe_clave = this.cr_fe_clave || null;
            json.cr_fe_status = this.cr_fe_status || null;
            json.fp_payment_method = this.fp_payment_method || null;
            return json;
        },
        init_from_JSON(json) {
            if (super.init_from_JSON) {
                super.init_from_JSON(...arguments);
            }
            this.cr_fe_document_type = json.cr_fe_document_type || null;
            this.cr_fe_consecutivo = json.cr_fe_consecutivo || null;
            this.cr_fe_clave = json.cr_fe_clave || null;
            this.cr_fe_status = json.cr_fe_status || null;
            this.fp_payment_method = json.fp_payment_method || null;
        },
        export_for_printing() {
            const receipt = super.export_for_printing ? super.export_for_printing(...arguments) : {};
            const partner = buildPartnerData(this, receipt);
            const company = buildCompanyData(this, receipt);

            // Normalize common keys used by different POS versions.
            receipt.orderlines = receipt.orderlines || receipt.order_lines || receipt.lines || [];
            receipt.paymentlines = receipt.paymentlines || receipt.payment_lines || [];
            receipt.subtotal = firstDefined(receipt.subtotal, receipt.total_without_tax, receipt.amount_untaxed, "");
            receipt.tax = firstDefined(receipt.tax, receipt.total_tax, receipt.amount_tax, "");
            receipt.total_with_tax = firstDefined(receipt.total_with_tax, receipt.total, receipt.amount_total, "");
            receipt.company = company;
            receipt.partner = partner;

            // Per-line tax amount (numeric). Template will format.
            const orderlines = this.getOrderlines
                ? this.getOrderlines()
                : this.get_orderlines
                  ? this.get_orderlines()
                  : this.lines || [];
            if (Array.isArray(receipt.orderlines)) {
                receipt.orderlines = receipt.orderlines.map((line, idx) => {
                    const ol = orderlines[idx];
                    if (!ol || !ol.get_all_prices) {
                        return { ...line, tax_amount: null };
                    }
                    const prices = ol.get_all_prices();
                    const taxAmount = prices && typeof prices.tax === "number" ? prices.tax : 0;
                    return { ...line, tax_amount: taxAmount };
                });
            }

            if (Array.isArray(receipt.paymentlines)) {
                receipt.paymentlines = receipt.paymentlines.map((paymentLine) => ({
                    ...paymentLine,
                    amount: firstDefined(paymentLine.amount, paymentLine.amount_formatted, ""),
                }));
            }

            receipt.einvoice = {
                document_type: this.cr_fe_document_type || null,
                consecutivo: this.cr_fe_consecutivo || null,
                clave: this.cr_fe_clave || null,
                status: this.cr_fe_status || null,
                payment_method: this.fp_payment_method || null,
                receptor_id:
                    normalizeText(
                        firstDefined(
                            receipt.einvoice && receipt.einvoice.receptor_id,
                            partner.vat,
                            this.getPartner && this.getPartner() && this.getPartner().vat,
                            this.get_partner && this.get_partner() && this.get_partner().vat
                        )
                    ) || null,
            };
            return receipt;
        },
    });
});
