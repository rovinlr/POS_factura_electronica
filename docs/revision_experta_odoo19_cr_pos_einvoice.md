# Revisión experta Odoo 19 + Python: faltantes del módulo `cr_pos_einvoice`

## Alcance revisado

Se revisó el addon `cr_pos_einvoice` en su manifiesto, modelos, vistas, seguridad y cron para identificar qué falta para una implementación robusta/productiva en Odoo 19.

## Veredicto corto

El módulo está bien orientado como **puente POS → FE** y tiene una base funcional sólida, pero todavía le faltan piezas importantes para operar con estándar productivo (calidad, trazabilidad, concurrencia y pruebas).

---

## Lo que sí está bien

1. **Separación de flujos TE vs FE** según exista factura real (`account.move`) o no.
2. **Estados FE y reintentos** en `pos.order`.
3. **Integración por cron** para envío y consulta de estados.
4. **Campos FE en POS config y métodos de pago** para mapear códigos CR.

---

## Qué le hace falta (prioridad alta)

## 1) Cobertura de pruebas automatizadas

Falta un paquete de tests (`tests/`) con al menos:

- Caso POS sin factura: genera TE, crea adjunto XML y agenda envío.
- Caso POS con factura: sincroniza estado desde `account.move` y no emite TE.
- Reintentos e idempotencia: no duplicar documento con misma llave.
- Cron de envío/consulta: transición de estados esperada.

Sin esto, cada cambio en Odoo 19 o en `l10n_cr_einvoice` puede romper compatibilidad sin detección temprana.

## 2) Endurecer concurrencia para consecutivo/idempotencia

Actualmente el consecutivo se calcula leyendo órdenes previas y tomando el mayor, lo que abre ventana de carrera bajo carga (dos workers pueden tomar el mismo consecutivo).

Recomendado:

- `ir.sequence` por compañía y tipo documental (`TE/FE/NC`) para el consecutivo.
- Mantener unicidad SQL para idempotencia y capturar explícitamente `IntegrityError` para reintentos limpios.

## 3) Validaciones fiscales mínimas antes de emitir

Faltan validaciones previas para evitar rechazos de Hacienda (ej. datos de receptor según reglas/monto, consistencia de impuestos y totales).

Recomendado:

- Método de validación previa (`_cr_validate_before_send`) con errores funcionales claros.
- Bloquear envío cuando falten datos obligatorios del emisor/receptor.

## 4) Mejor trazabilidad de errores operativos

Se guarda `cr_fe_last_error`, pero falta estandarizar códigos/categorías para soporte (timeout, auth, schema, rechazo fiscal, etc.).

Recomendado:

- Clasificar error técnico vs error de negocio.
- Guardar última respuesta relevante del backend FE/Hacienda para diagnóstico.

## 5) Optimización de cómputos en adjuntos

`_compute_cr_fe_attachment_ids` hace una búsqueda por cada orden (N+1), afectando rendimiento en listas grandes.

Recomendado:

- Agrupar consultas de adjuntos por lote (`read_group` o búsqueda única + mapeo en memoria).

---

## Qué le hace falta (prioridad media)

## 6) Endurecer UX/seguridad en acciones manuales

Los botones de envío/consulta en `pos.order` no tienen condiciones de visibilidad por estado ni restricciones funcionales adicionales en la vista.

Recomendado:

- Mostrar botones solo en estados válidos.
- Añadir confirmaciones/mensajes cuando no aplica (ej. pedido borrador/no pagado).

## 7) Cron más resiliente para operación real

Actualmente se ejecuta por lote simple; falta mayor control operativo para entornos de alto volumen.

Recomendado:

- Configurar límites por batch más explícitos y trazas por ejecución.
- Métricas básicas (procesados, exitosos, fallidos).

## 8) Localización/i18n y documentación operativa

No hay carpeta `i18n/` ni guía operativa detallada de soporte (playbook de incidentes).

Recomendado:

- Añadir traducciones (`es_CR`, `en_US` si aplica).
- Documento de operación con pasos de diagnóstico y recuperación.

---

## Qué le hace falta (prioridad baja)

## 9) Calidad de código y CI

Falta pipeline de calidad (lint/format/tests) para evitar regresiones.

Recomendado:

- Integrar `ruff`/`pylint-odoo` + ejecución de tests en CI.

## 10) Gobierno de datos FE

Puede reforzarse retención/gestión de adjuntos XML y observabilidad.

Recomendado:

- Política de retención por compañía/periodo.
- Dashboard técnico de estados FE por POS.

---

## Checklist sugerido para “quedar listo”

1. Crear tests automáticos críticos (4 escenarios mínimos).
2. Migrar consecutivo a `ir.sequence` por compañía/tipo.
3. Implementar validación fiscal previa al envío.
4. Optimizar cómputo de adjuntos y mejorar trazabilidad de errores.
5. Añadir CI + lint + documentación operativa.

Con estos puntos, el módulo pasaría de “funcional como puente” a “operable y mantenible en producción Odoo 19”.
