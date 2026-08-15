# ADR-WALKER-049: Workflow Actions — tasks y side effects como una sola capa de binding entre el engine y los features

> **Status:** Accepted
> **Fecha:** 2026-08-13 (refinado: 2026-08-14 — análisis arquitectónico completo)
> **Aprobado:** 2026-08-15
> **Autor:** Kilo (agente) + PO
> **Complementa:** ADR-WALKER-023 (use cases esconden infraestructura), ADR-WALKER-047 (confusión de registries), ADR-WALKER-048 (patrón canonical de capa de aplicación), ADR-WALKER-051 (main = composition root + orchestration), ADR-WALKER-045 F5 (mover side effects a main)
> **Pregunta que responde este ADR:** ¿cómo encajan tasks y side effects dentro de la arquitectura del workflow, del resto de la aplicación, dentro de main, y en su integración con los features?
> **Respuesta corta:** tasks y side effects **no son dos cosas distintas** — son UNA capa: **workflow actions**, el binding entre el state machine del engine y las Application Services de los features. El grafo las declara en dos puntos de invocación (`.action` y `.side_effects[]`), el engine las despacha por nombre contra registries. Los problemas observados (naming, envelope, stubs, mocks, 4 catálogos) son **síntomas de que esa capa nunca fue reconocida como elemento arquitectónico de primera clase** con un contrato y un catálogo únicos.

---

## 1. Contexto arquitectónico — la capa que no tiene nombre

### 1.1 Dónde viven hoy

```
┌─ TRANSPORT (MCP/CLI) ──────────── features/main/cli_interface ─────────┐
│   tools = actions públicas (scope=public) llamadas por nombre          │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   ▼
┌─ ENGINE (core/engine) ─────────── state machine del workflow ──────────┐
│   grafo: nodes → sub_states (action=TASK) + gates/transitions          │
│   · sub_state.action   → _task_registry.execute(action)  (orchestrator:663/812) │
│   · side_effects[].type → side_effect_engine.execute()   (side_effects.py:110)  │
│   PORTS: TaskRegistry + SideEffectEngine (el engine despacha por NOMBRE)│
└──────────────────────────────────┬─────────────────────────────────────┘
                                   ▼
┌─ WORKFLOW ACTIONS (features/main/task_execution) ──── LA CAPA SIN NOMBRE ─┐
│   tasks/  (59)  → verbos del sub_state (pasos) + tools públicas           │
│   side_effects/ (19) → efectos post-transición (con compensación)         │
│   cada acción: (ctx, data) → 1 llamada a 1 Application Service            │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   ▼
┌─ ORCHESTRATION (orchestration/) ── solo acciones cross-feature ──────────┐
│   TaskIntelligenceService, PrepareProjectService, CheckHealthService     │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   ▼
┌─ FEATURES (hexagonal) ──────────────────────────────────────────────────┐
│   Application Services (fachadas) → use_cases (lógica atómica) → domain │
│   delta_tracking, cognitive_knowledge, code_analysis, project, trace    │
└──────────────────────────────────────────────────────────────────────────┘
```

### 1.2 El hallazgo arquitectónico: DOS puntos de invocación del MISMO concepto

Verificado en `core/engine/workflow/nodes/*.json`: el grafo declara **ambos** canales en cada sub_state:

```json
{
  "name": "TASK_REVIEW",
  "action": "spec_task",                 ← TASK: el verbo del sub_state (qué ejecuta el engine para llenar el estado)
  "side_effects": [
    {"type": "advance_task"},            ← SIDE EFFECT: consecuencia post-transición (con compensation)
    {"type": "create_adr_from_spec"}
  ]
}
```

El engine los ejecuta por **nombre** contra dos registries:
- **Tasks**: `orchestrator.py:663` y `:812` → `_task_registry.execute(action, exec_ctx, input_data)`
- **Side effects**: `side_effects.py:110` → `side_effect_engine.execute(...)`

**Consecuencia arquitectónica:** la diferencia entre task y side effect NO es estructural (misma forma: name → handler → Result) — es **solo el punto de invocación en el grafo**:
- `action` = se ejecuta cuando el sub_state es el actual (el engine lo necesita para producir el input/resultado del estado)
- `side_effect` = se dispara al completar la transición (consecuencia, con compensación)

Y ambos **delegan en Application Services de features** (ADR-048): son la capa de binding entre el intent del grafo (names) y la capacidad de negocio (servicios).

---

## 2. Análisis arquitectónico — el rol de la capa

### 2.1 El engine es un state machine puro que despacha por nombre

`core/engine` no importa features (G5, 0 imports verificados) y no sabe qué hace cada acción: resuelve el sub_state actual, ejecuta su `action` por nombre, valida gates, transiciona y dispara `side_effects` por nombre. **El contrato del engine es un puerto: "dame un registry que resuelva un name → handler (ctx, data) → Result".**

De esto se derivan tres reglas:

**R1. La capa de acciones es el anti-corruption layer (ACL) entre engine y features.**
Es la ÚNICA capa donde el vocabulario del engine (names del workflow: `plan_sprint`, `save_to_ckg`, `advance_task`) se encuentra con el vocabulario de negocio de los features (Application Services). Por eso el **naming semántico no es cosmético**: es el contrato del ACL. Si los names son de infraestructura (`update_cpg`, `delete_ckg_node`), el ACL filtra el vocabulario equivocado — los features quedan acoplados al nombre, no a la operación.

**R2. El engine depende de un puerto (interfaz), main provee la implementación (DIP).**
El engine debería consumir **un solo contrato de resolución de acciones** — hoy consume **4 catálogos fragmentados** (TaskRegistry, DomainTaskRegistry hardcodeado, module_registry, SystemTaskRegistry) que son vistas parciales del mismo puerto. La fragmentación (C1) no es un problema de "limpieza": es **el puerto roto en 4 pedazos**, lo que hace que un name de acción exista en una vista y no en otra (12 fantasmas en el DomainTaskRegistry) y que las tools públicas se dupliquen (module_registry ∩ task_registry ~11).

**R3. Una acción = una operación de negocio = un name, dondequiera que se invoque.**
El mismo `save_to_ckg` aparece como `action` en design (sub_state CKG_SAVE) Y como `side_effect` en analyze/implement/refine/sprint. **Es la misma operación de negocio con dos puntos de invocación** — y hoy tiene además el alias `save_ckg` (3 names para 1 operación). Con un vocabulario único, el grafo referencia `documenting_knowledge` tanto en `action` como en `side_effects`, y el catálogo la resuelve igual en ambos puntos.

