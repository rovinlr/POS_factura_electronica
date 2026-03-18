# Checklist de integración `cr_pos_einvoice` ↔ `l10n_cr_einvoice` (`electronicCRinvoice`)

Esta guía define **qué debes implementar en `l10n_cr_einvoice`** para que `cr_pos_einvoice` actúe solo como puente POS, y toda la lógica fiscal real (XML/firma/envío/consulta) quede centralizada en FE.

## 1) Objetivo funcional

- Si la orden POS **no** tiene factura real (`account.move out_invoice/out_refund`): emitir **TE** desde `pos.order`.
- Si la orden POS **sí** tiene factura real: delegar flujo estándar de FE desde `account.move`.
- `cr_pos_einvoice` decide el flujo y pasa datos POS normalizados; `l10n_cr_einvoice` ejecuta FE real.

---

## 2) Contrato mínimo recomendado en `l10n_cr.einvoice.service`

Implementa en `l10n_cr_einvoice` un modelo público:

- Modelo: `l10n_cr.einvoice.service`

### Métodos de envío POS (al menos uno)

1. `enqueue_from_pos_order(order_id, payload=None, company_id=None, idempotency_key=None)`
2. (alias opcional) `send_from_pos_order(order_id, payload=None, company_id=None, idempotency_key=None)`
3. (alias opcional) `process_pos_order(order_id, payload=None, company_id=None, idempotency_key=None)`

> `cr_pos_einvoice` intenta esos nombres en ese orden.

### Métodos de consulta de estado POS (al menos uno)

1. `check_status_from_pos_order(order_id, idempotency_key=None)`
2. (alias opcional) `check_status(order_id, idempotency_key=None)`
3. (alias opcional) `get_pos_order_status(order_id, idempotency_key=None)`

---

## 3) Formato de respuesta esperado por `cr_pos_einvoice`

Para envío:

```python
{
  "ok": True/False,
  "status": "to_send|sent|accepted|rejected|error",
  "track_id": "...",        # opcional
  "clave": "...",           # opcional
  "consecutivo": "...",     # opcional
  "message": "..."          # opcional
}
```

Para consulta:

```python
{
  "status": "sent|accepted|rejected|error",
  "track_id": "...",        # opcional
  "clave": "...",           # opcional
  "consecutivo": "..."      # opcional
}
```

> Importante: `cr_pos_einvoice` usa `status` para normalizar y guardar `cr_fe_status`.

---

## 4) Payload canónico que recibe FE desde POS

`cr_pos_einvoice` construye y pasa un payload con esta estructura base:

```python
{
  "source_model": "pos.order",
  "source_id": <int>,
  "name": "POS/...",
  "date": "YYYY-MM-DD ...",
  "company_id": <int>,
  "partner_id": <int|False>,
  "currency_id": <int>,
  "total_untaxed": <float>,
  "total_tax": <float>,
  "total": <float>,
  "idempotency_key": "POS-...",
  "lines": [
    {
      "product_id": <int|False>,
      "name": "...",
      "qty": <float>,
      "price_unit": <float>,
      "discount": <float>,
      "tax_ids": [<int>, ...],
      "subtotal": <float>,
      "total": <float>
    }
  ],
  "payments": [
    {
      "amount": <float>,
      "payment_method_id": <int>,
      "fp_payment_method": "..",   # código FE
      "fp_sale_condition": ".."    # código FE
    }
  ]
}
```

---

## 5) Qué debe hacer internamente `l10n_cr_einvoice` (electronicCRinvoice)

1. Resolver `order = env['pos.order'].browse(order_id).exists()` y validar compañía.
2. Construir XML FE 4.4 (TE) desde payload canónico.
3. Firmar XML (XAdES/llave y certificado del emisor).
4. Enviar a Hacienda (token + endpoint sandbox/prod según ambiente).
5. Persistir:
   - clave, consecutivo, track_id,
   - estado tributación,
   - adjuntos XML (documento y respuesta/acuse).
6. Retornar `dict` compatible (`ok`, `status`, metadatos opcionales).

---

## 6) Idempotencia y concurrencia (obligatorio)

- Usa `idempotency_key` por compañía con `unique(company_id, idempotency_key)`.
- Si llega la misma llave, no regeneres ni reenvíes documento.
- Reintentos deben ser seguros (timeout/network) sin duplicar FE.

---

## 7) Estados sugeridos y mapping

Estados mínimos recomendados en FE interno:

- `queued` / `to_send`
- `sent`
- `accepted`
- `rejected`
- `error`

Asegura retornar uno de esos para que POS pueda normalizar sin ambigüedades.

---

## 8) Datos mínimos para TE desde POS

- Receptor (si aplica por monto/regla fiscal).
- Líneas con impuestos correctos luego de fiscal position.
- Método y condición de pago FE (`fp_payment_method`, `fp_sale_condition`).
- Totales consistentes (`untaxed + tax ~= total`, tolerancia de redondeo).

---

## 9) Pruebas de aceptación recomendadas

1. **POS sin factura**: crea TE, firma, envía y consulta estado real.
2. **POS con factura** (`to_invoice=True`): no crea TE; FE la maneja `account.move`.
3. **Reintento por timeout**: no duplica documento (misma idempotency key).
4. **Offline/replay POS**: no duplica FE al reconectar.
5. **Rechazo Hacienda**: estado final `rejected` y error trazable.

---

## 10) Diagnóstico rápido si “no funciona”

- ¿Existe modelo `l10n_cr.einvoice.service`?
- ¿Implementa al menos un método de envío POS y uno de consulta?
- ¿Devuelve `dict` con `status`?
- ¿Se está pasando `idempotency_key` y respetando unicidad por compañía?
- ¿Hay certificados/token/endpoint configurados en compañía?

Si todo lo anterior está correcto, `cr_pos_einvoice` ya puede operar como puente POS sobre `electronicCRinvoice`.
