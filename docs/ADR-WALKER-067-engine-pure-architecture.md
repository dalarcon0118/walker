# ADR-WALKER-067 — Motor de workflow puro: arquitectura objetivo y purificación

- **Status:** Activa (en ejecución)
- **Date:** 2026-08-28
- **Autor:** Walker agents (con el usuario)
- **Complementa:** ADR-WALKER-045 (composition root obligatorio), ADR-WALKER-062 (mantenibilidad monolith puertos DI CI), ADR-WALKER-065 (cpg port relocation), AGENTS.md (domain-first, métricas por fase)
- **Hipótesis:** `shared/core/engine` debe ser un motor de workflow **puro y agnóstico de propósito** (clean architecture / domain-first). Las dependencias hacia `infrastructure/` y `application/` desde el dominio, los puertos que filtran `dict`, y las god-classes rompen esa promesa. Invertir las 4 dependencias de dominio vía puertos locales (DIP) + purificar los puertos + desacoplar las god-classes recupera la frontera del motor con esfuerzo acotado y sin cambiar el transporte MCP ni el comportamiento observable.

---

## 1. Contexto (problema)

`shared/core/engine` se disenó domain-first, pero la realidad del código tiene fugas:

1. **Domain → Up violations (4 sitios):** el dominio importa infra/app directamente, rompiendo la regla "domain nunca importa infrastructure/application/features":
   - `domain/state.py` → `infrastructure.repositories.WorkflowStateRepository`
   - `domain/session.py` → `infrastructure.config.get`
   - `domain/entities.py` → `application.context_resolver.ContextResolver`
   - `domain/services/compensation_engine.py` → `infrastructure.side_effects.SideEffectEngine`
2. **Puertos que filtran `dict`:** `IWorkflowIO.get_sub_state(workflow: dict, ...)`, `IPresenter` recibe `node: dict`, `ISprintService` recibe `workflow_state: dict`, etc. El dominio define tipos ricos (`WorkflowGraph`, `WorkflowState`) pero los puertos aceptan dicts crudos → el dominio re-serializa y pierde tipos.
3. **God-classes:** `OrchestrationService` (~875 ln) y `use_cases.py` (~1347 ln) mezclan orquestación, validación de gates, resolución de contexto y side-effects.
4. **Módulos TEST-ONLY no cableados:** `domain/services/compensation_engine.py`, `domain/state.py` (en parte), `infrastructure/side_effect_outbox.py` existen pero no se usan en producción; su cableado o borrado está pendiente.
5. **`engine.py` es un cajón de sastre:** mezcla API pública (submit/transition/signal), servicios de sesión, y lógica de compatibilidad.

Trabajo previo ya completado (este mismo ADR lo asume como base): eliminación de código legacy muerto (`resolve.py`, `legacy/`, `catalog.py`, `migrate.py`, `sub_state_executor.py`, `interface/`), repunteo de `context/` a `shared.common.context`, y purificación de `check_blocked_state` (única función de `engine.py` con callers reales).

---

## 2. Decisión — arquitectura objetivo

Capas (de acuerdo a `docs/domain-first.md` y ADR-045/062):

```
features/ (MCP/CLI)                ── adaptadores, consumen puertos
   │
shared/core/engine/
   ├─ domain/        SSOT del modelo + reglas. Define PUERTOS.
   │                 NUNCA importa infrastructure/, application/, features/.
   │                 Resuelve adaptadores vía domain.ports_registry (local, puro).
   ├─ application/   use_cases (1 responsabilidad c/u), context_resolver, orchestrator.
   ├─ infrastructure/ adaptadores: repositories, config, side_effects, trace_log.
   └─ engine.py      FACADE FINO: submit/transition/signal + re-export de lo público.
shared/libs/         module_system (registry de puertos de features), cognitive_graph, context.
```

**Principio de inversión de dependencias (DIP) para el dominio:** el dominio define el puerto (interfaz) y lo resuelve en runtime desde `domain/ports_registry` (un diccionario en memoria, sin importar infra). El composition root (`bootstrap.py` / `app/composition/registry.py`) registra el adaptador concreto. Así `domain/state.py` NO importa `infrastructure/…`, sino que hace `resolve_port(IStateStore)`.

**Frontera del motor:** `engine.py` expone solo `submit/transition/signal/status` + constructores de entidades. La orquestación pesada vive en `application/`; el dominio no sabe de transporte.

---

## 2.1 Principio rector: Empaquetable (siempre)

El paquete distribuible es **`shared`**, no `engine`. Esto es un principio rector (no una
fase): cada cambio en `shared/core/engine` y sus hermanos se juzga contra él. Se codifica
como **INV-015** en `project.md`.

**Topología (quién sabe qué):**
- `shared.core.engine` (y `shared.libs.*`, `shared.common.*`) son **componentes internos**:
  autónomos, host-agnósticos, y **no saben quién los consume**.
- `engine` **NO re-exporta componentes hermanos** (p.ej. `shared.libs.module_system`),
  y **NO re-exporta clases concretas**: la superficie de `engine` es su API pública
  (nombres de su propio dominio), no la de sus hermanos.
- **`shared` se AUTOREGISTRA en el contenedor DI único del proceso** (estilo NestJS:
  cada módulo se registra a sí mismo; el DI lo localiza e inyecta). El contenedor es
  `module_registry` (en `shared.libs.module_system.registry`); `shared_registry` es un
  alias de legibilidad hacia él. **Al importarse `shared`**, registra el módulo
  `"shared.engine"` **por id** (con sus contratos en `metadata`) + adaptadores por
  defecto host-agnósticos **por contrato** (`register_port`). Por eso `shared` "sí sabe"
  de sus componentes: es quien se registra para que otros los resuelvan por DI.
- **El composition root NO registra `shared`.** `app/composition/container.py` usa el
  MISMO `module_registry` (singleton) que `shared` autopobló; el composition root solo
  registra los providers dependientes del host (IProjectContext, IConflictResolver,
  ISprintService, IPromptCollector, IConfig con rutas reales). Consecuencia: "llevarse
  `shared`" a otra app funciona — basta con `import shared` para que su módulo quede
  disponible por id+contrato; la app huésped no necesita cablear nada de `shared`.
- Los consumidores NO hacen `from shared.core.engine import X` para acoplarse; hacen
  `module_registry.get_module("shared.engine")` / `resolve_port(contrato)` /
  `get_service("shared.engine")`.