### 2.2 Cómo encaja en el resto de la aplicación

| Capa | Rol | Consume acciones |
|---|---|---|
| Transport (MCP/CLI) | expone acciones **públicas** como tools (`kg_search`, `status`, `submit`...) | sí, por name (adapter MCP) |
| Engine (core/engine) | state machine puro: sub_state actual → action → gates → transition → side effects | sí, por name (2 registries) |
| **Acciones (main/task_execution)** | **ACL: traducen intent del grafo → Application Service** | — |
| Orchestration (orchestration/) | acciones **cross-feature** (TaskIntelligenceService = kg_task_context/kg_diagnose/...) | consumida por acciones |
| Features (hexagonal) | Application Services = fachadas públicas del feature | expone servicios |

### 2.3 Cómo encaja dentro de main

main (INV-014) no es un feature y no tiene dominio. Tiene **tres responsabilidades**, y la capa de acciones es la tercera (hoy sin nombre propio):

1. **Composition root** — cablea todo (registra Application Services + infraestructura, nunca use cases).
2. **Orchestration** — lógica cross-feature (`orchestration/`).
3. **Workflow actions** — `task_execution/tasks/` + `task_execution/side_effects/`: los adapters del engine. No son features (sin dominio), no son orchestration (una sola operación de negocio, no cruza features), no son el engine (sin lógica de grafo).

**Lo que distingue a la capa:** es la entrega ("delivery mechanism") del engine. Una acción es una función pura de traducción: valida input, llama al servicio, envuelve el resultado. Cualquier lógica que no sea traducción pertenece al Application Service del feature (o a orchestration si cruza features).

### 2.4 Cómo se integra con los features (el contrato)

La integración es una **tabla de binding** acción → método de Application Service:

| Acción (name) | Feature | Application Service | Método |
|---|---|---|---|
| `plan_sprint`, `spec_task`, `close_sprint`... | delta_tracking | SprintApplicationService | plan/spec/close |
| `save_to_ckg` (action y side_effect) | cognitive_knowledge | CkgApplicationService | save_knowledge |
| `kg_extract`, `ckg_analyze`, `collect_knowledge` | cognitive_knowledge | CkgApplicationService | build_extract_payload/analyze |
| `review_code`, `cpg_analyze`, `update_cpg` | code_analysis | CpgService | review/business_layer |
| `detect_project_context`, `build_project_config` | project | ProjectService | detect/build |
| `trace_push` | event_tracing | TraceService | push |
| `kg_task_context`, `kg_diagnose`, `agent_context` | cross-feature | orchestration/IntelligenceService | task_context/diagnose |

Reglas de integración (ADR-048/051):
1. **Una acción = un servicio**: si una acción necesita orquestar 2+ features, esa acción consume un servicio de `orchestration/`, no hace la orquestación inline.
2. **Nunca use_cases directos** (INV-013), nunca infra de features.
3. **Si una acción tiene lógica de negocio, falta un Application Service** — la lógica sube al feature, la acción queda como traducción.
4. Los features evolucionan internamente (refactorizan use_cases/domain) sin romper nada: **la capa de acciones es el contrato estable** entre engine y features.

---

## 3. La unificación — WorkflowAction

**Decisión de diseño central:** tasks y side effects se modelan como UN solo tipo — `WorkflowAction` — con un campo `kind` que indica el punto de invocación en el grafo. Un solo catálogo, un solo contrato, un solo vocabulario.

```python
WorkflowAction = {
  name: str,                 # OPERACIÓN DE NEGOCIO (semántico, único)
  kind: "sub_state_action"   # se ejecuta como .action del sub_state (task)
      | "side_effect",       # se dispara post-transición
  scope: "internal" | "public" | "system",   # public = tool MCP
  handler: (ctx, data) -> TaskResult,        # 1 llamada a 1 Application Service
  compensate: (ctx, data) -> TaskResult | None,  # efectos de escritura (saga)
  metadata: {description, usage, tags, input_schema},  # declarativo (patrón kg_metadata)
}
```

**Un solo catálogo (ActionCatalog)** reemplaza los 4 actuales:
- `TaskRegistry` (59) + `DomainTaskRegistry` (24 hardcode) + `module_registry` (13) + `SystemTaskRegistry` → **UN ActionCatalog** con metadata declarativa.
- MCP tools = `kind in (sub_state_action, side_effect) and scope == "public"`.
- Steps del workflow = `kind == "sub_state_action" and scope == "internal"`.
- Efectos = `kind == "side_effect"`.
- El engine consume la **interfaz** (port); main provee la implementación (DIP, R2). `DomainTaskRegistry` se deriva del catálogo real (0 fantasmas).

**Un solo contrato:** `TaskResult` para ambas (las side effects ya se envuelven en el envelope `{status, type, scope, summary, signals}` — el mismo shape para las dos, con `compensate` opcional para las de escritura).

**Un solo vocabulario:** una operación de negocio = un name, en cualquier punto de invocación (R3). `documenting_knowledge` es la misma operación como `action` de design y como `side_effect` de analyze/sprint — con alias `deprecated=True` solo durante la transición.

---

## 4. MAPA HOY — evidencia (reframeada como síntomas de la capa no reconocida)

### 4.1 Síntoma A — el puerto roto en 4 catálogos

| Catálogo | Contenido | Estado |
|---|---|---|
| `TaskRegistry` (main) | 59 tasks reales (32 internal, 26 public, 1 system) | ✅ SSOT real |
| `DomainTaskRegistry` (engine) | 24 names hardcodeados | ❌ **12 fantasmas** (`qa_run`, `implement`, `po_accept`, `user_gate`... no existen en el registro real) |
| `module_registry` (engine) | 13 tools | ❌ duplica ~11 del TaskRegistry (`status`, `submit`, `kg_search`, `save_to_ckg`...) |
| `SystemTaskRegistry` (bootstrap) | 0 tasks | ❌ vacío |

**Efecto arquitectónico:** el engine no tiene UN puerto de resolución de acciones; tiene 4 vistas parciales que se desincronizan. Un name existe en una vista y no en otra → los gates del workflow que invocan un fantasma no ejecutan (None silencioso, misma clase de bug que los side effects).

### 4.2 Síntoma B — el ACL filtra el vocabulario equivocado (naming)

