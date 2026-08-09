# ADR-WALKER-018: event_tracing — Bounded Context "Trace": modelo SSOT, puerto de repositorio y migración legacy

> **Status:** Accepted — Fase A y Fase B implementadas (§8)
> **Fecha:** 2026-08-06
> **Contexto:** gaps de arquitectura/diseño detectados tras la migración a Result pattern (convention-001) en el feature event_tracing
> **Complementa:** ADR-WALKER-013 (DeltaGraph SSOT), ADR-WALKER-016 (Bounded Context Protocol + DIP), ADR-WALKER-017 (derivar en build, leer en query)

---

## 1. Contexto y problema

`event_tracing` (TraceGraph: historial de eventos del workflow) fue migrado de
try/except a Result pattern, pero el análisis de diseño reveló **11 gaps
estructurales** que la migración de estilo no resuelve:

| # | Severidad | Gap | Evidencia |
|---|---|---|---|
| 1 | 🔴 | **Doble representación de `TraceEvent`** (ODM `Entity` + dataclass de dominio) con campos distintos | `entities.py` vs `domain/models.py`; `__init__.py` exporta ambas |
| 2 | 🔴 | **Triple-fuente sin dedupe** — el consumidor compensa con dedupe propio | `reflection.py::_dedupe_events` ("sin tocar infra compartida") |
| 3 | 🔴 | **Fallback legacy en el hot path** — la migración a CG nunca se materializa | `load_traces` lee NDJSON/index en cada llamada |
| 4 | 🟠 | **Sin Port/Repository** — función concreta importada por todos | no existe `ports/`; inconsistente con delta_tracking/project |
| 5 | 🟠 | **Acoplamiento cruzado engine ↔ feature** (contrato de provider vive en el engine) | `context/providers.py` importa `features.engine.context` |
| 6 | 🟠 | **`mcp.py` no es dueño de su tool** — passthrough a `dispatch_public_task` | `mcp.py::_public_task` |
| 7 | 🟠 | **Round-trip de serialización roto** — `to_dict()` pierde `id`/`parent_id`/`parent_type`; `sprint_id` se lee de `parent_id` | `domain/models.py` |
| 8 | 🟡 | **`search_traces` traga filtros desconocidos** (fail-open) y no filtra por sprint | `search_service.py` |
| 9 | 🟡 | **Nondeterminismo de orden** — iteración sobre `{chain_dir, project_root}` (set) | `repository.py` |
| 10 | 🟡 | **`search_traces` sin consumidores reales** — solo la fachada `search()` | search en todo el repo |
| 11 | 🟡 | **IO en el hot path del prompt** — `ReflectionProvider.collect` escanea legacy en cada build de prompt REFINE | `context/providers.py` → `load_traces` |

### El principio

> **El repositorio es la única frontera de datos: garantiza unicidad, orden y
> canonicalidad. La capa de aplicación no compensa defectos de la capa de datos.
> La feature es dueña de su modelo, su puerto y su tool MCP.**

---

## 2. La decisión

**Rediseñar `event_tracing` como Bounded Context "Trace" autocontenido:**

1. **`TraceEvent` (dataclass de dominio) es el SSOT del modelo.** Se elimina el
   ODM duplicado (`entities.py`) del namespace público. Serialización **simétrica**
   (`to_dict` ↔ `from_dict`/`from_ndjson` conservan identidad).
2. **Port `TraceRepository` + `CognitiveTraceRepository`.** La capa de aplicación
   depende de la abstracción; la infraestructura implementa el puerto.
3. **Unicidad y orden garantizados en el repositorio.** Dedupe central por firma
   (timestamp, event_type, tool, data-blob) y orden determinista por timestamp.
4. **Migración legacy explícita e idempotente** (`migrate_legacy_traces`): copia
   NDJSON + index al índice trace canónico. IDs deterministas (hash de contenido)
   → re-ejecutar no duplica.
5. **Cache en proceso por firma de fuentes** para eliminar el re-escaneo legacy
   del hot path del prompt (con invalidación por mtime + TTL + invalidation al push).
6. **`search_traces` fail-closed**: filtros desconocidos no devuelven todo.
7. **`mcp.py` propietario**: el handler de `trace_push` delega en `push_event`
   de la feature — sin pasar por el task registry del engine.
8. **Fase B**: extraer el contrato de provider (`ContextPiece`, `ContextProvider`,
   formatters) a `libs/context` — refactor engine-wide (toca 6 features +
   engine) implementado y documentado en §3.8.

---

## 3. Cambios de diseño