**Definición.** `engine` solo conoce responsabilidades de *motor de workflow*: modelo de
dominio (`WorkflowState`/`WorkflowGraph`), reglas de avance (gates/edges), orquestación de
submit/transition/signal, y la mecánica que el host debe satisfacer. Todo lo demás es
responsabilidad del host y se entrega por inyección o como extra. Ver §2.3: el engine NO
conoce conceptos de negocio (project, conflictos, sprints, prompts, trabajo sin capturar,
springs informales); el host valida fuera.

**Naturaleza (sin ambigüedad):** `engine` es una **máquina de estados BPMN genérica**.
NO conoce `sprint`/`spring`, `spec`, `delta`, `cpg`/`ckg`/`trace` ni `project`. Los VOs
`SprintId`/`ExecutionContext` de `shared.common.shared_domain` son vocabulario del walker
y NO deben migrar al engine (documentado también en `shared/core/engine/__init__.py`);
su hogar es el dominio del walker (features/delta_tracking).

**Regla de oro.** *"Un componente interno no sabe quién lo usa; `shared` sí, porque es
quien lo re-exporta. Lo que depende del host es un puerto o un extra, nunca un hecho
asumido."*

**Responsabilidades que NO son del engine** (y por tanto no van en su `__init__`):
- Re-exportar el framework de módulos/registry → `ModuleDefinition`/`ToolDefinition`/
  `RestRoute`/`IModuleRegistry` de `shared.libs.module_system`: viven en `shared/__init__.py`
  (facade), no en `engine/__init__.py` (G1, ya corregido: `engine/__init__.py` ya no los
  re-exporta).
- Resolución de rutas del repo → `shared.libs.path_resolver` (`get_walker_root`): el host
  inyecta rutas vía `IConfig`/`IProjectContext` (ya existen como puertos) (G2).
- Definición de tools MCP / rutas REST → capa de transporte del host (fuera del engine).
- Integración directa con `cognitive_graph`/`delta_tracking` como core → extra opcional.
- Defaults de config atados al root del monorepo → el host provee `IConfig`.
- **Conceptos de negocio del proyecto que el workflow ataca** (ver §2.3): project
  (`IProjectContext`), conflictos de specs (`IConflictResolver`), sprints/springs
  (`ISprintService`), recolección/build de contexto de prompts (`IPromptCollector`),
  trabajo sin capturar, springs informales. El engine NO los conoce — ni como puerto de
  su constructor ni como lógica interna. El host los aplica EXTERNAMENTE (host valida fuera).

**Corolarios (checklist de segundo orden por cambio):**
- [ ] `engine/__init__.py` + `__all__` **no re-exportan** nada de `shared.libs.*` ni `features/*`; esos nombres se re-exportan en `shared/__init__.py`. — anti-patrón: componente interno acoplado a su hermano (G1).
- [ ] `engine` (y hermanos) no asumen el root del repo (sin `get_walker_root`/`CWD` en infraestructura core). — anti-patrón: host-hardcoding (G2).
- [ ] Hay empaquetado explícito del paquete `shared`: `pyproject.toml` propio, `py.typed`, `dependencies` declaradas, `extras` para integraciones. — anti-patrón: módulo no distribuible (G3).
- [ ] Toda dependencia host-específica entra por un **puerto** (`domain/ports.py`) o es **extra**. — anti-patrón: upward dependency / hidden dependency.
- [ ] El engine NO conoce conceptos de negocio: su constructor/`EngineBundle` solo recibe mecánica (repos/IO/tasks/side-effects/events); las reglas de negocio se aplican fuera del engine (host valida fuera, §2.3). — anti-patrón: engine que sabe de negocio (G4).
- [ ] `WorkflowState` lleva `schema_version` y migración (compatibilidad entre versiones del paquete). — anti-patrón: breaking state across versions (F6).

**Relación con el resto del ADR.** F1 (DIP) es la mecánica de este principio para el
dominio; F2–F6 (puertos tipados, inmutabilidad, versionado, saga/outbox, eventos tipados,
0 `except: pass`) son sus consecuencias para un paquete robusto. G1–G3 (§2.2) son las
violaciones actuales de este principio. INV-015 se apoya en INV-011 (`Result[T,E]`),
INV-012 (pureza) e INV-045 (composition root).

---

## 2.2 Comparativa — estado actual vs. listo para producción

El motor hoy **funciona** (MCP self-test 31 tools, CLI routes OK, smokes verdes) pero tiene
deudas que lo harían frágil en producción. La tabla contrasta cada dimensión; la columna
"Gap" cita evidencia real del repo. Fase 1 (DIP) ya está hecha; el resto es Fase 2–6.

