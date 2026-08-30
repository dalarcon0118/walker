# ADR-WALKER-064 — 10 estrategias Pareto (80/20) para mejorar lo existente

- **Status:** Activa
- **Date:** 2026-08-26
- **Autor:** Walker agents (con el usuario)
- **Complementa:** ADR-WALKER-062 (mantenibilidad monolith puertos DI CI), ADR-WALKER-063 (CognitiveGraph: snapshot O(N) → append+merge), ADR-WALKER-055 (deuda técnica estructural), ADR-WALKER-061 (storage como puerto), ADR-WALKER-045 (composition root obligatorio)
- **Hipótesis:** Aplicando el **principio de Pareto (80/20)** — ~20% del esfuerzo produce ~80% del valor — priorizar las estrategias por **impacto ÷ esfuerzo** resuelve la mayor parte de la deuda existente con las tareas más baratas primero, sin rediseñar el marco.

---

## 1. Método: Pareto aplicado a la deuda existente

Principio: en sistemas complejos, ~20% de las causas raíz generan ~80% del costo (o de los bugs). La priorización NO es por "lo que más molesta" sino por **impacto ÷ esfuerzo**:

```
ranking = impacto_alto / esfuerzo_bajo   → quick win (hacer ya)
        = impacto_alto / esfuerzo_alto   → núcleo (programar con presupuesto)
        = impacto_bajo  / esfuerzo_bajo  → pulido (hacer si se toca el archivo)
        = impacto_bajo  / esfuerzo_alto  → NO hacer (deuda diferida)
```

Evidencia de la espiral (ADR-063 §2.4): cada fix de performance añadió complejidad sobre el mismo núcleo O(N). El Pareto ataca el 20% de las causas que generan el 80% del costo (write amplification + locator roto + falta de gates de regresión), no los síntomas.

---

## 2. Las 10 estrategias (ranking Pareto)

| # | Estrategia | Impacto | Esfuerzo | Pareto |
|---|---|---|---|---|
| **S3** | Fix `compose_app()`/`get_module_service` | Medio-alto | Muy bajo | quick win |
| **S4** | Test arquitectónico CI (no-import + completitud de registries) | Medio-alto | Bajo | quick win |
| **S5** | Test de performance de `tools/call` (umbral de duración) | Medio | Bajo | quick win |
| **S2** | Cap/rotación del índice trace (blob acotado) | Alto | Bajo | quick win |
| **S7** | Flush por tiempo (además de umbral) en `TraceLogger` | Medio | Bajo | quick win |
| **S10** | SSOT de ciclos de vida por memoria (guía de storage) | Bajo-medio | Bajo | quick win |
| **S1** | CognitiveGraph: persistencia append+merge (infraestructura, ADR-063) | Alto | Medio-alto | núcleo |
| **S8** | Unificar resolución de servicios (`AppContainer` canónico) | Medio | Medio | núcleo |
| **S6** | Eliminar `_load()` redundante en `store()` | Bajo | Muy bajo | pulido |
| **S9** | `DbBackend` para CPG/CKG (Fase 2 ADR-063) | Alto | Alto | diferir |

### S1 — CognitiveGraph: persistencia append+merge (infraestructura)
- **Problema:** write amplification O(N) en la infraestructura (ADR-063 P1/P2); el batching del `TraceLogger` era un parche de aplicación.
- **Qué:** interfaz `StorageDb` (puerto DB-like, ADR-063 §4.4) con implementación de capa de ficheros `SegmentFileBackend` en `FileBackend`/`Index`: append a segmento inmutable (O(1), durable) + búsqueda sobre el índice en memoria (ES-like, sin cambio) + compaction periódica. Beneficia a TODOS los índices (trace, CPG, CKG, Delta); el batching del tracer se revierte.
- **Esfuerzo:** medio-alto. **Impacto:** alto (elimina la clase de problema que causó 162s en el trace y la write amplification latente en CPG/CKG).
- **Pareto:** el 20% de las causas (formato de persistencia) → el 80% del costo de escritura de todas las memorias. Es el núcleo; se programa con presupuesto.