| Name HOY | Operación de negocio | Name semántico |
|---|---|---|
| `save_to_ckg` / `save_ckg` | documentar conocimiento | `documenting_knowledge` |
| `update_cpg` / `update_code_index` | indexar código | `index_code` |
| `delete_ckg_node` / `rollback_ckg` | remover nodo de conocimiento | `remove_ckg_node` / `restore_ckg_node` |
| `update_sprint_status` | estado del sprint | `sprint_status` |
| `update_delta_graph` | sincronizar delta | `sync_delta` |
| `index_delta` / `reindex_delta` | indexar delta | `index_delta` (unificar) |

El mismo `save_to_ckg` es **a la vez** `action` (design CKG_SAVE) y `side_effect` (analyze/implement/refine/sprint) — la evidencia más clara de que es un solo concepto. Hoy conviven 3 names para esa operación (`save_to_ckg`, `save_ckg`, y el canónico de tools `documenting_knowledge` del ADR-048).

### 4.3 Síntoma C — el grafo declara acciones que la capa no implementa (bugs activos)

Mapa verificado `nodo → sub_state → side_effects`:

| Nodo | Sub_state | Side effects |
|---|---|---|
| analyze | GATHER / REVIEW / KNOWLEDGE_CAPTURE | `update_code_index` / `capture_decision` / `save_to_ckg`[comp:`delete_ckg_nodes`], `emit_event`, `update_ckg_status` |
| design | DESIGN_GATHER / DESIGN_REVIEW / CKG_SAVE | `update_code_index`, `build_project_graph` / `capture_decision` / `save_ckg`[comp], `update_delta_graph` |
| implement | USER_GATE / WORK / CODE_REVIEW_SCAN / CODE_REVIEW_RESULT / KNOWLEDGE_UPDATE / REPORT_GATE | `capture_decision`, `transform_spec` / `update_cpg`[comp], `update_ckg_status`, `transform_spec` / `update_cpg`, `transform_spec` / `capture_decision`, `transform_spec` / `transform_spec` / `save_to_ckg`, `capture_decision`, `transform_spec` |
| project_init | SCENARIO / BUILD_CKG / REVIEW | `capture_decision` / `save_to_ckg` / `capture_decision` |
| refine | DIAGNOSE / KNOWLEDGE_CAPTURE / ESCALATION | `emit_event` / `save_to_ckg`[comp:`delete_ckg_nodes`], `emit_event` / `emit_event`, `save_to_ckg` |
| sprint | PLANNING / PLAN_APPROVAL / TASK_SPEC / TASK_REVIEW / REVIEW / RETRO / CLOSE_CONFIRM / CLOSE | `emit_event` / `update_sprint_status`, `capture_decision` / `validate_plan`, `transform_spec` / **`advance_task`**, `validate_plan`, `capture_decision`, `transform_spec`, **`create_adr_from_spec`** / `emit_event`, `update_sprint_status`, `capture_decision` / `save_to_ckg`, `emit_event` / `capture_decision` / `emit_event`, `archive_sprint`, `reindex_delta` |
| sprint_planning | REVIEW | `emit_event` |
| verify | QA / PO_ACCEPT | `capture_decision` / `emit_event`, `index_delta`, `capture_decision` |

**3 BUGS ACTIVOS (nodos declaran names sin handler):**

| Name en nodos | Registrado | Efecto HOY |
|---|---|---|
| `advance_task` (sprint TASK_REVIEW) | ❌ | El sprint completa la revisión pero **no avanza el índice** → reprocesa la misma spec (loop) |
| `create_adr_from_spec` (sprint TASK_REVIEW) | ❌ | La decisión aprobada **no genera el ADR** → invisible para CKG |
| `delete_ckg_nodes` (plural, analyze/implement/refine — **es la compensación** de `save_to_ckg`) | ❌ solo `delete_ckg_node` (singular) | El rollback de conocimiento **no ejecuta** → nodos huérfanos en CKG |

El dispatcher los busca en handlers y stubs, no los encuentra → None silencioso (side_effects.py:563). **Es el failure_scenario del ADR ya materializado** — y la prueba de que la capa de acciones necesita el checklist de completitud (patrón AGENTS.md) para que un name declarado en el grafo sin implementación falle en CI, no en producción.

### 4.4 Síntoma D — la capa de acciones contiene cosas que no son acciones

| Item | Qué es realmente | Problema |
|---|---|---|
| `mocks.py` (`register_test_mocks`, 7 doubles: plan_sprint, implement, qa_run...) | test doubles | Vive en el paquete de producción `tasks/`; un root que lo llame pisa implementaciones reales |
| `run_tests` (`{"passed": True, "total": 0}`) | **falso positivo** | VERIFY.QA_RUN reporta verde sin correr nada — el gate de calidad miente |
| `transforms.py` (3) + `refinement.py` (3) + `implement_task` | passthrough stubs (`{"transformed": True}`) | El grafo declara verbos que la capa no implementa — aristas muertas |
| `query.py` | legacy | El metadata ya dice "Legacy; si vacío sugiere kg_multi" (G8) |
| `domain/models.py` (`TaskHandler` ABC) | abstracción muerta | 0 usos; main no tiene dominio (INV-014) |
| `close_sprint` | **duplicado** (planning.py:158 + knowledge.py:46) | 2 handlers para 1 name; gana el orden de discovery (no determinista) |
| handlers huérfanos en `delta_tracking/side_effects/` (planning.py, create_adr.py) | código sin cablear (revert ADR-045 F5) | SSOT dividido; tests importan del paquete viejo (conftest.py:241) |
| `cpg_updates.py` (262 líneas) | orquestación pesada | La lógica debería vivir en CpgService; el handler = disparar + envolver |

### 4.5 Síntoma E — contrato fragmentado (envelope + compensación)

- **Envelope**: 13/19 handlers usan `@as_side_effect`; 6 legacy (`cpg_updates` 4, `events`, `spec_transform`) retornan dicts propios → el Trace recibe shapes inconsistentes.
- **Compensación**: **0/19 handlers declaran `compensate`** — pero 3 nodos declaran `compensation` en el JSON (analyze, implement, refine). La infra de saga existe; la atomicidad del workflow es **diseño declarado sin ejecución**.

---

## 5. MAPA DEBERÍA — la capa de acciones como ciudadano de primera clase

### 5.1 Arquitectura objetivo

