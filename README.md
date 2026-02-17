# POS_factura_electronica

Integración de **Punto de Venta en Odoo 19** con **`l10n_cr_einvoice`** (Costa Rica).

## Funcionalidad

- Añade una opción en la configuración del POS para activar factura electrónica CR.
- Usa el check **Facturar** del cobro POS para definir el tipo de documento:
  - Marcado: **Factura electrónica**.
  - Sin marcar: **Tiquete electrónico**.
- Toma **método de pago FE** y **condición de pago FE** desde cada `pos.payment.method`.
- Agrega una pestaña **Documento electrónico** en la orden POS para auditar los datos enviados.
- Al generar factura desde POS, propaga los datos FE hacia `account.move`, publica y dispara firma/envío si el método existe.
- Cuando la orden es tiquete electrónico, intenta disparar el firmado/envío desde la orden POS si el método existe en el entorno.

## Instalación

1. Asegúrate de tener instalado y configurado `l10n_cr_einvoice`.
2. Copia `pos_factura_electronica` en tu ruta de addons.
3. Reinicia Odoo y actualiza lista de aplicaciones.
4. Instala **POS Factura Electrónica CR**.
5. En la configuración del POS, activa **Factura electrónica Costa Rica en POS**.
6. Configura en cada **Método de pago POS** los campos FE:
   - Método de pago FE.
   - Condición de pago FE.

## Nota técnica

El módulo reutiliza el flujo de `l10n_cr_einvoice` y evita pedir datos FE manualmente en el frontend del POS.
