# Mapeo de `pos.order` a payload canónico FE (para `l10n_cr_einvoice`)

Este puente **no necesita** que `l10n_cr_einvoice` lea `account.move.line` cuando el documento origen es POS.
Lo que hace es convertir `pos.order`/`pos.order.line` a un payload canónico que luego usa el servicio FE.

## ¿Cómo sabe `l10n_cr_einvoice` los datos de POS?

Desde `cr_pos_einvoice`, al emitir TE, se llama:

- `service.build_payload_from_pos_order(order)`

Ese método normaliza datos POS y entrega una estructura homogénea para la capa FE.

## Tabla de mapeo principal

### Encabezado (`pos.order` → payload)

- `order.id` → `source_id`
- `order.name` → `name`
- `order.date_order` → `date`
- `order.company_id.id` → `company_id`
- `order.partner_id.id` → `partner_id`
- `order.currency_id.id` → `currency_id`
- `order.amount_total - order.amount_tax` → `total_untaxed`
- `order.amount_tax` → `total_tax`
- `order.amount_total` → `total`

### Líneas (`pos.order.line` → `lines[]`)

- `line.product_id.id` → `product_id`
- `line.full_product_name` → `name`
- `line.qty` → `qty`
- `line.price_unit` → `price_unit`
- `line.discount` → `discount`
- `line.tax_ids_after_fiscal_position.ids` → `tax_ids`
- `line.price_subtotal` → `subtotal`
- `line.price_subtotal_incl` → `total`

### Pagos (`pos.payment` → `payments[]`)

- `payment.amount` → `amount`
- `payment.payment_method_id.id` → `payment_method_id`
- `payment.payment_method_id.fp_payment_method` → `fp_payment_method`
- `payment.payment_method_id.fp_sale_condition` → `fp_sale_condition`

## Resultado

Con este mapeo, `l10n_cr_einvoice` puede construir XML desde un **payload canónico** sin depender de que el origen sea factura o POS.