```
        nodo del workflow (grafo)
              │  .action = WORKFLOW ACTION (kind=sub_state_action)
              │  .side_effects[].type = WORKFLOW ACTION (kind=side_effect)
              │  valida contra el ActionCatalog (checklist) → 0 names huérfanos
              ▼
┌──────────────────────────────────────────────────────────────┐
│  ENGINE (core/engine) — state machine puro                   │
│  consume el PUERTO ActionCatalog (interfaz), por DIP         │
│  _task_registry.execute(name) + side_effect_engine.execute() │
│  → UN solo mecanismo de resolución por nombre                │
└──────────────────────────┬───────────────────────────────────┘
                           │  implementa (composition root)
                           ▼
┌──────────────────────────────────────────────────────────────┐
│  WORKFLOW ACTIONS (features/main/task_execution)             │
│  UN catálogo declarativo: name → kind → handler → compensate │
│  · names SEMÁNTICOS (operación de negocio)                   │
│  · handler = 1 llamada a 1 Application Service               │
│  · envelope TaskResult (status/type/scope/...) para ambas    │
│  · compensate declarado o idempotencia explícita             │
│  · 0 doubles, 0 stubs, 0 duplicados                          │
└──────────────┬──────────────────────────────┬────────────────┘
               ▼                              ▼
   orchestration/ (cross-feature)   features/*/application/services/
```

### 5.2 Mapa DEBERÍA — tasks (destino por grupo)

| Grupo | Tasks | DEBERÍA |
|---|---|---|
| Sprint (12) | plan_sprint, spec_task, save_task_spec, show_dashboard, close_sprint, edit_sprint, validate_plan, log_rejection_to_ckg, notify_po_escalation, archive_sprint_metrics, advance_task_index, update_sprint_progress | ✅ ya delegan en `SprintApplicationService`. Resolver duplicado `close_sprint` |
| Conocimiento (8) | session_init, collect_knowledge, analyze_capas, review_analysis, gather_context, gather_task_context, kg_extract, ckg_analyze, capture_task, index_repo, research_topic, build_initial_ckg | ✅ ya delegan en `CkgApplicationService`/`SessionCaptureService`. Mantener |
| CPG (3) | review_code, cpg_analyze, scan_code_and_build_cpg | ✅ ya usan `CpgService` vía port. Mantener |
| Fases kg (5) | kg_task_context, kg_diagnose, kg_qa_evidence, kg_sprint_evidence, agent_context | ✅ consumen `orchestration/` (IntelligenceService). Mantener |
| Engine (6) | status, submit, transition, prepare, list, help | ✅ tools del engine. En el catálogo unificado, `scope=public`, resueltas por el mismo ActionCatalog |
| Proyecto (4) | detect_project_context, build_project_config, create_first_sprint, add_tasks, list_projects, update_project | ✅ delegan en `ProjectService`/`SprintApplicationService`. Mantener |
| Trace (1) | trace_push | ✅ `TraceService`. Mantener |
| **Stubs (10)** | implement_task, run_tests, prepare_analysis_context, build_task_briefing, build_verification_context, refine_diagnose, route_to_target, escalate_refine | **Decisión por edge**: o lógica real (→ Application Service) o se eliminan. `run_tests` falso positivo: prohibido |
| Legacy (1) | query | Deprecar a favor de kg_multi (G8) |
| **Mocks (7)** | register_test_mocks | Fuera de producción: a `tests/` con registry de test inyectado |
| Fantasmas (12) | advance_task, gather_analysis, gather_design, implement, po_accept, qa_run, report_gate, review_task, scan_code, start_task, user_gate, verify_user_gate | Eliminar del DomainTaskRegistry: el catálogo unificado deriva la vista del registro real |

### 5.3 Mapa DEBERÍA — side effects (canónico, un solo vocabulario)

| HOY (legacy) | DEBERÍA (canónico) | Envelope | Compensación |
|---|---|---|---|
| `save_to_ckg` / `save_ckg` | `documenting_knowledge` | ✅ | `remove_knowledge` (era `delete_ckg_nodes`) |
| `update_cpg` / `update_code_index` | `index_code` | ✅ (hoy legacy) | `remove_code_index` |
| `update_delta_graph` | `sync_delta` | ✅ | — |
| `update_ckg_status` | `ckg_status` | ✅ | — |
| `update_sprint_status` | `sprint_status` | ✅ | — |
| `delete_ckg_node` / `rollback_ckg` | `remove_ckg_node` / `restore_ckg_node` | ✅ | — |
| `index_delta` / `reindex_delta` | `index_delta` (unificar) | ✅ | — |
| `advance_task` (side effect) | `advance_sprint_task` | ✅ | — |
| `create_adr_from_spec` | `create_adr` | ✅ | `remove_adr` |
| `emit_event` / `transform_spec` / `validate_plan` / `archive_sprint` / `build_project_graph` / `capture_decision` / `detect_decisions` | mantener | ✅ (3 legacy → envelope) | — |

### 5.4 Propuesta de solución arquitectónica — contrato de fallo unificado y routing a on_failure

**El problema que resuelve esta propuesta (verificado en código):** tasks y side effects tienen **políticas de fallo OPUESTAS**:

| Canal | Camino de fallo | Efecto | Evidencia |
|---|---|---|---|
| **Task** (`.action` del sub_state) | `InMemoryTaskRegistry.execute` → `TaskResult.failure` (TaskNotFoundError o excepción envuelta) → `_execute_task` eleva `AutoAdvanceError` si `not ok` → llega al `ErrorHandler` | **FAIL-LOUD**: el submit FALLA y el workflow se bloquea con mensaje (o sigue la estrategia del sub_state) | task_registry.py:44-73, use_cases.py:410 |
| **Side effect** (`side_effects[]`) | `SideEffectEngine.execute` → handler retorna None/ok=False → compensa previos → NO eleva ninguna excepción → `_execute_side_effects` solo registra `ok=False` en el estado | **SILENCIOSO**: el submit CONTINÚA y el workflow avanza sin ejecutar la acción — nadie se entera | side_effects.py:110-230, use_cases.py `_execute_side_effects` |

**La asimetría es la causa raíz de los 3 bugs activos** (sección 4.3): `advance_task`, `create_adr_from_spec` y `delete_ckg_nodes` caen al camino silencioso — el sprint avanza "exitosamente" sin avanzar la tarea, sin crear el ADR y sin rollback. Además, los stubs legacy del dispatcher (`update_cpg`, `save_ckg`, `save_to_ckg`...) devuelven dicts con `"updated": True` / `"saved": True` — **falsos éxitos**: reportan que hicieron algo sin hacerlo.