| # | Dimensión | Estado actual (evidencia) | Objetivo producción | Gap |
|---|-----------|---------------------------|----------------------|-----|
| A | **Capas / dependencias** | `domain/` NO importa `infrastructure`/`application` (grep 0 tras F1 ✅). 4 puertos resueltos vía `domain.ports_registry`. | Mantener frontera domain-first; un test CI que falle si `domain/` importa hacia arriba. | ✅ F1 hecho; falta test CI (FS-1). |
| B | **Superficie del motor (`engine.py`)** | Fachada fina (206 ln): `load_workflow`/`load_state`/`save_state`/`check_blocked_state` + 2 privadas. `check_blocked_state` es la única pública con callers. | Facade = solo `submit/transition/signal/status` + constructores de entidades. | 🟢 buen estado; alinear nombres en F3. |
| C | **Orquestación (god-class)** | `application/orchestrator.py` = **875 ln**, mezcla submit/transition/signal/blocked/conflict. | `SubmitService`/`TransitionService`/`SignalService` SRP (<250 ln c/u). | 🔴 F3. |
| D | **Casos de uso (god-class)** | `application/use_cases.py` = **1347 ln**, múltiples responsabilidades. | `SubmitUseCase`/`TransitionUseCase`/`CheckBlockedStateUseCase`/`ResolveConflictUseCase` SRP. | 🔴 F3. |
| E | **Puertos / tipado** | **TIPADOS (F2/E)**: `IWorkflowIO.get_sub_state(workflow: WorkflowGraph)`, `IPresenter.show_node_header(node: WorkflowNode)`, `show_transitions(workflow: WorkflowGraph)`, `show_flow_progress(state: WorkflowState)` — sin callers reales; firmas corregidas. | Puertos aceptan tipos ricos; `dict` solo en DTOs de borde. | 🟡 F2 (hecho). |
| F | **Estado: mutabilidad** | `workflow_api.py:187-192` reasigna `state.current_node`/`state.current_sub_state` **in-place** antes de guardar. | `submit/transition` retornan **nueva** `WorkflowState` (inmutabilidad); el caller decide persistir. | 🔴 F2. |
| G | **Estado: versionado / unit-of-work** | **CAS implementado (F6/G)**: `WorkflowState.revision` + `save` con optimistic concurrency (`StateConflictError` en lost update). | `save` falla por revision desactualizada; único escritor por `chain_dir`. | 🟡 F6 (hecho; reload+merge pendiente). |
| H | **Side-effects / Saga / Outbox** | **Saga + Outbox en `SideEffectSaga` (F4/H)**: `run(ctx, effects, outbox=None)` enqueue/mark_completed/failed/compensated. | Saga con outbox atómico y compensación en orden inverso. | 🟡 F4 (hecho; cableado del outbox al submit pendiente). |
| I | **Eventos / trazabilidad** | **Contrato tipado + tracing PLUGGABLE (F5/I)**: `WorkflowEvent` dataclass + `ITracer.trace_event(event)`; el motor usa el tracer INYECTADO (NoopTracer default); sin TraceLogger() en el motor. | Evento tipado; `trace()` queda de compat. | 🟡 F5 (contrato hecho; migración de emit-sites pendiente). |
| J | **Observabilidad / health** | **`IEngineMetrics` + `EngineMetrics` (F5/J)**: p99 de submit/transition, state size; `engine.health_metrics()`. | p99 vía puerto; host puede reemplazar el default. | 🟢 F5 (hecho). |
| K | **Errores silenciosos** | 0 `except: pass` reales en `shared/core/engine` (solo docstring). | 0 silenciosos. | 🟢 F6 (hecho). |
| L | **Transporte MCP (intocable)** | JSON-RPC 2.0 `_handle_request` estable, 31 tools. | No tocar (invariante de transporte). | 🟢 intacto. |

### Scorecard de producción

| Dimensión | Estado | Fase que lo cierra |
|-----------|--------|--------------------|
| A Capas/dependencias | ✅ hecho | F1 (done) |
| B Superficie motor | 🟢 ok | F3 (alineación) |
| C Orquestación SRP | 🟡 | F3 (orchestrator 875→643: PromptService + EventDispatchService) |
| D Casos de uso SRP | 🟡 | F3 (use_cases.py → paquete SRP) |
| E Puertos tipados | 🟡 | F2 (firmas tipadas) |
| F Inmutabilidad estado | 🔴 | F2 |
| G Versionado/unit-of-work | 🟡 | F6 (CAS) |
| H Saga/Outbox cableado | 🟢 | F4 (port ISideEffectOutbox + SideEffectOutbox sqlite SPEC-009 + InMemory; cableado al submit) |
| I Eventos tipados | 🟢 | F5 (emit-sites → trace_event; tracer pluggable; query_manager sin TraceLogger) |
| J Observabilidad | 🟢 | F5 (IEngineMetrics) |
| K Errores silenciosos | 🟢 | F6 (0 reales) |
| L Transporte MCP | 🟢 | — |

### Riesgo de producción si se envía "as-is" (sin F2–F6)
- **Lost update (G):** 2 submits concurrentes CLI+MCP al mismo `chain_dir` → el último gana, se pierde progreso (sin `version`).
- **Indeterminismo por mutabilidad (F):** `state` se muta in-place → difícil reproducir/testear; el mismo objeto se comparte entre capas.
- **Datos malformados en puertos (E):** un caller puede pasar un `dict` sin `node_id` a `IPresenter`/`ISprintService` → `AttributeError` en runtime.
- **Fallos silenciosos (K):** `except: pass` oculta errores de gate/efecto → el usuario cree que el submit fue OK.
- **Efectos parciales sin compensación (H):** si un side-effect falla tras otro exitoso, no hay compensación en producción → estado inconsistente (CKG/Delta vs estado del sprint).
- **Trazabilidad frágil (I):** `data: dict` abierto rompe el replay/auditoría si cambia la forma del payload.

---

## 2.3 Motor anémico — el engine no conoce conceptos de negocio (host valida fuera)

**Principio (refuerzo de §2.1).** La responsabilidad del engine es UNA: **cargar la
config del workflow y ejecutarla**. Todo concepto de negocio del proyecto que el
workflow ataca — project, conflictos de specs, sprints/springs, recolección y build de
placeholders de prompts, trabajo sin capturar, springs informales — es INCUMBENCIA DEL
HOST y no vive en el engine, ni como puerto de su constructor ni como lógica interna.

**Contrato de entrada (VO).** El host instancia el engine con un `EngineBundle`
(dataclass en `shared/core/engine/application/bundle.py`) inyectado vía `workflow_api`.
Sus campos son SOLO de mecánica del workflow:

- Requeridos: `workflow_repo`, `state_repo`, `workflow_io`, `task_registry`,
  `side_effect_engine`.
- Opcionales (mecánica): `context_registry`, `contract_validator`, `action_catalog`,
  `event_dispatcher`, `event_log`, `tracer`.

NO forman parte del contrato (ni del bundle ni del constructor): `project_context`,
`conflict_resolver`, `sprint_service`, `prompt_collector`.

**Regla de oro: el host valida fuera.** Las reglas de negocio se aplican FUERA del
engine, antes/después de `submit`/`transition`, y el host decide si bloquear:

- **Trabajo sin capturar** (al cerrar SPRINT): el HOST llama a
  `features/main/workflow/uncaptured_work.py::detect_uncaptured_work` y decide si
  bloquear. El engine NO tiene `_check_uncaptured_work_gate` ni puerto
  `IUncapturedWork`.
- **Spring informal** (delta): el HOST lo computa; el engine no tiene
  `_is_informal_sprint` (borrado: código muerto + path_resolver) ni devuelve
  `informal` en status.
- **Prompts:** el engine RENDERIZA (PromptManager/`resolve_prompt`, mecánica interna)
  pero NO recolecta ni builda contexto de negocio. `PromptContextBuilder` vive en
  `features/main/prompts/`; el contexto llega por el caller (`extra_data`/`context`),
  jamás por consulta a delta dentro del engine.
