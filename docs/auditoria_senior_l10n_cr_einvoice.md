# Auditoría técnica senior (Odoo 19): `cr_pos_einvoice` vs proceso esperado de `l10n_cr_einvoice`

## Veredicto ejecutivo

**No, en el estado actual de este repositorio el módulo no está realizando el proceso completo de FE de Costa Rica como lo haría `l10n_cr_einvoice` productivo (generar XML UBL/ATV válido, firmarlo criptográficamente y enviarlo realmente a Hacienda).**

Sí existe una **arquitectura de delegación** y de orquestación POS→FE, pero la implementación disponible aquí contiene **stubs/simulaciones** para generación, firma y envío.

---

## Qué sí está bien implementado (arquitectura/orquestación)

1. `cr_pos_einvoice` decide correctamente el flujo:
   - Si hay factura real (`account_move` válido), delega FE al flujo de `account.move`.
   - Si no hay factura real, procesa TE desde `pos.order`.

2. Existe control de idempotencia y estados FE en `pos.order` (`cr_fe_idempotency_key`, `cr_fe_status`, reintentos, próxima ejecución).

3. Hay hooks de cron para combinar objetivos de envío/consulta de `account.move` y tickets POS.

---

## Hallazgos críticos (gap contra FE real)

### 1) Generación XML no es FE real
En `l10n_cr_einvoice/services/einvoice_service.py`, `generate_xml()` serializa un JSON con `doc_type`+`payload`, no construye XML FE v4.4.

### 2) Firma digital no existe
En el mismo servicio, `sign_xml()` retorna el contenido sin firmar (`return xml`).

### 3) Envío a Hacienda está mockeado
`send_to_hacienda()` devuelve una respuesta simulada (`{"status": "sent", ...}`), no hay llamada HTTP real, token, endpoint ni manejo de certificados.

### 4) Consulta de estado para tickets POS está simplificada
En `pos.order`, `_cr_check_ticket_status_from_order()` cambia `sent -> accepted` localmente sin consulta real al backend tributario.

### 5) Riesgo de falso positivo “aceptado”
En `_cr_send_ticket_from_order()`, si `process_full_flow()` retorna `ok=True`, se fuerza `cr_fe_status='accepted'` inmediatamente en POS; no espera acuse de Hacienda.

### 6) Dependencia de métodos públicos en `account.move` sin implementación local
`_cr_pos_call_send_method()` busca métodos como `action_sign_and_send`, `action_send_to_hacienda`, etc. Esto solo funcionará si otro módulo real los provee.

---

## Conclusión técnica

- **Como adaptador POS**, el módulo está bien orientado: encola, delega, sincroniza estados y evita duplicidades.
- **Como implementación FE completa CR**, **no cumple** por sí solo con el ciclo requerido de `l10n_cr_einvoice` (XML normativo + firma criptográfica + envío/consulta real a Hacienda).

Si en tu entorno productivo existe otro `l10n_cr_einvoice` completo que sobreescriba estos stubs y exponga los métodos esperados en `account.move`, entonces el puente POS puede funcionar. Pero **lo que hay en este repositorio, aislado, no ejecuta FE real**.

---

## Recomendaciones senior (prioridad alta)

1. Reemplazar stubs de servicio por integración real:
   - XML FE 4.4 por tipo documental (TE/FE/NC/ND).
   - Firma XAdES con certificado de emisor.
   - Envío y consulta a endpoints de Hacienda (sandbox/prod), con token OAuth vigente.

2. No marcar `accepted` en POS hasta recibir respuesta oficial de Hacienda.

3. Mantener `cr_pos_einvoice` como capa delgada y centralizar la lógica fiscal en `l10n_cr_einvoice` real.

4. Agregar pruebas de integración:
   - caso ticket POS sin factura,
   - caso factura POS con `out_invoice`,
   - reintentos y deduplicación por idempotency key,
   - sincronización de estado desde Hacienda.