### S2 — Cap/rotación del índice trace
- **Problema:** N crece sin límite → cada reescritura más cara (ADR-063 FS-1); medido: 57k ≈ 1.3s por rewrite.
- **Qué:** en `_flush_pending()`, acotar el blob (retener últimos N días/eventos o rotar a `trace.1.json.gz`) con retención mínima garantizada para la tarea/sesión en curso.
- **Esfuerzo:** bajo. **Impacto:** alto a corto plazo (acota el O(N) sin rediseñar, gana tiempo para S1).
- **Pareto:** el win inmediato más barato. Cuidado FS-2 (no borrar evidencia que el gate necesita).

### S3 — Fix `compose_app()`/`get_module_service`
- **Problema:** `features/main/bootstrap.py:429` usa `ProjectService` sin importarlo → **todo `get_module_service()` crashea** (`NameError`); el idiom id+contrato del ADR-062 no funciona por esa vía (visto en el refactor de handlers/kg_context).
- **Qué:** import/inyección de `ProjectService` en `compose_app()`; test de humo de `get_module_service("workflow")`.
- **Esfuerzo:** muy bajo. **Impacto:** medio-alto (desbloquea el locator; quita un landmine).
- **Pareto:** fix de ~1 línea que arregla una vía entera.

### S4 — Test arquitectónico CI (no-import + completitud de registries)
- **Problema:** la regla domain-first (A5: `shared/core/engine` nunca importa `features/`) y el patrón de completitud de registries (AGENTS.md) no están automatizados.
- **Qué:** test que falla si `shared/core/engine` importa `features/`; y EXPECTED-set que incluye `"tracer"` (ya registrado en el composition root) en `test_container_completeness_bdd.py`.
- **Esfuerzo:** bajo. **Impacto:** medio-alto (previene regresiones de capas y omisiones de servicios).
- **Pareto:** automatiza lo que hoy es manual (FS-4/FS-5 de `domain-first.md`).

### S5 — Test de performance de `tools/call`
- **Problema:** la regresión de 162s pasó desapercibida hasta medirla; nadie la detecta sola.
- **Qué:** benchmark de `tools/call update_incremental_code` con umbral (p.ej. < 30s en CI) y alerta si crece con el tamaño del índice.
- **Esfuerzo:** bajo. **Impacto:** medio (detecta write amplification y regresiones de trazado).
- **Pareto:** 1 test previene la clase entera de regresión de performance.

### S6 — Eliminar `_load()` redundante en `store()`
- **Problema:** `store()` llama `_load()` (index.py:407) y `_store_core` lo llama otra vez (421) (ADR-063 P5).
- **Qué:** quitar el `_load()` externo de `store()`/`store_many()` (el core ya lo hace).
- **Esfuerzo:** muy bajo. **Impacto:** bajo (ahorra un stat syscall por write).
- **Pareto:** pulido — solo si se toca `index.py`.

### S7 — Flush por tiempo en `TraceLogger`
- **Problema:** el residual sub-umbral queda sin persistir hasta el próximo umbral o `atexit` → durabilidad tardía.
- **Qué:** timestamp del primer `_pending` + flush si `now - first > 2s` (además del umbral de 20).
- **Esfuerzo:** bajo. **Impacto:** medio (persistencia más inmediata, sin rediseño).
- **Pareto:** cierra el hueco del batching actual hasta que llegue S1.

### S8 — Unificar resolución de servicios (`AppContainer` canónico)
- **Problema:** dos composition paths (`compose_app()` roto vs `AppContainer.build()`) → inconsistencias (visto en F3 y en el refactor).
- **Qué:** deprecar `compose_app`/`get_module_service` en favor de `AppContainer.build().module_registry` (2 pasos: warn → swap); actualizar callers.
- **Esfuerzo:** medio. **Impacto:** medio (consolida el root único ADR-045).
- **Pareto:** núcleo de consistencia; se hace tras los quick wins.

### S9 — `DbBackend` (alternativa de escala)
- **Problema:** write amplification latente en builds grandes (ADR-063 FS-4).
- **Qué:** backend sqlite/lmdb/duckdb detrás del MISMO puerto `StorageBackend` (alternativa a S1 cuando la escala supere el fichero).
- **Esfuerzo:** alto. **Impacto:** alto (escala real).
- **Pareto:** **diferir** — S1 (append+merge en ficheros) cubre el 80% del costo actual; `DbBackend` solo si el fichero deja de alcanzar.