**La infraestructura de routing YA existe pero solo la alimentan las tasks** — verificado en `core/engine/application/error_handler.py` + `core/engine/domain/errors.py`:

```
ErrorHandler.handle(ctx, error, sub_state)
  ├─ classify(error) → ErrorType (validation/execution/transition/state/session/infrastructure/unknown)
  ├─ resolve_strategy(): sub_state.retry.on_error (por nodo) → fallback DEFAULT_STRATEGIES
  │    (validation→block+message, execution→retry, transition→block+message, state→escalate,
  │     session→retry, infrastructure→retry_2x, unknown→retry)
  └─ ON_FAILURE → _on_failure(): AdvanceService.jump_to_sub_state(state, graph, sub_state.on_failure)
                                    → si falla → transition_uc.execute(gate_name=sub_state.on_failure)
  Otras estrategias: RETRY/RETRY_2X (re-submit), COMPENSATE (saga), ESCALATE (BlockResolver), BLOCK_MESSAGE
```

El puente de nombres `_ERROR_CLASS_BRIDGE` ya mapea excepciones del engine a claves de plantilla (`AutoAdvanceError`/`TaskNotFoundError` → `TaskFailedError`; `ValidationError` → `SchemaViolation`; `TransitionBlockedError`/`GateValidationError`/`SubStateNotFoundError` → `PreconditionError`). **Lo único que falta es que el canal de side effects alimente este machinery.**

**Propuesta (3 cambios):**

1. **Una semántica de fallo para toda WorkflowAction**: el catálogo (sección 5.4) incorpora `on_failure` (destino) + `retry` (`max_attempts` + mapa `on_error` por tipo/clase de error) + `compensate`, declarados por acción — igual que los sub_states con `action` ya lo hacen en `verify.node.json` (`"on_error": {"TaskTimeoutError": "retry_2x", "TaskFailedError": "on_failure"}`). Mismo contrato para tasks y side effects.
2. **Cerrar la asimetría**: `SideEffectEngine.execute()` devuelve `side_failed=True` cuando un efecto no-optional falla (None o ok=False) y el pipeline de submit eleva `SideEffectFailedError` (clasificado como `TaskFailedError` vía el bridge) → el error llega al `ErrorHandler` → estrategia del sub_state (on_failure/retry/block/escalate) → **el side effect que falla termina en el MISMO machinery que la task que falla**. Opcionalmente por efecto: `on_failure` declarado en el nodo (ej. `{"type": "index_code", "on_failure": "RETRY"}`).

   **La semántica `optional` YA existe — este cambio es más barato de lo que parece (verificado):** `SideEffectDef.optional` (`core/engine/domain/values.py:50`, default `False` = OBLIGATORIO) se parsea de los nodos en `repositories.py:169` y `entities.py:143`, y ya se declara en los JSON (`sprint`, `implement`, `design`, `refine`, `analyze`). PERO hoy `optional` solo controla la **saga** (si el fallo dispara compensación, side_effects.py:168/221/232) — **NO controla el routing de error**: ni siquiera un efecto obligatorio que falla llega al ErrorHandler; compensa, hace `break` y el submit continúa. Entonces el cambio 2 **no agrega configuración**: solo cambia la política de fallo del obligatorio (`optional=False` falla → elevar `SideEffectFailedError` → ErrorHandler → on_failure), dejando el opcional (`optional=True`) como hoy (warning y continuar).
3. **Checklist de completitud en CI** (sección 7): `{action} ∪ {side_effect.type} ∪ {compensation.type} ⊆ ActionCatalog` → los 3 bugs activos y cualquier name futuro sin implementación fallan en CI antes de tocar producción.

**Resultado:** el canal de acciones tiene UNA política de fallo: *toda acción que falla llega al `ErrorHandler`, que decide **por nodo** (`on_failure` + `retry.on_error` del sub_state) con fallback **por contexto** (`DEFAULT_STRATEGIES`, `BlockResolver` para escalate, eventos `on:{}` para rerouting por señal)*. Los mecanismos por nodo y por contexto ya existen y están verificados — esta propuesta solo extiende su cobertura al canal de side effects.

---

## 6. Consecuencias

### 6.1 Positivas

- **El engine deja de tener 4 catálogos**: un puerto (ActionCatalog) que la composition root implementa; los 12 fantasmas y las ~11 tools duplicadas desaparecen estructuralmente.
- **Una operación de negocio = un name**: `documenting_knowledge` como action y como side_effect; el ACL filtra el vocabulario correcto.
- **3 bugs activos corregidos** (advance_task, create_adr_from_spec, delete_ckg_nodes): el sprint avanza, las decisiones generan ADR, el rollback ejecuta.
- **Contrato único**: TaskResult/envelope para ambas; compensación declarada o idempotencia; el Trace recibe shape consistente.
- **La capa de acciones queda reconocida**: tercer rol de main (composition root + orchestration + actions), con reglas claras de qué es una acción y qué no (sin doubles, sin stubs, sin lógica).
- **La integración con features es un contrato**: la tabla de binding acción → Application Service permite que los features refactoricen internamente sin romper el workflow.

### 6.2 Negativas / Trade-offs

- **Refactor amplio**: unificar los 4 catálogos toca engine (dispatcher, registries), mcp de features y del engine, ~20 nodos JSON, ~19 registros de side effects, 59 tasks, tests. Por fases (sección 9).
- **Riesgo de romper el workflow**: un name mal migrado = acción que no ejecuta. Mitigado por checklist de completitud + suite E2E.
- **Aliases transitorios**: conviven canónico + alias durante la migración; un dev nuevo podría usar el alias. Mitigado en failure_scenarios.
- **Quitar mocks/stubs puede romper tests E2E** que dependían del determinismo por nombre: migrar a registry de test inyectado ANTES de retirar.
- **El unificado cambia el contrato del DomainTaskRegistry**: el engine que invocaba fantasmas (ej. `qa_run`) hoy "falla silenciosamente"; al derivar del catálogo real, esos gates dejan de declararse — hay que decidir si el workflow necesita la task real o elimina el gate.

---

## 7. failure_scenarios