- **Conflictos de specs** (edit/merge en delta): el host. `WorkflowConflictResolver`
  quedó reducido a mecánica `BlockResolver` (skip/force/escalate/compensate); ya no
  recibe el puerto `IConflictResolver` ni delega estrategias de specs.

**Evidencia (2026-08-28):**
- `OrchestrationService.__init__` y `EngineBundle` SIN `project_context`,
  `conflict_resolver`, `sprint_service`, `prompt_collector`.
- `shared/core/engine/application/prompt_context_builder.py` y `uncaptured_work.py`
  ELIMINADOS del engine (movidos a `features/main/`).
- `submit_orchestrator.py` sin gate de uncaptured_work; `_inject_spec_predicates` y el
  conteo de TASK_REVIEW ya no consultan spring (fallbacks documentados / leen
  `state.context`); `context_resolver`/`query_manager`/`composer`/`workflow_api` sin
  spring/prompt.
- Puertos `IProjectContext`/`IConflictResolver`/`ISprintService`/`IPromptCollector`
  siguen existiendo en `domain/ports.py` pero el engine NO los consume: son contratos
  que el HOST usa para su propia validación externa.

**Checklist por cambio:**
- [ ] ¿El engine conoce un concepto de negocio (spring, proyecto, prompt-data, trabajo
  sin capturar, informal)? → NO debe: el host lo aplica externamente. — anti-patrón:
  engine que sabe de negocio.
- [ ] El constructor/bundle solo recibe mecánica (repos/IO/tasks/side-effects/events).
  — anti-patrón: puertos de negocio en el constructor del engine.
- [ ] Si un gate depende de datos externos, el engine los recibe como DATA
  (`state.context` / `fields` / `extra_data`), jamás consulta spring/proyecto/prompt.
  — anti-patrón: consulta a delta/proyecto dentro del engine.

---

## 2.4 Composition root declarativo (NestJS) — sin glue manual ni monkeypatch

**Problema (corregido).** `app/composition/container.py` + `app/composition/registry.py`
eran glue imperativo: `register_all` cableaba a mano el `OrchestrationService` con repos
explícitos, registraba servicios uno por uno y, peor, `container.py` **monkeypatcheaba**
`orch._action_catalog = ...` / `se_engine._action_catalog = ...` (mutación de atributos
privados cruzando fronteras de módulo). Eso rompía la modularidad/empaquetamiento de
INV-015: la composición no era DI, era ensamblaje manual.

**Solución (NestJS-like, ADR-066).** El composition root es UN MÓDULO declarativo
(`app/module.py` con `MODULE = ModuleManifest(name="app", register=_register_host)`).
Cada provider del host se declara como **factory lazy que inyecta por id+contrato desde
el ModuleRegistry único del proceso**:

- `workflow_repo` / `state_repo` → eager (override de los defaults host-agnósticos que
  `shared` registró; último registro gana). Son la base del adapter `workflow_store`.
- `workflow_store` (`WorkflowStoreAdapter`) → provider UNIFICADO (grafo+estado, §7.2).
- `task_registry`, `side_effect_engine`, `context_registry`, `tracer` → lazy (host).
- `action_catalog` → lazy: inyecta `task_registry` + `side_effect_engine` y construye el
  catálogo (`actions`, puerto único de tasks+side-effects; §7.2).
- `engine` (`OrchestrationService`) → lazy: resuelve SOLO `workflow_store` + `actions`
  (+ mecánica opcional) — bundle unificado, sin repos/registries concretos.
- `workflow` (facade) → lazy: envuelve `engine`.

`register_all` quedó **delgado**: libs services → módulos MCP → manifests (cada módulo se
autoregistra; `app.module` registra tasks/side-effects y providers) → providers de
contexto → resolver `engine`/`workflow` por id+contrato. `container._compose` solo
importa módulos y resuelve. Se eliminó el monkeypatch.

**Dónde vive la instancia del engine (respuesta).** La instancia del engine se
construye en **`main`** — la función de composición del entry point de la app
(`register_all` en `app/composition/registry.py`, llamada por `AppContainer.build()`),
**NO como provider en `app/module.py`**. `main` la instancia EXPLÍCITAMENTE con todos
sus parámetros desde los providers del registry (patrón servidor):

```python
# app/composition/registry.py — register_all() (main de composición)
engine = OrchestrationService(
    workflow_store=module_registry.get_service("workflow_store"),
    actions=module_registry.get_service("action_catalog"),
    workflow_io=module_registry.resolve_port(IWorkflowIO),
    tracer=module_registry.get_service("tracer"),
    context_registry=module_registry.get_service("context_registry"),
)
module_registry.register_service("engine", engine)
```

- `app/module.py` declara SOLO los **ingredientes** (workflow_store, actions,
  task_registry, side_effect_engine, tracer, context_registry) — nunca el engine.
- `shared` NO instancia: solo exporta la **clase** `Engine` + defaults host-agnósticos.
- `container._compose` NO construye a mano: llama a `main` (register_all) y resuelve.

**Consecuencia INV-015:** `shared` sigue siendo empaquetable (se autoregistra al
importarse); el host solo declara SU módulo; el contenedor no ensambla nada a mano.

**Checklist por cambio:**
- [ ] Un provider del host se declara como factory en `app/module.py` (DI por id+contrato),
  jamás como `register_service` suelto en el container. — anti-patrón: glue manual.
- [ ] La instancia del engine se construye en `main` (register_all, entry point de
  composición) con parámetros explícitos desde los providers — NO como provider en el
  módulo ni a mano en el container. — anti-patrón: instanciación dispersa / engine en module.
- [ ] Nada de mutar atributos privados de otro módulo desde el container
  (`orch._x = ...`); el wiring se hace por constructor en la factory. — anti-patrón:
  monkeypatch / hidden coupling.
- [ ] `shared` NO se registra en el composition root; solo se importa (autoregistro).
  — anti-patrón: composition root que re-cablea shared.

---

## 2.5 `shared` es un SharedModule (Angular/Nest) — dónde viven los use cases

**Naturaleza.** `shared` es un **SharedModule** (análogo a Angular `SharedModule` /
shared providers de Nest): capa **reutilizable, host-agnóstica, sin estado de negocio**.
Sus componentes internos (`core/engine`, `libs/cognitive_graph`, `libs/module_system`,
`common/*`) son **genéricos y no saben quién los usa** (INV-015).

**¿Use cases / application services son necesarios para `shared`?** NO para `shared` como
módulo, y NO para sus librerías puras. Se distinguen DOS tipos:

