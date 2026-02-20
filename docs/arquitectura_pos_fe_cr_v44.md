# Arquitectura propuesta: Integración POS ↔ Facturación Electrónica Costa Rica 4.4

## Objetivo

Definir una arquitectura limpia entre `l10n_cr_einvoice` (núcleo FE) y `cr_pos_einvoice` (adaptador POS) que:

1. Mantenga intacto el flujo nativo de POS Odoo 19.
2. Emita **TE desde `pos.order`** solo cuando no existe factura real.
3. Emita **FE exclusivamente desde `account.move`** cuando `to_invoice=True`.
4. Evite doble emisión, soporte concurrencia y funcione en multi-company.

---

## A) Arquitectura propuesta limpia (modelos y responsabilidades)

### 1. `l10n_cr_einvoice` (Single Source of Truth)

Responsable único de:

- Construcción XML v4.4 (TE/FE/NC/ND según documento).
- Firma criptográfica.
- Envío a Hacienda y consulta de estado.
- Persistencia de clave, consecutivo, track-id, estado tributación.
- Gestión de adjuntos XML/PDF/acuse.
- Política de reintentos y normalización de errores.

#### Servicio central (nuevo contrato público)

Crear/estandarizar un servicio público y estable, por ejemplo:

- Modelo: `l10n_cr.einvoice.service`.
- Métodos:
  - `enqueue_from_pos_order(order_id, payload, company_id, idempotency_key)`
  - `enqueue_from_move(move_id, company_id, idempotency_key)`
  - `process_job(job_id)`
  - `check_status(job_id)`

> `cr_pos_einvoice` no firma, no arma XML final, no consulta Hacienda directamente.

### 2. `cr_pos_einvoice` (adaptador POS)

Responsable exclusivo de:

- Decidir flujo A/B con regla crítica (factura real sí/no).
- Derivar payload mínimo desde `pos.order` para TE.
- Invocar servicio central de `l10n_cr_einvoice`.
- Mantener estado funcional en `pos.order` para trazabilidad de caja/POS.

No debe:

- Duplicar lógica de firma/envío/XML.
- Inferir factura a partir de `session_move_id`.
- Procesar asientos contables de sesión como FE.

### 3. Regla canónica de decisión

En `pos.order`:

```python
has_real_invoice = bool(
    order.account_move
    and order.account_move.move_type in ('out_invoice', 'out_refund')
    and order.account_move.state != 'cancel'
)
```

- `has_real_invoice=True` ⇒ Flujo B (FE por `account.move`).
- `has_real_invoice=False` ⇒ Flujo A (TE por `pos.order`).

`session_move_id` queda explícitamente fuera de la decisión.

### 4. Pago POS y selections oficiales

`pos.payment.method.fp_sale_condition` y `fp_payment_method` deben consumir exactamente las selecciones oficiales de `account.move`.

- Si existen campos alternos (`l10n_cr_payment_method`, `l10n_cr_payment_condition` o equivalentes), se marcan **deprecados** y se migra a los campos `fp_*`.
- El adaptador POS solo lee `fp_*`.

---

## B) Diagrama textual de ambos flujos

### Flujo A — TE desde POS (sin factura real)

1. POS paga orden (`paid/done`).
2. Hook post-pago en `cr_pos_einvoice` evalúa guard:
   - `has_real_invoice == False`.
3. Construye payload TE desde `pos.order` (líneas, impuestos, receptor si aplica, método/condición pago).
4. Genera `idempotency_key` estable (company + pos_reference/uuid).
5. Encola job asíncrono en `l10n_cr_einvoice`.
6. Worker FE procesa: XML + firma + envío + adjuntos + estado.
7. Callback/sync actualiza `pos.order` con clave/consecutivo/estado/adjuntos.

### Flujo B — FE desde Factura (to_invoice=True)

1. POS crea `account.move` real (`out_invoice`/`out_refund`).
2. Hook en `cr_pos_einvoice` evalúa guard:
   - `has_real_invoice == True`.
3. `cr_pos_einvoice` **no** crea TE ni job TE.
4. `l10n_cr_einvoice` ejecuta flujo estándar sobre `account.move`.
5. Estado FE se refleja en factura y opcionalmente se sincroniza resumen en `pos.order`.

---

## C) Diseño del servicio central reutilizable

### Contrato de entrada

#### `enqueue_from_pos_order(...)`

- `order_id`
- `company_id`
- `idempotency_key`
- `payload` normalizado:
  - emisor/sucursal/terminal
  - tipo documento = TE
  - líneas, impuestos, descuentos
  - moneda y tipo cambio
  - receptor (si aplica)
  - `fp_sale_condition`, `fp_payment_method`

#### `enqueue_from_move(...)`

- `move_id`
- `company_id`
- `idempotency_key`

### Persistencia recomendada

Modelo cola central: `l10n_cr.einvoice.job`