| # | Fallo | Causa raíz | Mitigación | Detección |
|---|---|---|---|---|
| 1 | **Acción en nodos sin handler → None silencioso** (HOY: `advance_task`, `create_adr_from_spec`, `delete_ckg_nodes`) | Grafo y catálogo se desincronizan; el dispatcher devuelve None sin error | Checklist de completitud (`{action} ∪ {side_effect.type} ∪ {compensation.type} ⊆ ActionCatalog`); orden: 1) canónico+alias, 2) migrar nodos, 3) retirar alias | `tests/test_workflow_action_completeness_bdd.py` + E2E que verifica `ops_completed` |
| 2 | El puerto fragmentado vuelve: un name existe en un catálogo y no en otro → gates que no ejecutan | 4 catálogos como vistas parciales | Unificar en ActionCatalog; la composition root lo construye UNA vez; checklist de completitud del catálogo | Test: `DomainTaskRegistry.names ⊆ TaskRegistry.names` y `task_registry ∩ module_registry = ∅` |
| 3 | Dev registra name legacy y el alias pisa al canónico | Aliases conviven y son visibles | `deprecated=True` + linter + grep CI | `grep save_to_ckg\|update_cpg\|delete_ckg_node` en side_effects/, tasks/ y nodes/ → 0 |
| 4 | El stub del dispatcher responde en silencio para un type con handler modular | Stubs hardcodeados sobreviven | Eliminar stubs que duplican handlers modulares | Test: todo name de nodo pasa SIEMPRE por el catálogo |
| 5 | Handler sin envelope retorna dict propio → el Trace parsea campos que no existen | 6 handlers legacy | Envelope obligatorio en el registro | Test de completitud de envelope + E2E verifica `status/type/scope` |
| 6 | Saga falla a mitad y no hay compensación → estado a medias | 0/19 declaran `compensate` | Todo action de escritura declara compensación o idempotencia | Test: todo `compensation` en nodos tiene handler + suite de atomicidad |
| 7 | `mocks.py` en producción pisa tasks reales | Test doubles en el paquete de producción | Mover a `tests/`; `tasks/` sin archivos de test | Grep CI: `register_test_mocks\|mock` en `tasks/` → 0 |
| 8 | `run_tests` falso positivo: VERIFY reporta verde sin correr nada | Stub devuelve `passed=True, total=0` | Prohibir stubs de verificación: runner real o la task no existe | Test: `total > 0` o la task no se registra |
| 9 | Renombrar un side effect rompe `test_workflow_e2e_bdd.py:216` (`save_to_ckg`) | Test acoplado al name legacy | Migrar el assert en la MISMA fase del rename | Suite E2E verde tras cada fase |
| 10 | Los huérfanos de `delta_tracking/side_effects/` se quedan y un dev los registra desde el paquete viejo → SSOT dividido | Revert dejó código sin cablear | Resolver SSOT; shim temporal con grep CI de retiro | Grep: `delta_tracking.side_effects` en features/tests/core → 0 |
| 11 | Al unificar el catálogo, un gate del workflow que invocaba un fantasma (`qa_run`) desaparece → el workflow pierde un paso sin aviso | La vista derivada elimina lo que el grafo aún declara | Checklist de completitud cubre TAMBIÉN `.action` de nodos (no solo side_effects); decidir por gate: task real o eliminar el gate | Test de completitud reporta `{action} en nodos no registrados: {qa_run, ...}` |
| 12 | Quitar stubs rompe tests E2E que dependían del determinismo por nombre | Suite E2E acoplada a `{"status": "planned"}` del mock | Migrar a registry de test inyectado ANTES de retirar | Suite E2E verde tras la migración de mocks |

---

## 8. Implementación por fases

### Fase 0 — Corregir los bugs activos (crítico, hoy)
1. Registrar `advance_task`/`save_task_spec` + `create_adr_from_spec` desde el SSOT de main (o alinear el name del nodo); resolver huérfanos de `delta_tracking/side_effects/` (migrar conftest.py:241 + test_create_adr_bdd.py).
2. Registrar `delete_ckg_nodes` como compensación real (o renombrar nodos al canónico `remove_knowledge`).
3. Test de completitud nuevo: `{action de nodos} ∪ {side_effect.type} ∪ {compensation.type} ⊆ registro`.

### Fase 1 — El puerto: ActionCatalog (unificar los 4 catálogos)
1. Definir el modelo `WorkflowAction` (name/kind/scope/handler/compensate/metadata) y el `ActionCatalog`.
2. La composition root construye UN catálogo desde los módulos de `task_execution/` (declarativo, patrón `kg_metadata`); los 4 registries actuales se vuelven **vistas** del catálogo (o se eliminan).
3. `DomainTaskRegistry` derivado del catálogo real (0 fantasmas); `module_registry` sin solape.
4. Canónicos + alias: `documenting_knowledge`, `index_code`, `remove_ckg_node`, `sprint_status`.
5. Resolver `close_sprint` duplicado (un name, un handler); eliminar stubs legacy del dispatcher (502-560).

### Fase 2 — Contrato: envelope y compensación
1. Migrar los 6 legacy (cpg_updates 262 líneas → lógica a `CpgService`, events, spec_transform) al envelope.
2. Declarar `compensate` o idempotencia en actions de escritura; checklist valida `compensation[].type` ⊆ catálogo.

### Fase 3 — Tasks: mocks, stubs y nodos
1. Migrar `mocks.py` a `tests/` con registry de test inyectado (antes de retirar, para no romper E2E).
2. Decidir por edge: transforms/refinement/implement_task (lógica real o eliminación); prohibir `run_tests` falso positivo.
3. Migrar nodos a names canónicos (action + side_effects + compensation) + actualizar `workflow.schema.json`; migrar `test_workflow_e2e_bdd.py:216`.
4. Eliminar `TaskHandler` muerto; deprecar `query.py` (G8).

### Fase 4 — Retirar alias legacy
1. Eliminar alias deprecados; extender `memory-tools-naming-convention.md` al canal del engine.
2. Greps CI: 0 names legacy en actions/ y nodes/; 0 `delta_tracking.side_effects`; 0 mocks en `tasks/`; `task_registry ∩ module_registry = ∅`.

---

## 9. Anexo — El modelo mental en palabras simples (lectura rápida)

> Explicación pedagógica del análisis arquitectónico, para quien llega sin contexto. Complementa las secciones 1-5; si entendés este anexo, el resto del ADR es detalle y evidencia.

### 9.1 Qué es el workflow

El workflow es una **máquina que camina por estados**: una ruta con paradas (sub_states). En cada parada hace una cosa y después decide a qué parada sigue.

Tomemos la parada `TASK_REVIEW` del sprint (donde se revisa una spec):