1. **Use cases MECÁNICOS (contrato del subsistema):** SOLO los necesita un subsistema
   autocontenido dentro de shared — el engine (`application/use_cases.py`: SubmitDataUseCase,
   TransitionUseCase, …). Son operaciones de la MÁQUINA BPMN (submit/transition/status), no
   negocio. Sin ellos el engine no es un engine, es solo un dominio. Las libs puras
   (`cognitive_graph`, `module_system`, `common`) NO tienen use cases.
2. **Use cases de NEGOCIO:** NUNCA viven en `shared` — viven en el HOST
   (`features/` + `tasks/actions`, side-effects), que CONSUME los componentes de shared.

**Regla:** la capa `application` (use cases) existe a nivel del SUBSISTEMA (engine), no en
el dominio anémico ni en las libs. Un componente de `shared` NO agrega use cases de negocio;
los mecánicos solo si es un subsistema autocontenido.

**Checklist por cambio:**
- [ ] Un componente de `shared` NO contiene use cases de NEGOCIO (viven en features/tasks).
  — anti-patrón: negocio en shared.
- [ ] Una lib pura (`libs/*`, `common/*`) NO tiene application services — solo ports e
  implementaciones. — anti-patrón: lib con orquestación de negocio.
- [ ] El engine (subsistema autocontenido) SÍ tiene su capa `application` con use cases
  MECÁNICOS (su contrato). — si se quitaran, el engine no ejecutaría.

---

## 3. Plan de purificación por fases

### Fase 1 — Invertir las 4 dependencias de dominio (DIP)  ✅ HECHA
Cambiar los 4 sitios a `resolve_port(<Puerto>)` + registrar adaptadores en `bootstrap.register_engine_domain_ports()`:
- `IStateStore` ← `WorkflowStateRepository` (infra) — `domain/state.py`
- `IConfig` ← `ConfigAdapter` (infra) — `domain/session.py`
- `IContextValidator` ← `ContextResolver` (app) — `domain/entities.py`
- `ISideEffectEngine` ← `SideEffectEngine` (infra) — `domain/services/compensation_engine.py`

Validación: `python -m compileall`, smokes F1/F2/legacy_removal/dead_funcs, `mcp --self-test`, `bin/walker routes`, grep de `domain/**` sin imports a `infrastructure`/`application`.

### Fase 2 — Purificar los puertos (dict → tipos de dominio)
- `IWorkflowIO.get_sub_state(workflow: WorkflowGraph, ...)` y `IPresenter`/`ISprintService` aceptan `WorkflowGraph`/`WorkflowState` (no `dict`).
- `IWorkflowLoader.load_state(chain_dir) -> WorkflowState` ya correcto; migrar callers que pasan `dict`.
- Inmutabilidad: `submit/transition` retornan una **nueva** `WorkflowState` (jamás mutan la entrada); `engine.py` deja de re-asignar `state.sub_state_data` in-place.

### Fase 3 — Desacoplar las god-classes
- Partir `OrchestrationService` en: `SubmitService`, `TransitionService`, `SignalService` (SRP), cada uno recibe sus puertos por constructor.
- Partir `use_cases.py` (~1347 ln) en casos de uso de 1 responsabilidad (`SubmitUseCase`, `TransitionUseCase`, `BlockedStateUseCase`, `ConflictResolveUseCase`).

### Fase 4 — Cablear o borrar saga / compensation / outbox
- Decidir: `compensation_engine.py` y `side_effect_outbox.py` se cablean al flujo real de submit (Saga con outbox atómico) o se borran. Hoy son TEST-ONLY.
- Si se cablean: `ISideEffectEngine.execute(ctx, effects, outbox)` con outbox atómico (M-01).

### Fase 5 — Eventos tipados + observabilidad
- `ITracer.trace(event_type, data)` recibe eventos como **contrato tipado** (dataclass), no `dict` abierto.
- Métricas de health del motor expuestas vía el mismo puerto (p99 de submit, tamaño de state).

### Fase 6 — Estado versionado + unit-of-work + sin `except: pass`
- `WorkflowState.version` se usa para unit-of-work (optimistic concurrency en `save_state`).
- Eliminar `except Exception: pass` silenciosos (al menos `logger.warning` + métrica).

---

## 4. `failure_scenarios`

**FS-1 — `resolve_port` devuelve `None` en un contexto sin bootstrap**
- **fallo:** `domain/state.py` o `domain/session.py` lanzan `RuntimeError` en un test/import que no levantó el app.
- **causa raíz:** el registro de puertos solo se puebla en `bootstrap.register_engine_domain_ports()`, que corre al importar `engine/__init__.py`; si algo importa el dominio saltando el paquete, el registro está vacío.
- **mitigación:** `register_engine_domain_ports()` se ejecuta en la importación de `shared/core/engine/bootstrap.py` (importado por `engine/__init__.py`), cubriendo tests/CLI/MCP; `IContextValidator`/`ISideEffectEngine` degradan a no-op si `None` (no lanzan).
- **detección:** smoke que importa `WorkflowGraph(...).validate_invariants()` y `SessionRecord().session_dir` sin app explícito; grep CI de `domain/**` importando `infrastructure`/`application`.

**FS-2 — Registro doble de `ISideEffectEngine` con instancia distinta a la de producción**
- **fallo:** la compensación corre con un `SideEffectEngine()` vacío (sin task_registry/tracer) en vez del cableado por el composition root final.
- **causa raíz:** `register_engine_domain_ports()` registra un default y el composition root final (`app/composition/registry.py`) no lo re-registra.
- **mitigación:** el composition root final re-registra `ISideEffectEngine` con el `SideEffectEngine` totalmente cableado (regla: `register_port` reemplaza); tests de compensación usan el del composition root.
- **detección:** test que verifica `resolve_port(ISideEffectEngine)._handlers` poblado tras `compose_app()`/`register_all`.

**FS-3 — Fase 2 rompe callers que pasan `dict` a los puertos**
- **fallo:** `IPresenter`/`ISprintService` reciben `dict` y la firma cambia a `WorkflowGraph` → `AttributeError` en runtime.
- **causa raíz:** migración de puertos sin actualizar todos los adaptadores CLI/MCP.
- **mitigación:** migración en un solo PR con grep de todos los callers de cada puerto; test de contrato de puertos (los adaptadores implementan la firma tipada).
- **detección:** test de arquitectura que instancia cada adaptador y llama al método del puerto con el tipo correcto; `mypy`/pyright en CI.

