# Mapeo TE 4.4 desde POS

Este puente construye el XML de **Tiquete Electrónico (TE)** desde `pos.order`, `pos.order.line` y `pos.payment`.

## Fuentes de datos

### Encabezado
- `pos.order.name` -> consecutivo lógico del pedido.
- `pos.order.date_order` -> `FechaEmision`.
- `pos.order.company_id` -> `Emisor`.
- `pos.order.partner_id` -> `Receptor` (si existe).
- `pos.order.currency_id`, `amount_total`, `amount_tax` -> `ResumenFactura`.

### Detalle (`LineaDetalle`)
- `pos.order.line.full_product_name` -> `Detalle`.
- `pos.order.line.qty` -> `Cantidad`.
- `pos.order.line.price_unit` -> `PrecioUnitario`.
- `pos.order.line.discount` -> `MontoDescuento` (calculado).
- `pos.order.line.tax_ids_after_fiscal_position` -> impuestos.
- `pos.order.line.price_subtotal` / `price_subtotal_incl` -> subtotales y total línea.

### Pagos
- `pos.payment.payment_method_id.fp_sale_condition` -> `CondicionVenta`.
- `pos.payment.payment_method_id.fp_payment_method` -> `MedioPago`.

## Código
- Normalización desde POS: `l10n_cr_einvoice/services/einvoice_service.py` (`build_payload_from_pos_order`).
- Sección TE 4.4: `_build_te44_payload_from_pos_order`.
- Render XML TE: `_generate_te44_xml`.
