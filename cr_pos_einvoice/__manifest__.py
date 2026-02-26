{
    "name": "CR POS Electronic Invoice Bridge",
    "summary": "Puente POS -> FE CR (Tiquete/Factura) reutilizando l10n_cr_einvoice",
    "version": "19.0.1.1.0",
    "category": "Point of Sale",
    "author": "FenixCr Solutions",
    "license": "LGPL-3",
    "depends": ["point_of_sale", "account", "l10n_cr_einvoice"],
    "data": [
        "security/ir.model.access.csv",
        "data/cron.xml",
        "views/hacienda_pos_menu_views.xml",
        "views/pos_config_views.xml",
        "views/pos_payment_method_views.xml",
        "views/pos_order_views.xml",
    ],
    "assets": {
        "point_of_sale._assets_pos": [
            "cr_pos_einvoice/static/src/js/receipt_einvoice_patch.js",
            "cr_pos_einvoice/static/src/xml/cr_pos_receipt.xml",
            "cr_pos_einvoice/static/src/scss/cr_pos_receipt.scss",
        ],
    },
    "installable": True,
    "application": False,
}