**FS-4 — Fase 3 parte `OrchestrationService` y rompe el flujo de submit**
- **fallo:** submit/transition dejan de disparar side-effects o validaciones por servicio no cableado.
- **causa raíz:** split de god-class sin preservar el orden (validate → side-effects → trace → state).
- **mitigación:** respetar el orden TEA (Transaction→Event→Audit, ADR-033) en cada servicio nuevo; tests E2E de submit por nodo.
- **detección:** tests E2E `test_cli_walker_bdd.py` + `test_agent_e2e_bdd.py` en verde antes de borrar la clase vieja.

**FS-5 — Fase 6 (unit-of-work) rechaza submits legítimos por versión**
- **fallo:** `save_state` falla por `version` desactualizado en concurrencia CLI+MCP.
- **causa raíz:** optimistic concurrency sin retry/merge.
- **mitigación:** reload + merge por clave de sub-estado antes de reintentar; el motor es el único escritor por `chain_dir`.
- **detección:** test de concurrencia (2 submits simultáneos al mismo `chain_dir`).

---

## 5. Checklist de segundo orden (AGENTS.md)

- ¿Qué módulos importan esto? → `domain/state.py`, `domain/session.py`, `domain/entities.py`, `domain/services/compensation_engine.py`, `bootstrap.py`, `infrastructure/repositories.py`, `infrastructure/config.py`, `infrastructure/side_effects.py`, `application/context_resolver.py`.
- ¿Qué side effects dispara? → registro de puertos en memoria (idempotente); persistencia de `WorkflowState` (vía `IStateStore`); logs de compensación.
- ¿Qué eventos de dominio publica? → ninguno nuevo; reusa `ITracer` existente.
- ¿Qué tests dependen? → `tests/test_bpmn_gaps_p1_bdd.py` (`CompensationEngine.execute`), smokes F1/F2/legacy_removal/dead_funcs, `mcp --self-test`, `bin/walker routes`.
- ¿Qué cambia en MCP/CLI/CKG/Delta/Trace? → nada en la API pública; CKG/Delta no se tocan en Fase 1. El transporte JSON-RPC 2.0 (`_handle_request`) es intocable.

---

## 6. Métricas por fase (M-01..M-06)

**Fase 1 (DIP)**
- [ ] M-03 Modelo de dominio — el dominio define el puerto y no importa infra/app: `grep -rn "infrastructure\|application" shared/core/engine/domain/ | grep import` debe ser 0 (anti-patrón a evitar: Hidden Dependency / Upward Dependency).
- [ ] M-06 Extensibilidad/configuración — `register_engine_domain_ports()` es el único punto de cableado de adaptadores de dominio (anti-patrón a evitar: Scattered Wiring / Not Invented Here).
- [ ] M-01 Integridad de escritura — `IStateStore.save` es atómico (rename) y `load` retorna default si no existe (anti-patrón a evitar: Partial Write).

**Fase 2 (puertos tipados)**
- [ ] M-03 Modelo de dominio — `IWorkflowIO.get_sub_state`/`IPresenter`/`ISprintService` reciben `WorkflowGraph`/`WorkflowState`, no `dict` (anti-patrón a evitar: Anemic Domain Model / Primitive Obsession).
- [ ] M-05 Concurrencia/validación — `submit/transition` retornan nueva `WorkflowState` inmutable (anti-patrón a evitar: Mutable Shared State).
- [ ] M-02 Variabilidad de estrategias — precedentes de gates en un solo SSOT (anti-patrón a evitar: Shotgun Surgery).

**Fase 3 (god-classes)**
- [ ] M-05 Concurrencia/validación — `OrchestrationService` dividido en servicios SRP < 250 ln cada uno (anti-patrón a evitar: God Class).
- [ ] M-03 Modelo de dominio — cada use case 1 responsabilidad (anti-patrón a evitar: Feature Envy).
- [ ] M-06 Extensibilidad — nuevos nodos vía puerto, no editando `use_cases.py` (anti-patrón a evitar: Accidental Complexity).

**Fase 4 (saga/outbox)**
- [ ] M-01 Integridad de escritura — side-effect + outbox atómico todo-o-nada (anti-patrón a evitar: Transaction Script suelto).
- [ ] M-04 Eventos/acoplamiento — compensación desacoplada por evento (anti-patrón a evitar: Distributed Big Ball of Mud).
- [ ] M-03 Modelo de dominio — `WorkflowState` registra `node_executions` de compensación (anti-patrón a evitar: Missing Audit Trail).

**Fase 5 (eventos tipados)**
- [ ] M-04 Eventos/acoplamiento — `ITracer.trace` recibe dataclass de evento tipado (anti-patrón a evitar: Stringly-Typed Event).
- [ ] M-06 Extensibilidad — health metrics del motor vía puerto (anti-patrón a evitar: Hidden Telemetry).
- [ ] M-02 Variabilidad — estrategia de trazado por config (anti-patrón a evitar: Hardcoded Behavior).

**Fase 6 (versionado/uow)**
- [ ] M-01 Integridad de escritura — `save_state` usa `version` (optimistic concurrency) + reload/merge (anti-patrón a evitar: Lost Update).
- [ ] M-05 Concurrencia — único escritor por `chain_dir` (anti-patrón a evitar: Race Condition).
- [ ] M-03 Modelo de dominio — 0 `except: pass` silenciosos en el motor (anti-patrón a evitar: Swallowed Error).

---

## 7. Análisis técnico — revisión de dominio y composición (2026-08-28)

Documenta el análisis realizado (dominio + composición), los hallazgos, las
decisiones y la evidencia. Es el "por qué" de las secciones §2.3 y §2.4.

### 7.1 Revisión del dominio (`shared/core/engine/domain`)

Contraste contra conceptos de un workflow engine de referencia (BPMN/Camunda,
Temporal, XState, Step Functions). Hallazgos por severidad:

| # | Hallazgo | Severidad | Decisión | Evidencia |
|---|----------|-----------|----------|-----------|
| D1 | `WorkflowState.to_dict/from_dict` NO serializaba 9 campos (blocking P0, señales P1, `process_data` P2, `conflicts`) → **pérdida de datos en persistencia** | 🔴 ALTA | Completar round-trip (persistir los 9) | verificado en runtime: 0 perdidos tras fix |
| D2 | `SessionStore` (file-IO) vivía en `domain/session.py` | 🔴 ALTA | Puerto `ISessionStore` + adapter en `infrastructure/session_store.py`; `SessionRecord` puro | `domain/` sin file-IO |
| D3 | `domain/state.py` anotaba `WorkflowState` pero retornaba `returns.Failure` (mentira de tipo); helpers de task-status (negocio) en el engine | 🔴 ALTA | `Result[WorkflowState, str]` / `Result[None, str]`; task-status fuera del engine | tipos honestos; test anti-alucinación inline |
| D4 | Doble mecanismo de transición: `Gate` (inter-nodo) vs `SubState.on_success/on_failure` (intra-nodo) sin SSOT | 🟡 MEDIA | SSOT por responsabilidad documentado + **invariante 6** en `validate_invariants` (targets intra-nodo existen) | workflow real valida OK |
| D5 | `NodeType` (vocabulario de negocio SPRINT/IMPLEMENT/...) y `GateType` MUERTOS en el engine | 🟡 MEDIA | Borrados (enums, re-exports, `__all__`) | cero usos lógicos (solo re-exports) |
| D6 | `SubState` god-dataclass (~30 campos + legacy) | 🟡 MEDIA | VOs componibles `TimeoutConfig`/`SignalEvents` + props derivadas; campos muertos `on_failure_loop`/`order` eliminados | declaración plana persiste (sin breaking) |
| D7 | `DomainEvent` stringly-typed; claves `sub_state_data` stringly (`SubStateKey` sin uso); `SessionRecord.status` string; `version` sin CAS; `NodeExecution.side_effects: list[dict]` | 🟢 BAJA | **Deuda registrada** (gaps F5/F6 del ADR) | pendiente |

### 7.2 Análisis de redundancia del `EngineBundle`

**Hallazgo.** El bundle repetía conceptos: `workflow_repo`+`state_repo` (acceso al
mismo workflow) y `task_registry`+`side_effect_engine`+`action_catalog`
(`ActionCatalog` ya es el puerto unificado de tasks+side-effects, ADR-049).

**Intento fallido (revertido).** Se intentó unificar con puertos que re-exponían los
concretos (`IWorkflowStore.workflow_repo`, `ActionCatalog.task_registry`). Eso es una
**abstracción falsa**: el engine seguía conociendo los concretos, solo que derivados de
propiedades. Se revirtió (el árbol quedó consistente).

**Lección.** Envolver sin desacoplar es el mismo anti-patrón que motivó este ADR. Un
puerto unificado de verdad exige que el engine consuma SOLO `load_graph/load_state/
save_state` y `execute_action` — refactor profundo de use cases.

**UNIFICACIÓN REAL (implementada).** El engine ahora consume SOLO dos puertos de
ejecución, sin re-exponer concretos:
- `IWorkflowStore` (`load_graph`/`load_state`/`save_state`) reemplaza `workflow_repo` +
  `state_repo`. El puerto NO expone repos (adapter en `infrastructure/workflow_store.py`).
- `actions` (`ActionCatalog.execute_action`) reemplaza `task_registry` +
  `side_effect_engine` + `action_catalog`. Tasks y side-effects se ejecutan vía el
  catálogo; el **orquestador Saga** de side-effects vive en el engine como servicio de
  dominio (`application/side_effect_saga.py`) usando `actions`, con envelopes, optional,
  idempotencia y compensación declarada por el grafo.
- `EngineBundle` y `OrchestrationService.__init__` quedaron con:
  `workflow_store`, `actions`, `workflow_io` (+ mecánica opcional). Los use cases /
  sub-orquestadores (`SubmitDataUseCase`, `TransitionUseCase`, `SubmitOrchestrator`,
  `StepOrchestrator`, `TransitionOrchestrator`, `StatusOrchestrator`, `ErrorHandler`)
  consumen los puertos, no repos/registries concretos.
- El host (`app/module.py`) declara `workflow_store`/`action_catalog` como providers y
  el engine factory resuelve SOLO esos puertos.

### 7.3 Diagnóstico de composición (container/registry)

**Hallazgo.** `container.py` + `registry.py` eran glue imperativo:
- `register_all` cableaba a mano el `OrchestrationService` y registraba servicios uno a uno.
- `container.py` **monkeypatcheaba** `orch._action_catalog = ...` /
  `se_engine._action_catalog = ...` (mutación de atributos privados entre módulos).
- Rompía la modularidad/empaquetamiento (INV-015): no era DI, era ensamblaje manual.

**Decisión.** Composición DECLARATIVA (NestJS, §2.4): `app/module.py` como módulo host
con providers lazy por id+contrato; `register_all` delgado (libs → MCP → manifests →
context → resolver `engine`/`workflow`); `container._compose` solo importa y resuelve.
Sin monkeypatch: el `ActionCatalog` es un provider que inyecta `task_registry` +
`side_effect_engine`, y el engine lo recibe por constructor.

### 7.4 Decisiones registradas (resumen)

| Decisión | Dónde | Estado |
|----------|-------|--------|
| Motor anémico: el engine NO conoce conceptos de negocio; el host valida fuera | §2.3 | ✅ hecho |
| Round-trip completo de `WorkflowState`; `ISessionStore`; `Result` honestos | §7.1 D1-D3 | ✅ hecho |
| SSOT de transiciones + invariante 6; borrado `NodeType`/`GateType`; VOs `SubState` | §7.1 D4-D6 | ✅ hecho |
| Deudas BAJAS (D7): eventos tipados, `SubStateKey`, `SessionRecord.status`, CAS | §7.1 D7 | 🔴 pendiente |
| Bundle sin puertos falsos; unificación REAL de `workflow_store`/`actions` | §7.2 | ✅ hecho (IWorkflowStore + SideEffectSaga vía actions) |
| Composición declarativa NestJS (sin glue/monkeypatch) | §2.4 / §7.3 | ✅ hecho |

### 7.5 Refinamiento de `shared/libs` (negocio fuera de libs)

**Problema (corregido).** `shared/libs/cognitive_graph` y `shared/libs/memories`
contenían **lógica de negocio del walker**, no librerías genéricas: los schemas de las
4 memorias (CPG/CKG/Delta/Trace), el search handler del trace, los CPG de referencia
(`cpg-ref-`), la proyección `COMPACT_FIELDS`/`SEVERITY_ORDER`, la resolución de paths
de proyecto (`get_project_dir`) y `MemoryQueryService`. Una librería debe ser genérica,
reutilizable y host-agnóstica (caches/builders/impls abstractas); el negocio vive en el
dominio/features (INV-015).