### 3.1 Modelo SSOT y round-trip simétrico (`domain/models.py`)

- `to_dict()` ahora incluye `id`, `parent_id`, `parent_type` (antes se perdían).
- `from_ndjson`/`from_dict` leen `sprint_id` con fallback: `sprint_id` → `parent_id`
  (compat con datos legacy que solo tienen `parent_id`).

### 3.2 Puerto `TraceRepository` (`application/ports/trace_repository.py`)

```python
class TraceRepository(ABC):
    def load_traces(self, chain_dir: str) -> list[TraceEvent]: ...
    def migrate_legacy(self, chain_dir: str) -> int: ...      # idempotente
    def push_event(self, chain_dir: str, data: dict) -> dict: ...
```

`CognitiveTraceRepository` (infra) lo implementa. Las funciones de módulo
(`load_traces`, `migrate_legacy_traces`, `push_event`) se conservan como
**backward-compat** para `reflection.py`, `__init__.py` y tests. `push_event`
responde `{"ok", "data": {"event_id", "event_type"}}`. La raiz CG del indice
trace es `project_root` (si resuelve) con fallback a `chain_dir` — consistente
con el task `trace_push` del engine, para visibilidad cruzada de eventos.

### 3.3 Unicidad + orden + cache en el repositorio (`infrastructure/repository.py`)

- `_dedupe(events)`: por firma `(timestamp, event_type, tool, data)`.
- Orden final determinista: `sort(key=(timestamp, event_type))`.
- `_CACHE[chain_dir] = (firma_legacy_mtimes, expira, eventos)` con TTL corto;
  `push_event` invalida la entrada del chain_dir.

### 3.4 Migración legacy explícita (`migrate_legacy_traces`)

```
NDJSON (data/traces/*.json.gz) ─┐
                                ├─► migrate_legacy_traces() ─► CognitiveGraph("trace")
Index (data/trace.json.gz) ─────┘        (ids = hash de contenido → idempotente)
```

Se conserva la lectura directa de legacy como fallback de lectura (compat), pero
ya no es la única vía: la migración consolida en el índice canónico. Un comando
CLI futuro puede invocarla para consolidar.

### 3.5 `search_traces` fail-closed (`application/search_service.py`)

- Claves desconocidas → el evento **no** matchea (fail-closed: un typo no devuelve todo).
- Nuevo filtro `sprint_id`/`sprint` (antes imposible a pesar de existir el campo).

### 3.6 MCP propietario (`mcp.py`)

El handler de `trace_push` resuelve `chain_dir` (proyecto → sprint activo, igual
que `dispatch_public_task`) y delega en `push_event` de la feature. `_public_task`
(engine dispatch) desaparece del feature.

### 3.7 Cero consumidores huérfanos

`search()` (fachada) se mantiene como API pública de la feature; `search_traces`
pasa a soportar el contrato completo de filtros. El ODM `entities.py` se elimina
(no tenía consumidores).

### 3.8 Fase B: contrato de provider → `libs/context` (IMPLEMENTADA)

**Target:** mover `ContextPiece`/`ContextBudget`/`ComposedContext`
(engine/context/models.py), `fmt_tokens` + `format_*` (engine/context/format.py)
y el ABC `ContextProvider` (engine/context/registry.py) a `libs/context/`, con
`features/engine/context/*` re-exportando para backward-compat. Beneficio: el
contrato del plugin deja de ser propiedad de una feature y los providers de
code_analysis/cognitive/delta/project/spring/event_tracing dependen de libs.

**Implementación:**

```
libs/context/
  models.py     → ContextPiece, ContextBudget, ComposedContext
  format.py     → fmt_tokens, format_ckg/cpg/trace/state/delta_results
  contracts.py  → ContextProvider (ABC del plugin)
  __init__.py   → API pública plana
```

- `features/engine/context/{models,format}.py` → shims puros de re-export
  (backward-compat, cero lógica).
- `features/engine/context/registry.py` → define solo `ContextProviderRegistry`;
  el ABC `ContextProvider` se importa de libs y se re-exporta.
- 6 providers de features (event_tracing, spring, cognitive_knowledge,
  code_analysis, project, delta_tracking) importan de `libs.context` — **0 matches**
  de `features.engine.context` en providers.
- `libs/context` importa solo stdlib (DIP verificado por grep).
- `ContextProviderRegistry`/`ContextComposer`/`QueryManager` permanecen en el
  engine: son el mecanismo de composición, no el contrato del plugin.

