# cr_pos_einvoice

Integración de **Punto de Venta en Odoo 19** con **`l10n_cr_einvoice`** (Costa Rica) mediante un adaptador POS orientado a mantener el comportamiento nativo de Odoo.

## Enfoque de arquitectura

- `l10n_cr_einvoice` es la **fuente única de verdad** para XML, firma, envío a Hacienda, adjuntos y estados.
- `cr_pos_einvoice` se limita a:
  - decidir flujo de emisión (TE desde `pos.order` o FE desde `account.move`),
  - preparar el payload POS,
  - delegar al servicio central FE.
- La regla crítica para detectar factura real usa **únicamente** `account.move` con `move_type in ('out_invoice', 'out_refund')`.
- **No** se usa `session_move_id` como señal de factura.

## Documento de diseño

Ver propuesta detallada en:

- `docs/arquitectura_pos_fe_cr_v44.md`
- `docs/checklist_integracion_electroniccrinvoice.md`

## Instalación

1. Instala y configura `l10n_cr_einvoice`.
2. Copia `cr_pos_einvoice` en tu ruta de addons.
3. Reinicia Odoo y actualiza lista de aplicaciones.
4. Instala **CR POS Electronic Invoice Bridge**.
