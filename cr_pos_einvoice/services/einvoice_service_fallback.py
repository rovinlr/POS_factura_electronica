"""Fallback FE service for POS flows when l10n_cr_einvoice service path is unavailable."""

from odoo.addons.l10n_cr_einvoice.services.einvoice_service import EInvoiceService as BaseEInvoiceService


class EInvoiceService(BaseEInvoiceService):
    """Alias class to keep backward compatible imports from POS adapter."""

    pass
