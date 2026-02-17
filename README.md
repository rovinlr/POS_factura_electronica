# POS_factura_electronica

Integración de **Punto de Venta en Odoo 19** con **`l10n_cr_einvoice`** (Costa Rica).

## Funcionalidad

- Añade una opción en la configuración del POS para activar factura electrónica CR en el flujo de cobro.
- En la pantalla de pago del POS agrega el botón **Datos FE CR** para seleccionar:
  - Tipo de documento: **Factura electrónica**, **Tiquete electrónico** o **Nota de crédito**.
  - Método de pago FE.
  - Condición de pago FE.
- Al facturar desde la orden POS, propaga estos datos hacia `account.move` (si los campos del módulo `l10n_cr_einvoice` existen), publica la factura y dispara firma/envío electrónico.

## Instalación

1. Asegúrate de tener instalado y configurado `l10n_cr_einvoice`.
2. Copia `pos_factura_electronica` en tu ruta de addons.
3. Reinicia Odoo y actualiza lista de aplicaciones.
4. Instala **POS Factura Electrónica CR**.
5. En la configuración del POS, activa **Factura electrónica Costa Rica en POS**.

## Nota técnica

El módulo está diseñado para reutilizar el flujo ya implementado por `l10n_cr_einvoice`; únicamente captura y traslada datos del contexto POS al documento contable generado.
