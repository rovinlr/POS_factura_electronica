# Mapeo POS -> FE (TE y NC)

Este puente usa **la misma lógica central del servicio `l10n_cr_einvoice`** para construir XML, firmar, enviar y adjuntar documentos.

## Regla
- Si el pedido POS no está facturado, se emite desde `pos.order`.
- Tipo de documento POS:
  - `te` si `amount_total >= 0`
  - `nc` si `amount_total < 0`

## Origen de datos

### Encabezado (`pos.order`)
- `name`, `date_order`, `company_id`, `partner_id`, `currency_id`
- `amount_total`, `amount_tax`

### Líneas (`pos.order.line`)
- `full_product_name`, `qty`, `price_unit`, `discount`
- `tax_ids_after_fiscal_position`
- `price_subtotal`, `price_subtotal_incl`

### Pagos (`pos.payment`)
- `payment_method_id.fp_sale_condition`
- `payment_method_id.fp_payment_method`

## Dónde está implementado
- Payload POS: `l10n_cr_einvoice/services/einvoice_service.py` (`build_payload_from_pos_order`).
- Servicio público para POS (TE/NC): `l10n_cr_einvoice/models/einvoice_service.py`
  - `build_pos_xml_from_order`
  - `build_te_xml_from_pos`
  - `build_nc_xml_from_pos`
  - `send_to_hacienda`
  - `consult_status`

## Nota
El XML de POS para TE/NC se genera con `EInvoiceService.generate_xml(...)` del base para mantener una ruta homogénea de creación/firma/envío/adjuntos.