**Validación:** suite completa verde (294 tests) incluyendo
`test_reflection_context`, `test_auto_query_gate`, `test_cognitive_graph`,
`test_prompts_gates` y `build_context_registry()` con los 11 providers.

---

## 4. Impacto

| Área | Antes | Después |
|---|---|---|
| Modelo | 2 `TraceEvent` (ODM + dataclass) | 1 dataclass SSOT |
| Serialización | `to_dict` pierde `id`/`parent_id`/`parent_type` | round-trip simétrico |
| Unicidad | dedupe en aplicación (`reflection._dedupe_events`) | contrato del repositorio |
| Orden | set iteration (nondeterminista) | determinista por timestamp |
| Legacy | leído en cada llamada | cache por firma + migración explícita |
| MCP tool | passthrough al engine | handler de la feature |
| `search_traces` | fail-open | fail-closed + filtro sprint_id |
| Hot path prompt | re-escaneo legacy por build | cache en proceso (invalidación por mtime/TTL) |

### Trade-offs aceptados

- **Staleness del cache**: ventana corta (TTL) en la que un push reciente puede no
  reflejarse en `load_traces`; acotado y consistente con el modelo snapshot del walker.
- ~~Fase B diferida~~ — resuelto: el contrato de provider vive en `libs/context`
  (Fase B implementada, §3.8).

---

## 5. Alternativas consideradas

| Alternativa | Por qué se rechaza |
|---|---|
| **Mantener el ODM como modelo** | El ODM no puede representar eventos completos (sin `input`/`output`/`parent_id`/`started_at`/`finished_at`/`id`) y no tiene consumidores |
| **Dedupe en cada consumidor** | Multiplica lógica y vuelve a inflar conteos; la unicidad es responsabilidad del repo |
| **Auto-migrar dentro de `load_traces`** | Convertiría lecturas en escrituras (efectos en el camino feliz y en tests); la migración es una operación explícita |
| **Mover el contrato de provider a libs YA** | Blast radius engine-wide; se documenta como Fase B con pasos concretos |
| **Eliminar `search()`/`search_traces`** | Es la API pública de la feature (CLI/agentes); se mejora (fail-closed) en vez de eliminarla |

---

## 6. Pasos de implementación

**Fase A (implementada en esta sesión):**
1. `domain/models.py` — round-trip simétrico.
2. `application/ports/trace_repository.py` — puerto (nuevo).
3. `infrastructure/repository.py` — `CognitiveTraceRepository` + dedupe central +
   orden determinista + cache por firma + `migrate_legacy_traces` + `push_event`.
4. `application/search_service.py` — fail-closed + filtro `sprint_id`.
5. `application/reflection.py` — eliminar `_dedupe_events` (compensación).
6. `mcp.py` — handler propietario (push_event). `__init__.py` — un solo `TraceEvent`.
7. Eliminar `entities.py` (ODM duplicado sin consumidores).
8. Tests de diseño: round-trip, fail-closed, dedupe, migración idempotente.

**Fase B (implementada — pasos ejecutados):**
1. Crear `libs/context/{models,format,contracts,__init__}.py` con el contrato
   completo (incluye el ABC `ContextProvider` en `contracts.py`).
2. `features/engine/context/{models,format}.py` re-exportan desde libs
   (backward-compat); `registry.py` define solo `ContextProviderRegistry`.
3. Actualizar imports de los 6 providers de features a `libs.context`.
4. Validar: suite completa verde (294) + grep DIP (0 matches) + registry 11 providers.

---

## 7. Reglas de diseño (non-negotiables)

- **Un solo `TraceEvent`** (dominio) — cero duplicación de modelos.
- **El repositorio garantiza unicidad y orden** — la aplicación no compensa.
- **Serialización simétrica** — `to_dict`/`from_dict`/`from_ndjson` nunca pierden identidad.
- **`search_traces` fail-closed** — filtro desconocido ≠ devolver todo.
- **El MCP tool es de la feature** — no passthrough al task registry del engine.
- **La migración legacy es explícita e idempotente** — nunca escritura en el camino feliz de lectura.
- **Result pattern (convention-001)** — cero except silenciosos, logs por nivel.

---

## 8. Estado

| Fase | Contenido | Estado |
|---|---|---|
| **A** | §3.1–3.7 (modelo, puerto, repo, migración, search, mcp, limpieza) | ✅ IMPLEMENTADA + tests verdes |
| **B** | §3.8 (contrato de provider → `libs/context`, incl. `ContextProvider` ABC) | ✅ IMPLEMENTADA — 294 tests verdes, DIP verificado (0 matches de `features.engine.context` en providers) |