- `source_model` (`pos.order` / `account.move`)
- `source_id`
- `company_id`
- `document_type` (TE/FE)
- `idempotency_key` (índice único por compañía)
- `state` (`queued`,`processing`,`done`,`error`)
- `attempt_count`, `next_attempt_at`, `last_error`
- `clave`, `consecutivo`, `track_id`
- `xml_attachment_id`, `response_attachment_id`

### Idempotencia técnica

Constraint SQL:

- `unique(company_id, idempotency_key)`

Con esto se neutralizan duplicados por:

- reintento de `create_from_ui`
- reenvíos por POS offline reconectando
- doble click del operador

---

## D) Guard anti doble emisión correcto

En `pos.order` definir métodos explícitos:

1. `_cr_has_real_invoice_move()`
   - solo considera `account_move.move_type in ('out_invoice','out_refund')` no cancelado.
2. `_cr_can_emit_ticket()`
   - `state in ('paid','done','invoiced')`
   - `not _cr_has_real_invoice_move()`
   - estado FE no `sent/processing` para misma idempotency key.
3. `_cr_resolve_emission_flow()`
   - retorna `"invoice"` o `"ticket"`.

Reglas:

- Si flujo = `invoice`, bloquear cualquier TE.
- Si flujo = `ticket`, bloquear si ya existe job `queued/processing/done` con misma key.

---

## E) Lista de campos necesarios en `pos.order`

### Identificación y guard

- `cr_fe_flow` (`ticket`,`invoice`,`none`)
- `cr_fe_idempotency_key` (indexado)
- `cr_fe_company_id` (related company, index)

### Trazabilidad FE POS

- `cr_fe_status` (`not_applicable`,`queued`,`processing`,`sent`,`error`)
- `cr_fe_document_type` (`te`,`fe`)
- `cr_fe_clave`
- `cr_fe_consecutivo`
- `cr_fe_track_id`
- `cr_fe_xml_attachment_id`
- `cr_fe_response_attachment_id`
- `cr_fe_last_error`
- `cr_fe_retry_count`
- `cr_fe_next_try`
- `cr_fe_last_send_date`

### Relacionales

- `cr_fe_job_id` (`Many2one l10n_cr.einvoice.job`)
- `cr_invoice_move_id` (opcional related a `account_move` para lectura explícita)

> Evitar depender de `cr_ticket_move_id` como mecanismo principal para TE.

---

## F) Lista de validaciones necesarias

1. **Multi-company**
   - `order.company_id == config.company_id`
   - Métodos de pago de misma compañía.
   - Servicio FE ejecuta en `with_company(order.company_id)`.

2. **Regla crítica de documento**
   - Si existe `out_invoice/out_refund`: prohibido TE.
   - Si no existe: permitido TE.

3. **Integridad de pagos FE**
   - `fp_payment_method` y `fp_sale_condition` presentes en método principal.
   - Fallback seguro a códigos por defecto de compañía (parametrizables).

4. **Estados POS válidos**
   - Solo emitir en `paid/done/invoiced`.
   - Nunca emitir en `draft/cancel`.

5. **Montos e impuestos**
   - Total líneas + impuestos = total orden (con tolerancia de redondeo).

6. **Receptor**
   - Si cliente identificado, validar tipo/numero identificación requerido por FE.

7. **Concurrencia**
   - Lock lógico al encolar (`SELECT ... FOR UPDATE` del job por key o create con unique+retry).

8. **Errores recuperables vs terminales**
   - Recuperables: timeout, 5xx, token expirado.
   - Terminales: validación XML, firma inválida, datos obligatorios ausentes.

---

## G) Checklist de pruebas

### Unitarias

- Guard `_cr_has_real_invoice_move()` ignora `session_move_id`.
- `to_invoice=True` con `out_invoice` ⇒ flujo `invoice`.
- Orden pagada sin factura ⇒ flujo `ticket`.
- Idempotency key estable ante reintentos.

### Integración POS

- Venta normal (sin facturar) crea TE async y actualiza estado.
- Venta con facturación crea FE por `account.move` y no TE.
- Devolución facturada (`out_refund`) delega FE correctamente.
- Múltiples pagos elige método principal por mayor monto.

### Concurrencia / resiliencia

- Doble `create_from_ui` simultáneo no duplica TE.
- Offline→online con replay no duplica documentos.
- Reintentos cron respetan `next_try`.

### Multi-company

- Dos compañías con secuencias/certificados distintos no contaminan datos.
- Jobs y adjuntos quedan en la compañía correcta.

### No regresión POS

- Cierre de sesión POS y `session_move_id` sin cambios funcionales.
- Arqueo/contabilidad de sesión intactos.

---

## Decisiones técnicas clave (resumen)

1. **Separación estricta de responsabilidades**: POS decide flujo; FE central ejecuta ciclo tributario completo.
2. **Regla de verdad única para factura real**: solo `account.move` cliente (`out_invoice/out_refund`).
3. **Asincronía obligatoria en TE POS** para no bloquear caja.
4. **Idempotencia fuerte por clave estable + unique SQL** para escenarios offline/replay.
5. **Compatibilidad nativa Odoo 19**: no altera `session_move_id` ni contabilidad de sesión.