```json
{
  "name": "TASK_REVIEW",
  "action": "spec_task",              ← 1. HACE ESTO MIENTRAS ESTÁ EN LA PARADA
  "side_effects": [
    {"type": "advance_task"},         ← 2. ESTO PASA CUANDO SALE DE LA PARADA
    {"type": "create_adr_from_spec"}
  ]
}
```

Hay **dos momentos** en los que la máquina actúa:
1. **Mientras está en la parada** → ejecuta `action` (una TASK).
2. **Cuando sale de la parada** (transición) → dispara `side_effects`.

### 9.2 Task vs side effect: la única diferencia es CUÁNDO

- **Task** (`spec_task`, `plan_sprint`, `run_tests`) = lo que se ejecuta *dentro* de la parada.
- **Side effect** (`advance_task`, `save_to_ckg`, `emit_event`) = lo que se dispara *al salir* de la parada.

Pero **ambos son lo mismo estructuralmente**: un **nombre** que apunta a una **función**. La única diferencia es *cuándo* la máquina las llama. El código del engine lo confirma — busca el nombre y lo ejecuta:

```python
# Para las tasks (orchestrator.py:663)
task_result = self._task_registry.execute(action, exec_ctx, input_data)

# Para los side effects (side_effects.py:110)
side_effect_engine.execute(...)
```

El engine no sabe qué hace `spec_task` ni `advance_task` — solo sabe "en esta parada, llamá a este nombre" y lo busca en una **agenda** (registry).

### 9.3 La máquina no hace el trabajo: delega

El engine (`core/engine`) es **puro**: solo sabe de estados, gates y transiciones. No sabe nada de conocimiento, sprints o código. Es como un jefe que grita nombres de empleados pero no sabe cómo hacen su trabajo.

El que sabe hacer el trabajo son los **features** (los departamentos):
- `cognitive_knowledge` → guarda conocimiento (CKG)
- `delta_tracking` → maneja sprints
- `code_analysis` → analiza código

Entre el jefe (engine) y los departamentos (features) hay un **traductor** — esa es la capa de actions (`features/main/task_execution/`):

```
ENGINE (el jefe)
   │  grita: "save_to_ckg"
   ▼
ACTIONS (el traductor)   ← features/main/task_execution/tasks/ + side_effects/
   │  traduce: "el jefe quiere guardar conocimiento"
   │  llama al departamento correcto: CkgApplicationService.save_knowledge()
   ▼
FEATURES (el departamento que hace el trabajo real)
```

**Ese traductor es la capa de tasks + side effects** — la respuesta a "cómo encaja": son la capa que une al engine con los features (anti-corruption layer, sección 2.1).

### 9.4 Por qué son el mismo concepto

La prueba más clara está en `design.node.json`: `save_to_ckg` aparece como **action** (task) en una parada (`{ "name": "CKG_SAVE", "action": "save_to_ckg" }`) y como **side_effect** en otros nodos (`{ "side_effects": [{"type": "save_to_ckg"}] }`).

**Es la misma operación de negocio** ("guardar conocimiento") llamada desde dos momentos distintos. Hoy, para colmo, esa operación tiene 3 nombres (`save_to_ckg`, `save_ckg`, `documenting_knowledge`) — como si el jefe la llamara de tres formas distintas según el día.

### 9.5 Qué sale mal HOY (los síntomas)

Si el traductor (actions) es una capa que nadie reconoció formalmente, pasan estas cosas:

**a) El jefe grita un nombre que no existe en la agenda.** El grafo dice `advance_task`, pero nadie registró `advance_task`:

```python
# side_effects.py:563 — el dispatcher no encuentra el handler
if etype not in self._handlers:
    return None   # ← silencioso: NO hace NADA y nadie se entera
```

Resultado: el sprint completa la revisión pero **nunca pasa a la siguiente tarea** — se queda en loop con la misma spec. Es un bug **activo hoy** (sección 4.3).

**b) Hay 4 agendas en vez de una.** El engine mira en `TaskRegistry` (59), `DomainTaskRegistry` (24 hardcodeados, 12 **fantasmas** — nombres que no existen en ningún lado), `module_registry` (13) y `SystemTaskRegistry` (vacío). Como un jefe con 4 teléfonos donde algunos empleados solo están en uno (sección 4.1).

**c) En la agenda hay funciones falsas.** `run_tests` devuelve `{"passed": True, "total": 0}` — dice que los tests pasan **sin correr ninguno**; `mocks.py` registra empleados falsos que "planifican" el sprint con respuestas inventadas. Si el traductor está lleno de mentiras, el workflow funciona "en falso" (sección 4.4).

**d) El traductor inventa respuestas.** `transforms.py` recibe datos y devuelve `{"transformed": True}` sin transformar nada. El jefe cree que se transformó el contexto; no se transformó nada (sección 4.4).

### 9.6 La propuesta en una frase

**Reconocer que tasks y side effects son UNA sola capa (workflow actions) y darle UNA agenda única, UN vocabulario y UN contrato:**

```python
WorkflowAction = {
  name: "documenting_knowledge",    # 1 operación de negocio = 1 nombre
  kind: "sub_state_action" | "side_effect",  # cuándo se llama
  scope: "internal" | "public",     # si es tool MCP o paso interno
  handler: ... → 1 llamada a 1 Application Service,  # el traductor real
  compensate: ...                    # cómo deshacer si falla
}
```

Con eso:
- El jefe (engine) tiene **una sola agenda** y deja de gritar fantasmas → se arreglan los 3 bugs (Fase 0).
- El traductor tiene **un vocabulario**: `documenting_knowledge` es lo mismo como task que como side effect.
- **Agenda y grafo que SIEMPRE coinciden** — lo garantiza el checklist de completitud (si el grafo declara un nombre sin implementación, falla el CI, no producción).
- El traductor **no miente**: sin mocks, sin stubs, sin "tests que pasan sin correr".

### 9.7 Los tres caminos de una acción (éxito / falla / no existe) y quién maneja el fallo

**Tasks (canal fail-loud, verificado en task_registry.py:44-73 + use_cases.py:410):**

| Camino | Qué pasa | Efecto en el workflow |
|---|---|---|
| No existe | `TaskResult.failure(TaskNotFoundError)` → `not ok` | **Submit FALLA** (raise `AutoAdvanceError`) → ErrorHandler → estrategia del sub_state |
| Falla (excepción) | `TaskResult.failure` (excepción envuelta) | **Submit FALLA** → idem |
| Éxito | `TaskResult.success` + trace `task_execute` | Continúa: valida output → side effects → auto-advance |

