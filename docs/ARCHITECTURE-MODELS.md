# Architecture Models — Walker

Catálogo de modelos de pensamiento aplicados al diseño y evolución de Walker.

Fuente: SPEC-004 (Modelos de pensamiento para diseño y evolución de Walker).

---

## 1. Pensamiento de segundo orden

**Principio**: antes de aprobar cualquier cambio, responder:
1. ¿Qué módulos importan este?
2. ¿Qué side effects dispara?
3. ¿Qué eventos de dominio publica?
4. ¿Qué tests dependen de este comportamiento?
5. ¿Qué cambia en MCP adapter, CLI, CKG, DeltaGraph, Trace?

Si la respuesta a alguna es "no sé", el cambio tiene **riesgo de segunda orden no controlada**.

**Aplicación en Walker**:
- Side effect → cascada en CKG/Delta/Trace.
- Tool MCP nueva → impacto en 5 módulos.
- `tests/features/` como namespace → tests rotos.

---

## 2. Pensamiento sistémico (stocks/flujos)

**Principio**: el sistema es más que la suma de partes; hay retroalimentación.

**Aplicación en Walker**:
- WorkflowState = **stock**.
- `submit`/`transition` = **flujos**.
- `on_success` = **loop de retroalimentación**.

Si `spec_task` se corrompe, specs en `draft` crecen y el sprint se estanca.

---

## 3. Pre-mortem (contrafactual)

**Principio**: simular fallos antes de aprobar specs.

**Aplicación en Walker**:
Toda spec/ADR nueva debe incluir `failure_scenarios`:
```json
"failure_scenarios": [
  "Si delta_tracking se corrompe, toda la trazabilidad se pierde → mitigación: backup diario",
  "Si _SERVER se cachea y el workflow cambia, tests ven estado viejo → mitigación: reloader + tests con tmp_path"
]
```

---

## 4. Costos de transacción

**Principio**: cada operación de coordinación tiene fricción.

**Aplicación en Walker**:
- `submit` por cada sub-estado = alta fricción.
- `auto-advance` por `on_success` reduce fricción pero hay que preservar trazabilidad.

---

## 5. Opciones reales

**Principio**: mantener decisiones abiertas mientras haya valor.

**Aplicación en Walker**:
En vez de `gates` rígidas en `edges.json`, el LLM elige camino vía `on_success`. Mantiene opciones abiertas más tiempo.

---

## 6. Normalización de desviaciones

**Principio**: los fallos puntuales se normalizan y el sistema se degrada silenciosamente.

**Aplicación en Walker**:
Si `transform_spec` falla y el workflow avanza igual, se crean specs "zombies" en `draft` forever. Hay que diseñar **circuit breakers**, no warnings.

---

## 7. Inmutabilidad / Invariantes

**Principio**: identificar lo que nunca debe cambiar.

**Aplicación en Walker**:
- WorkflowState nunca se pierde entre submits.
- JSON-RPC 2.0 es el transporte.
- `_handle_request` es la entrada MCP.
- delta_tracking es SSOT.

Todo lo demás (formatos de spec, nombres de tools) puede evolucionar.

---

## 8. Abstracción correcta

**Principio**: a veces la abstracción es el problema, no la solución.

**Aplicación en Walker**:
`TaskRegistry` mezcla tasks de dominio (`spec_task`) con tasks de sistema (`help`, `list`). Esa abstracción genera los skips actuales. Separar `DomainTaskRegistry` de `SystemTaskRegistry` elimina la fricción.

---

## 9. Cobra effect

**Principio**: incentivos mal diseñados producen el comportamiento opuesto.

**Aplicación en Walker**:
Si `capture_decision` se ejecuta siempre, el CKG se llena de decisiones triviales. Segundo orden: el LLM pierde señal porque todo es "importante". Hay que filtrar decisiones relevantes.

---

## 10. Tragedia de los comunes

**Principio**: recursos compartidos sin regulación se degradan.

**Aplicación en Walker**:
`context_bag` y los placeholders (`cpg max_results: 30`, `ckg max_nodes: 20`) son recursos comunes. Si todos los sub-estados piden `max_results: 30`, el sistema se degrada. Hay que **presupuestar contexto** por nodo.

---

## 11. Robustez vs eficiencia

**Principio**: trade-off que debe ser explícito.

**Aplicación en Walker**:
`_SERVER` cachea el workflow para eficiencia, pero pierde robustez ante cambios en disco (`server stale`). El reloader mitiga, pero agrega complejidad. Documentar el trade-off evita "optimizaciones" que rompen tests.

---

## 12. Información asimétrica

**Principio**: el sistema tiene más información que el agente; diseñar capas graduadas.

**Aplicación en Walker**:
El LLM decide `approve`/`refine`, pero no ve el `WorkflowState` completo. Es bueno (reduce complejidad para el LLM) pero malo (decisiones subóptimas). Diseñar **capas de información graduadas**, no todo o nada.

---

## 13. Corrección local vs global

**Principio**: una operación puede ser correcta localmente pero incorrecta en el sistema completo.

**Aplicación en Walker**:
`transform_spec` en `TASK_SPEC` modifica la spec localmente bien. Pero si mañana hay LLM tasks paralelas, pueden corruptar el mismo documento. Diseñar para **concurrencia desde ya**, no como afterthought.

---

## Checklist de segundo orden para specs/ADRs

Antes de aprobar cualquier cambio, responder:
1. ¿Qué módulos importan este?
2. ¿Qué side effects dispara?
3. ¿Qué eventos de dominio publica?
4. ¿Qué tests dependen de este comportamiento?
5. ¿Qué cambia en MCP adapter, CLI, CKG, DeltaGraph, Trace?

Si la respuesta a alguna es "no sé", el cambio tiene riesgo de segunda orden no controlada.
