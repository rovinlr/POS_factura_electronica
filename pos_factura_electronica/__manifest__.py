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
        "views/pos_payment_method_views.xml",
        "views/pos_order_views.xml",
    ],
    "installable": True,
    "application": False,
}