**Side effects (canal silencioso, verificado en side_effects.py:110-230):**

| Camino | Qué pasa | Efecto en el workflow |
|---|---|---|
| No existe (sin handler ni stub) | `None` → `ok=False` → compensa previos | **Submit SIGUE y el workflow avanza** — la acción no se ejecutó y nadie se entera |
| Falla | `ok=False` → compensa previos (si no optional) | **Submit SIGUE** |
| Éxito | `normalize()` a envelope + trace `side_effect_execute` | Se registra con ok=True |
| **Stub legacy** (update_cpg, save_ckg, save_to_ckg...) | Devuelve dict con `"updated": True` / `"saved": True` + `"result": "(stub — TASK-011 final)"` | **FALSO ÉXITO**: reporta que hizo algo sin hacerlo |

**Quién captura `on_failure` (error_handler.py + domain/errors.py):** las excepciones del pipeline (AutoAdvanceError, ValidationError, TransitionBlockedError...) las capturan StepOrchestrator (step_orchestrator.py:311) y SubmitOrchestrator (submit_orchestrator.py:326) → `OrchestrationService._handle_error` → `ErrorHandler.handle()`. El handler **clasifica** (ErrorType), **resuelve estrategia** (`sub_state.retry.on_error` por nodo → fallback `DEFAULT_STRATEGIES`) y ejecuta: `ON_FAILURE` salta a `sub_state.on_failure` (vía `AdvanceService.jump_to_sub_state`, fallback a transition por gate); `RETRY` re-submitea; `COMPENSATE` compensa; `ESCALATE` usa BlockResolver; `BLOCK_MESSAGE` devuelve el error al LLM.

**¿Resolución de conflictos de on_failure por nodo o por contexto?** AMBOS — verificado:
- **Por nodo**: cada sub_state declara `on_failure` (destino en fallo) + `retry.on_error` (mapa tipo/clase de error → estrategia). Ej. verify.node.json QA_RUN: `"on_failure": "QA_RUN"`, `"on_error": {"TaskTimeoutError": "retry_2x", "TaskFailedError": "on_failure"}`; implement WORK → `USER_GATE`; refine KNOWLEDGE_CAPTURE → `DIAGNOSE`; analyze GATHER → fallo vuelve a GATHER.
- **Por contexto**: `AdvanceService.jump_to_sub_state` (contexto = estado actual + grafo), `BlockResolver` para escalate/block (context-aware), eventos `on:{}` del workflow para rerouting por señal (`quality_gate_failed` → REFINE), y `_DECISION_GATE_OVERRIDES` (decisión → gate por sub_state).
- **El gap**: los side effects NO alimentan este machinery — el fallo de un side effect nunca llega al ErrorHandler (ver propuesta §5.5).

### 9.8 Los 4 registries — ¿mismo o diferente? ¿bug o diseño?

| Registry | Qué es | Propósito | ¿Bug o diseño? |
|---|---|---|---|
| `InMemoryTaskRegistry` | El SSOT real: `dict[name → fn]` + metadata (59 tasks) | Guardar y ejecutar | **Diseño** — el único almacén |
| `SystemTaskRegistry` | **Vista filtrada** (wrapper) sobre un base: solo `scope=public` | Exponer a CLI/MCP solo lo público (SPEC-005 #7: visibilidad = metadata.scope) | **Diseño** |
| `DomainTaskRegistry` | **Vista filtrada**: solo `scope=internal/domain` | Vista del engine para tasks internas | ⚠️ **Diseño de vista + BUG: conserva `_DOMAIN_TASKS` hardcodeado (24 names, 12 fantasmas)** — viola SPEC-005 #7 que SystemTaskRegistry sí cumplió |
| `InMemoryModuleRegistry` | **OTRA COSA**: catálogo de módulos (tools MCP + rutas REST) + container DI (`register_service`) | Exponer tools/rutas al MCP y resolver servicios | **Diseño** — pero **BUG de solape**: sus 13 tools duplican ~11 tasks |

**Respuesta corta:** el patrón de vistas (System/Domain como proyecciones sobre un SSOT) es diseño intencional y correcto (segregación de interfaces). Los bugs son 3: (a) `DomainTaskRegistry` con lista hardcodeada → 12 fantasmas; (b) `ModuleRegistry` solapando tools con tasks → dos fuentes para el mismo contrato público; (c) `bootstrap(separated=True)` puede construir vistas desde un registry **vacío** → vista desincronizada del SSOT.

---

## 10. Referencias

- ADR-WALKER-047 — B-7 (deuda de names) y B-1/B-3 (doble fuente de verdad): los 4 catálogos son la misma confusión en el canal del engine.
- ADR-WALKER-048 — patrón canonical de capa de aplicación: las actions delegan en Application Services; la duplicación del catálogo bloquea su F2 (rename canónico de tools).
- ADR-WALKER-051 — main = composition root + orchestration: la capa de actions es el tercer rol de main; la lógica cross-feature vive en `orchestration/`.
- ADR-WALKER-045 F5 — mover side effects a main quedó a medias por revert: origen de los huérfanos.
- AGENTS.md — patrón de tests de completitud para registries (base del checklist).
- Evidencia de código:
  - `core/engine/application/orchestrator.py:663,812` — `_task_registry.execute(action, ...)`: el engine despacha `.action` por nombre.
  - `core/engine/infrastructure/side_effects.py:110,468,563` — dispatch de side effects y None silencioso.
  - `core/engine/application/workflow_loader.py:37` — el grafo carga `ss.action` en el entry del sub_state.
  - `core/engine/workflow/nodes/*.json` — `.action` y `.side_effects[]` declarados en el mismo sub_state; bugs activos `advance_task` (sprint:612), `create_adr_from_spec` (sprint:641), `delete_ckg_nodes` (analyze:439, implement:128, refine:345).
  - `core/engine/infrastructure/domain_task_registry.py` — 12 names fantasma.
  - `features/main/task_execution/tasks/` — mocks.py:16-32, implementation.py (run_tests falso positivo), transforms.py/refinement.py (passthrough), planning.py:158 + knowledge.py:46 (close_sprint duplicado).
  - `features/delta_tracking/side_effects/` — handlers huérfanos (planning.py, create_adr.py).
- Auditoría 2026-08-14: 59 tasks / 19 side effects registrados; 4 catálogos; 3 bugs activos nodo↔registro; 6 handlers sin envelope; 0 compensaciones; 10 tasks stub; 12 fantasmas.