### S10 — SSOT de ciclos de vida por memoria
- **Problema:** el mismatch trace/snapshot puede reintroducirse sin guía (ADR-063 §2.7).
- **Qué:** sección en `domain-first.md`/ADR: qué storage le corresponde a cada ciclo de vida (CPG=replace, CKG=batch, Delta-specs=snapshot pequeño, **Trace/backlog=log append**).
- **Esfuerzo:** bajo. **Impacto:** bajo-medio (previene errores de diseño caros).
- **Pareto:** documentación barata que evita regresiones; paralelo a los quick wins.

---

## 3. Orden de ejecución sugerido

1. **Quick wins (80% del valor, ~20% del esfuerzo):** S3 → S4 → S5 → S2 → S7 → S10.
2. **Núcleo:** S1 (infraestructura append+merge) → S8 (unificar resolución).
3. **Pulido:** S6 (solo si se toca `index.py`).
4. **Diferido:** S9 (cuando el tamaño lo exija).

---

## 4. `failure_scenarios`

**FS-1 — Priorizar mal (hacer S9/S6 primero y dejar S2/S3 para el final)**
- **fallo:** se gasta el presupuesto en lo caro; los wins baratos quedan sin hacer y la regresión de performance persiste.
- **causa raíz:** ignorar el ranking impacto÷esfuerzo.
- **mitigación:** orden de ejecución fijado en §3; quick wins primero.
- **detección:** revisión del plan de sprint contra la tabla del ADR.

**FS-2 — Cap (S2) borra evidencia que el gate necesita**
- **fallo:** recortar docs viejos del trace rompe `has_kg_query`/auditoría (evidencia de `required_tools` perdida).
- **causa raíz:** cap sin criterio de retención.
- **mitigación:** retención por antigüedad con mínimo garantizado; nunca recortar eventos de la tarea/sesión en curso.
- **detección:** test del gate tras el cap; conteo de eventos retenidos por tarea.

**FS-3 — S1 (append+merge infra) lanzado sin validar gates**
- **fallo:** `has_kg_query` devuelve 0 → el gate `required_tools` bloquea submits legítimos.
- **causa raíz:** cortar el snapshot antes de validar el read model.
- **mitigación:** escribir log + read model en paralelo, validar gates E2E, y solo entonces dejar el snapshot.
- **detección:** test E2E del gate con eventos recién appendeados; replay con filtro por task/session.

**FS-4 — S8 (unificar resolución) rompe callers de `compose_app`**
- **fallo:** migrar sin actualizar callers → `KeyError`/`RuntimeError` en runtime.
- **causa raíz:** deprecar sin reemplazo verificado.
- **mitigación:** deprecación en 2 pasos (warn → swap) + tests de callers.
- **detección:** suite E2E + grep de callers restantes de `get_module_service`.

---

## 5. Checklist de segundo orden (AGENTS.md)

- ¿Qué módulos importan esto? → `trace_log.py`, `bootstrap.py`, `container.py`, `index.py`, `file_backend.py`, `tool_catalog.py`, gates.
- ¿Qué side effects dispara? → cap/rotación del trace, reconstrucción del read model, deprecaciones.
- ¿Qué eventos de dominio publica? → eventos trace (kg_query, ToolRegistered/ToolInvoked, WORKFLOW_FORCED).
- ¿Qué tests dependen? → gates `required_tools`, E2E BDD, `test_container_completeness_bdd.py`, benchmark de `tools/call`.
- ¿Qué cambia en MCP/CLI/CKG/Delta/Trace? → la API de `ITracer`/`has_kg_query`/`replay` no cambia; CKG/Delta solo en S9 (diferido).

---

## 6. Impacto esperado

- **S2+S7 (quick wins):** acotan el O(N) y la durabilidad del batching actual sin rediseño → la llamada MCP queda estable (~7s) a cualquier N razonable.
- **S3+S8:** desbloquean el locator id+contrato y consolidan el composition root único (ADR-045/062).
- **S4+S5:** gates de CI que previenen la clase de regresiones que motivaron este análisis.
- **S1:** elimina el problema de raíz en la infraestructura (write-side append O(1) + read model en memoria) para todos los índices.
- **S9:** deuda diferida con criterio explícito (solo cuando el tamaño lo exija).