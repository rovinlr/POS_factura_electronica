# cr_pos_einvoice

Integración de **Punto de Venta en Odoo 19** con **`l10n_cr_einvoice`** (Costa Rica) mediante un adaptador POS orientado a mantener el comportamiento nativo de Odoo.

## Enfoque de arquitectura

- `l10n_cr_einvoice` es la **fuente única de verdad** para XML, firma, envío a Hacienda, adjuntos y estados.
- `cr_pos_einvoice` se limita a:
  - decidir flujo de emisión (TE desde `pos.order` o FE desde `account.move`),
  - preparar payload POS,
  - delegar al servicio central FE (`l10n_cr.einvoice.service`).
- La regla crítica para detectar factura real usa **únicamente** `account.move` con `move_type in ('out_invoice', 'out_refund')`.
- **No** se usa `session_move_id` como señal de factura.

> Este repositorio **no incluye** la implementación productiva de FE de Costa Rica.
> Debes instalar un `l10n_cr_einvoice` real en tu instancia.

## Instalación

1. Instala y configura `l10n_cr_einvoice` productivo en Odoo.
2. Copia `cr_pos_einvoice` en tu ruta de addons.
3. Reinicia Odoo y actualiza lista de aplicaciones.
4. Instala **CR POS Electronic Invoice Bridge**.

## Validaciones incorporadas en el puente POS

- Idempotencia por compañía.
- Secuencia de consecutivo por compañía/tipo documental usando `ir.sequence`.
- Validaciones previas de datos mínimos del emisor y pagos POS.
- Reintentos con backoff y trazabilidad de error (`cr_fe_error_code`, `cr_fe_last_error`).

## Otros cargos en POS (FE CR v4.4)

El puente ahora acepta otros cargos en la orden POS para enviarlos al payload canónico FE.

### Cómo asignarlos desde POS (JS/OWL)

En un módulo POS propio, sobre la orden activa:

```javascript
const order = this.pos.get_order();
order.setOtherCharges([
  {
    type: "02",          // catálogo FE (ej. flete)
    code: "99",          // subcódigo/razón
    amount: 1500.0,       // > 0
    currency: "CRC",
    description: "Flete local",
  },
]);
```

### Qué persiste y qué recibe FE

- Se exporta en JSON POS como `cr_other_charges` (alias: `other_charges`).
- Backend POS lo guarda en `pos.order.cr_other_charges_json`.
- El payload FE incluye alias:
  - `other_charges`
  - `otros_cargos`
  - `fp_other_charges`

Con esto, implementaciones de `l10n_cr_einvoice` con diferentes nombres de campo pueden consumir los cargos sin duplicar XML ni romper assets OWL.
