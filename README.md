# cr_pos_einvoice

Integración de **Punto de Venta en Odoo 19** con **`l10n_cr_einvoice`** (Costa Rica) mediante un puente robusto para Tiquete/Factura.

## Funcionalidad principal

- **Siempre** crea `pos.order` y, al pagarse:
  - Si **NO** se marca *Facturar*: crea un `account.move` puente para **Tiquete Electrónico** y lo encola para envío FE.
  - Si **sí** se marca *Facturar*: usa la factura `account.move` estándar y la encola para **Factura Electrónica**.
- Flujo **asíncrono** con cron y reintentos para que la validación POS no falle si Hacienda no responde.
- Idempotencia para evitar duplicados de envío y de documento puente por orden POS.
- Botones operativos en `pos.order` y `account.move`:
  - **Send to Hacienda**
  - **Check Hacienda status**
- Pestaña en `pos.order`: **Factura Electrónica (CR)** con tipo, clave, estado y XML.

## Instalación

1. Instala y configura `l10n_cr_einvoice`.
2. Copia `cr_pos_einvoice` en tu ruta de addons.
3. Reinicia Odoo y actualiza lista de aplicaciones.
4. Instala **CR POS Electronic Invoice Bridge**.

## Nota sobre el módulo anterior

`pos_factura_electronica` fue retirado en favor de `cr_pos_einvoice` para evitar solapamientos y mantener una sola implementación estable para Odoo 19.