**Split (implementado).**
- `shared/libs/cognitive_graph` → quedó como **STORAGE ENGINE genérico** (grafo, token
  index, backends sqlite/file, ODM, searcher, io, migrations). Sin schemas de memorias,
  sin trace helpers, sin `get_project_dir`, sin `COMPACT_FIELDS`/`SEVERITY_ORDER`.
  `__init__` acepta `register_defaults` (callable inyectado) + `compact_fields`/
  `severity_order` (proyección/ranking inyectados).
- `app/memory_schemas.py` (nuevo, host) → semántica del walker: `register_default_
  memory_indexes(graph)` (schemas ckg/delta/trace/ref + search handler del trace +
  trace descentralizado del proyecto) + `COMPACT_FIELDS`/`SEVERITY_ORDER`. El host lo
  inyecta: `CognitiveGraph(register_defaults=register_default_memory_indexes)`.
- `shared/libs/memories/` ELIMINADO → `MemoryQueryService` vive en `app/memories.py`
  (negocio del walker).
- Todos los callers de producción actualizados (build_service, kg_context,
  index_service, registry, import_ckg_v1_cli, handlers) — `auto_register=True` →
  `register_defaults=register_default_memory_indexes`.
- `shared/libs` + `shared/common` quedan SIN deps hacia features/app/protocols
  (verificado con grep) → INV-015 (empaquetable) restaurado.

**Checklist por cambio:**
- [ ] Un paquete de `shared/libs` solo contiene lógica genérica/reutilizable
  host-agnóstica; la semántica del negocio (schemas, memorias, paths de proyecto) se
  inyecta desde el host (app/features) o vive allí. — anti-patrón: negocio en libs.
- [ ] `shared/libs`/`shared/common` NO importan `app`/`features`/`protocols`
  (grep CI). — anti-patrón: upward dependency (INV-015).

**Incumbencias SRP adicionales en `libs` (auditoría):**
- ✅ **L1 hecho**: `shared/libs/module.py` ELIMINADO — placeholders vacíos del prototipo
  (`class CognitiveGraph: ...`, `class PromptCollector: ...`) registrados como providers
  en el DI (módulo "shared"). Código muerto + negocio (prompts) declarado como lib.
- ✅ **L2 hecho**: `shared/libs/path_resolver` MOVIDO a `app/path_resolver.py` (host) —
  el layout del walker ya no vive en libs. El engine quedó autocontenido: `config.py`
  resuelve la raíz walker sin path_resolver; `repositories.py`/`compensation_engine.py`
  purgados; los 38 callers actualizados a `from app.path_resolver import …`.
- ✅ **L3 hecho**: `shared/libs/context_resolver.py` MOVIDO a `app/context_resolver.py`.
- ✅ **L4 hecho**: `SprintId` ELIMINADO de `shared_domain` (vocabulario walker muerto,
  cero usos); `ExecutionContext` queda (es la ENTRADA de la máquina BPMN — step/submit/
  transition lo reciben; §2.1 Naturaleza).
- ✅ **L6 hecho**: formatters de MEMORIAS (CKG/CPG/Delta/Trace) movidos de
  `shared/common/context/format.py` a `app/context_format.py` (host); `common` queda con
  solo `fmt_tokens` (genérico); `format_state_result` ELIMINADO (muerto + vocabulario
  sprint); engine `context/__init__` ya no re-exporta formatters walker.
- 🟢 **L5 parcial**: `ResultDict`/`ResultAny` ELIMINADOS (sin usos); quedan como deuda
  menor aceptada: convención `{project_id}/` en `module_system/registry.py:78` y nombres
  "proyección" en `projection_store.py` (clase genérica).
- 🟢 **L5 pendiente**: `ResultDict` alias, convención REST `{project_id}/` en
  `module_system/registry.py`, nombres "proyección" en `projection_store.py`.

---

## 8. Propuestas de solución pendientes — análisis SRP/abstracciones (documentado, SIN acción)

Todas las propuestas pendientes se DOCUMENTAN aquí con su análisis de abstracciones/SRP
antes de ejecutar. **Regla: no mover conceptos de código que violen las abstracciones o
el SRP.** Cada propuesta indica explícitamente QUÉ NO hacer.

### P1 — Ubicación de `SprintId` y `ExecutionContext` (revisión de L4)

**Análisis (uso real):**
- **`SprintId`**: SOLO re-exportado en `shared/common/shared_domain/__init__.py` — **cero
  usos** (muerto). Es vocabulario del walker ("sprint"/"spring").
- **`ExecutionContext`**: es la **entrada de la máquina de estados BPMN** — el engine
  (step/submit/transition) lo recibe como contexto de instancia; además lo usan tasks y
  features (25+). Es **mecánica del engine**, no negocio.

**Decisión SRP (qué SÍ / qué NO):**
- ✅ `ExecutionContext` → **pertenece al engine** (contexto de instancia BPMN). NO moverlo
  fuera: es el input del motor. El campo `chain_dir` es cómo el host identifica la
  instancia (adapter concern), no negocio del motor.
- ❌ `SprintId` → **NO va al engine** (BPMN no conoce "sprint"). **NO va en `shared_domain`
  genérico** (es vocabulario walker). Su hogar correcto: `features/delta_tracking` (donde
  vive el concepto sprint/spring), o **eliminarse** si sigue sin uso.
- **Estado:** documentado. NO se mueve nada hasta decidir con el usuario.

### P2 — Tests a la nueva firma

**Análisis SRP:** actualizar tests a las firmas nuevas (engine unificado, cognitive_graph
`register_defaults`, path_resolver en app) es housekeeping — no cambia abstracciones.
- ✅ Actualizar imports/firmas de tests.
- ❌ NO reestructurar producción para acomodar tests.
- **Estado:** pendiente de ejecución (postergado por el usuario).

### P3 — Commit de la rama

Housekeeping (sin abstracciones). Pendiente.

### P4 — Registro de ADRs en CKG

Housekeeping (walkers MCP down). Pendiente.

### P5 — Refinamiento frontend (ADR-010)

Movido al proyecto `react-native-frontend` (`docs/adr/ADR-010`). Los fixes H1/H2/H3 del
frontend NO tocan el engine ni `shared` — se ejecutan en el frontend (fuera de este ADR).
