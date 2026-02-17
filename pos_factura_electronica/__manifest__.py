{
    "name": "POS Factura Electrónica CR",
    "summary": "Integra POS con l10n_cr_einvoice para emisión electrónica en Costa Rica",
    "version": "19.0.2.0.0",
    "category": "Point of Sale",
    "author": "Tu Empresa",
    "license": "LGPL-3",
    "depends": [
        "point_of_sale",
        "account",
        "l10n_cr_einvoice",
    ],
    "data": [
        "security/ir.model.access.csv",
        "views/pos_config_views.xml",
    ],
    "assets": {
        "point_of_sale._assets_pos": [
            "pos_factura_electronica/static/src/js/pos_fe_selection.js",
            "pos_factura_electronica/static/src/xml/pos_fe_selection.xml",
        ],
    },
    "installable": True,
    "application": False,
}
