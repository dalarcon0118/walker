# ADR-WALKER-045: Engine Neutro en `core/engine` + Feature Main como Composition Root Final

> **Status:** Aprobada
> **Fecha:** 2026-08-12
> **Autor:** Kilo + PO
> **Complementa:** ADR-WALKER-036 (delta_tracking SSOT), ADR-WALKER-043 (ToolRegistry), INV-010 (ModuleRegistry/TaskRegistry separados), ADR-WALKER-016 (DIP)

---

## 1. Problema

El engine tiene **dos responsabilidades mezcladas**:

1. **Componente core arquitectónico:** orquesta el grafo de workflow (submit/transition/auto-advance/context-composer).
2. **Caso de uso de dominio:** implementa el modelo de desarrollo sprint (plan_sprint, spec_task, review_task, implement, verify, close_sprint) acoplando el ciclo de vida del sprint a la máquina de estados.

Esto genera dependencias circulares y acoplamiento:

| Síntoma | Ubicación | Impacto |
|---|---|---|
| Engine importa delta_tracking | `features/engine/application/use_cases.py`, `orchestrator.py` | El core conoce el dominio de sprint |
| delta_tracking registra tasks internas en el engine | `features/task_execution/tasks/planning.py` | El dominio se cuela en el registry del core |
| _build_workflow_services arma closures sin DI | `bootstrap.py:316-406` | Composition root incompleto |
| Orquestador puede instanciarse vacío | `orchestrator.py:101` | Production bug C2: submit sin ejecutar actions |
| Tests usan mocks para cubrir el gap | `conftest.py:173-191` | Divergencia tests vs producción |

**Además**, no existe un **feature main** que sea el composition root final de la aplicación. Hoy cada entry point (CLI, MCP, WorkflowAPI) arma su propio grafo de dependencias, perpetuando la duplicidad D1 en `capa_rout.md`.

---

## 2. Decisión

**Elevar el engine a `core/engine` como infraestructura pura de workflow, aislar `delta_tracking` como modelo de desarrollo sin dependencias de framework, y crear `features/main` como composition root final que integra ambos.**

### 2.1 `core/engine` = infraestructura de workflow (agnóstico de dominio)

`core/engine` es al **workflow** lo que `libs/cognitive_graph` es a las **memorias superiores**: infraestructura pura.

- `OrchestrationService` orquesta el workflow: submit, transition, status, prepare.
- `TaskRegistry` ejecuta tasks declaradas por **otros módulos**.
- `SideEffectEngine` ejecuta side effects declarados por **otros módulos**.
- `ContextComposer` compone contexto desde providers declarados por **otros módulos**.
- No hay tasks "internas" de sprint en el engine. Las tasks de `plan_sprint`, `spec_task`, etc. **no existen en `core/engine`**; son registradas externamente por `features/main`.

**Regla:** `core/engine` no importa `delta_tracking`, `code_analysis` ni `cognitive_knowledge`. Es un componente reutilizable que orquesta workflows declarativos desde `workflow.json`.

### 2.2 `delta_tracking` = modelo de desarrollo (agnóstico de engine)

`delta_tracking` define el modelo de sprint como **funciones puras** exportadas en módulos de `tasks/` y `side_effects/`. No importa `core/engine`.

```python
# features/delta_tracking/tasks/planning.py
# Funciones PURAS de dominio — sin dependencias de core/engine
def plan_sprint(ctx, data):
    goal = data.get("goal", "")
    tasks = data.get("tasks", [])
    # ... lógica de dominio pura ...
    return {"sprint_id": sprint_id, "goal": goal, "status": "planned"}

def spec_task(ctx, data):
    # ... lógica de dominio pura ...
    return {"spec_id": spec_id, "status": "specified"}
```

```python
# features/delta_tracking/side_effects/spec_transform.py
# Side effect PURO de dominio — accede a DeltaGraph, NO al engine
def transform_spec(ctx, data):
    # ctx contiene la infraestructura de delta_tracking inyectada por features/main
    spring_service = ctx.get("spring_service")
    spec_id = data.get("spec_id")
    new_status = data.get("status")
    spring_service.update_spec_status(spec_id, new_status)
    return {"ok": True}
```

**Regla:** `delta_tracking` define **QUÉ** hace el sprint (ciclo de vida, actividades, side effects), pero no sabe **CÓMO** se ejecuta. Eso es responsabilidad de `features/main`.

**Nota:** En la arquitectura actual, `delta_tracking` accede a `WorkflowStateRepository` y `SessionStore` del engine. En la nueva arquitectura, **esa responsabilidad se elimina de `delta_tracking`** y pasa a `features/main`. `delta_tracking` solo interactúa con su propia infraestructura (DeltaGraph).

### 2.3 `features/main` = composition root final (el único que integra)

`features/main` es el **único** módulo que conoce `core/engine`, `delta_tracking`, `code_analysis`, `cognitive_knowledge`, `event_tracing`, `project`, etc. Su única responsabilidad es **componer** la aplicación:

```python
# features/main/bootstrap.py
def compose_app(chain_dir: str) -> AppComposition:
    # 1. Construir infraestructura base del engine
    from core.engine.infrastructure.task_registry import InMemoryTaskRegistry
    from core.engine.infrastructure.side_effects import SideEffectEngine
    from core.engine.infrastructure.repositories import WorkflowRepository
    from core.engine.application.orchestrator import OrchestrationService

    registry = InMemoryTaskRegistry()
    se_engine = SideEffectEngine()
    workflow = WorkflowRepository().load(chain_dir)
    orch = OrchestrationService(
        task_registry=registry,
        side_effect_engine=se_engine,
        workflow=workflow,
        ...
    )

    # 2. Discovery AUTOMÁTICO de tasks, side effects, providers y MCP modules
    # desde features/ (main SÍ puede escanear features/)
    _discover_and_register_tasks(registry)
    _discover_and_register_side_effects(se_engine)
    _discover_and_register_providers(...)
    _discover_and_register_mcp_modules(...)

    # 3. Exponer la composición completa
    return AppComposition(
        workflow=orch,
        task_registry=registry,
        side_effect_engine=se_engine,
        workflow_graph=workflow,
    )
```

**Regla:** `features/main` es el **único** que:
- Construye `OrchestrationService` con sus dependencias.
- Hace discovery y registra tasks/side effects/providers/modules de todos los features.
- Carga `workflow.json` y lo expone.
- Implementa el front controller CLI/MCP.
- Expone la API pública de la aplicación.

**Nota:** El discovery automático se mantiene, pero se ejecuta desde `features/main` (que sí puede escanear `features/`), no desde `core/engine` (que no puede).

### 2.4 Front Controller en `features/main`

`features/main` implementa el **front controller común** para CLI y MCP:

```
CLI (argv)                     MCP (JSON-RPC)
   │                               │
   ▼                               ▼
cli_interface/main()         mcp_adapter._call_tool()
   │                               │
   └───────────┬───────────────────┘
               ▼
   Front-Controller (features/main)
   handle_status / handle_submit / handle_transition / ...
               │
               ▼
   compose_app().workflow.submit/transition/status/prepare
```

Tanto CLI como MCP delegan en el front controller de `features/main`. El front controller lee `workflow.json` para exponer las rutas y tools disponibles.

### 2.5 `core/engine` lee `workflow.json`

El engine **no hardcodea** nodos, edges o actions. Lee `workflow.json` (o `workflow.v3/workflow.json`) para:

- Cargar el grafo de workflow (`WorkflowRepository`).
- Resolver prompts (`prompt_id` por nodo/sub-estado).
- Determinar side effects por sub-estado.
- Resolver eventos `on:{}`.

Esto hace que el engine sea **abstracto**: puede orquestar cualquier workflow declarativo, no solo el de sprint.

---

## 3. Consecuencias

### 3.1 Positivas

- **Engine verdaderamente neutro:** No importa `delta_tracking`, `code_analysis` ni `cognitive_knowledge`. Puede testearse aislado con un registry mock de tasks genéricas.
- **Delta-tracking como modelo puro:** El sprint se define como funciones puras sin dependencias de framework. El flujo de sprint vive en `features/delta_tracking/tasks/` y `features/delta_tracking/side_effects/`.
- **Composition root único final:** `features/main` arma TODO. No hay composition roots dispersos en CLI, MCP, WorkflowAPI, tests.
- **Desacoplamiento sin complejidad:** No hay capas nuevas, solo una separación clara de responsabilidades.
- **Tests alineados:** Los tests de delta_tracking usan el engine real (neutro). Los tests del engine usan mocks de tasks. No hay mezcla.
- **Engine como infraestructura:** Al igual que `cognitive_graph` es infraestructura para memorias, `core/engine` es infraestructura para workflows.

### 3.2 Negativas / Trade-offs

- **Movimiento de código:** `features/engine/` → `core/engine/` (cambio de import en todos los consumidores).
- **Nuevo feature main:** Requiere crear `features/main/` con `bootstrap.py`, `__init__.py`, y actualizar entry points (CLI, MCP) para consumirlo.
- **Refactor de imports:** Todo lo que hoy importa de `features/engine/` debe migrar a `core/engine/`.
- **Eliminación de tasks hardcodeadas:** El engine no tiene tasks de dominio; delta_tracking las registra en runtime.

---

## 4. Mapa de consumo (arquitectura objetivo)

```
┌──────────────────────────────────────────────────────────────┐
│                      features/main (app)                      │
│  compose_app(): composition root final                       │
│                                                                │
│  - core/engine: OrchestrationService (infraestructura)        │
│  - delta_tracking: register_sprint_tasks(registry)            │
│  - code_analysis: register_analysis_tasks(registry)           │
│  - cognitive_knowledge: register_kg_tasks(registry)           │
│  - Front Controller: handle_status / submit / transition / ... │
│  - CLI + MCP adapter                                           │
└───────────────────────────┬──────────────────────────────────┘
                            │
           ┌────────────────┼────────────────┐
           ▼                ▼                ▼
   delta_tracking     code_analysis     cognitive_knowledge
   (modelo de         (CPG)             (CKG)
    desarrollo)                              │
           │                │                │
           │  (sin dependencias mutuas)         │
           │  delta_tracking NO importa         │
           │  core/engine                        │
           │  core/engine NO importa             │
           │  delta_tracking                     │
           ▼                ▼                ▼
       core/engine (infraestructura compartida)
       OrchestrationService / TaskRegistry /
       SideEffectEngine / WorkflowRepository
```

**Reglas:**
1. `core/engine/` NO importa `delta_tracking`, `code_analysis` ni `cognitive_knowledge`.
2. `delta_tracking`, `code_analysis`, `cognitive_knowledge` NO importan `core/engine`.
3. `features/main` es el **único** que integra `core/engine` y todos los features.
4. Entry points (CLI, MCP) consumen `features/main`, no el engine directo.
5. El engine lee `workflow.json` para ser abstracto y declarativo.
6. **Discovery automático mantenido:** `features/main` escanea `features/*/tasks/`, `features/*/side_effects/`, `features/*/context/providers.py` y `features/*/mcp.py`. `core/engine` no escanea `features/`.

---

## 5. Mapa de archivos afectados

### Nuevos archivos

| Archivo | Responsabilidad |
|---|---|
| `core/engine/__init__.py` | Exporta API pública del engine |
| `core/engine/bootstrap.py` | Construye TaskRegistry, SideEffectEngine, OrchestrationService (sin discovery de features/ ni libs/) |
| `core/engine/application/orchestrator.py` | `OrchestrationService` (neutro, sin tasks de dominio) |
| `core/engine/application/use_cases.py` | `SubmitDataUseCase`, `TransitionUseCase`, etc. (neutros) |
| `core/engine/domain/models.py` | `WorkflowState`, `ExecutionContext`, etc. |
| `core/engine/domain/services.py` | `AdvanceService`, `ValidationService`, etc. |
| `core/engine/infrastructure/task_registry.py` | `InMemoryTaskRegistry` |
| `core/engine/infrastructure/side_effects.py` | `SideEffectEngine` |
| `core/engine/infrastructure/repositories.py` | `WorkflowRepository`, `WorkflowStateRepository` |
| `features/main/__init__.py` | Exporta `compose_app()` |
| `features/main/bootstrap.py` | Composition root final + discovery automático de tasks/side effects/providers/MCP/lib services |
| `features/main/app.py` | `AppComposition` (dataclass) + `WorkflowAPI` |
| `features/main/front_controller.py` | Front controller común CLI/MCP |
| `features/delta_tracking/tasks/planning.py` | Tasks de sprint (funciones puras) |
| `features/delta_tracking/tasks/implementation.py` | Tasks de implementación (funciones puras) |
| `features/delta_tracking/tasks/verification.py` | Tasks de verificación (funciones puras) |
| `features/delta_tracking/tasks/review.py` | Tasks de review (funciones puras) |
| `features/delta_tracking/side_effects/spec_transform.py` | Side effects de specs (funciones puras) |
| `features/delta_tracking/side_effects/create_adr.py` | Side effects de ADRs (funciones puras) |
| `features/delta_tracking/side_effects/planning.py` | Side effects de sprint (funciones puras) |
| `libs/*/__init__.py` | Cada paquete expone `register_module(registry)` para discovery automático de servicios DI |

### Patrón de discovery para `libs/` (estilo NestJS AppModule)

| Origen | Destino | Motivo |
|---|---|---|
| `features/engine/` | `core/engine/` | El engine es infraestructura, no un feature |

### Archivos que se mueven (Fase 4: Delta-Tracking puro)

| Origen | Destino | Motivo |
|---|---|---|
| `features/task_execution/tasks/planning.py` | `features/delta_tracking/tasks/planning.py` | Tasks de sprint pertenecen a delta_tracking |
| `features/task_execution/tasks/implementation.py` | `features/delta_tracking/tasks/implementation.py` | Stub de implement_task es dominio de sprint |
| `features/task_execution/tasks/verification.py` | `features/delta_tracking/tasks/verification.py` | verify_predicates es parte del DoD de sprint |
| `features/task_execution/tasks/review.py` | `features/delta_tracking/tasks/review.py` | review_code es parte del flujo de sprint |
| `features/task_execution/side_effects/spec_transform.py` | `features/delta_tracking/side_effects/spec_transform.py` | Side effect específico de specs |
| `features/task_execution/side_effects/create_adr.py` | `features/delta_tracking/side_effects/create_adr.py` | Side effect específico de ADRs |
| `features/task_execution/side_effects/planning.py` | `features/delta_tracking/side_effects/planning.py` | Side effects de sprint |

### Archivos que se eliminan / vacían

| Archivo | Acción | Fase |
|---|---|---|
| `features/engine/` | Eliminar (movido a `core/engine/`) | Fase 1 |
| `features/task_execution/tasks/planning.py` | Eliminar (movido a delta_tracking) | Fase 4 |
| `features/task_execution/tasks/implementation.py` | Eliminar (movido a delta_tracking) | Fase 4 |
| `features/task_execution/tasks/verification.py` | Eliminar (movido a delta_tracking) | Fase 4 |
| `features/task_execution/tasks/review.py` | Eliminar (movido a delta_tracking) | Fase 4 |
| `features/task_execution/side_effects/planning.py` | Eliminar (movido a delta_tracking) | Fase 4 |
| `features/task_execution/side_effects/spec_transform.py` | Eliminar (movido a delta_tracking) | Fase 4 |
| `features/task_execution/side_effects/create_adr.py` | Eliminar (movido a delta_tracking) | Fase 4 |

### Archivos que se modifican

| Archivo | Cambio |
|---|---|
| `core/engine/bootstrap.py` | Se limpia de discovery de `features/` y `libs/`; solo construye infraestructura genérica. `_register_libs_services` se elimina (mueve a `features/main` como discovery automático) |
| `core/engine/application/orchestrator.py` | Constructor exige `task_registry` y `side_effect_engine` |
| `features/main/bootstrap.py` | Nuevo: composition root final + discovery automático de tasks/side effects/providers/MCP + DI services de libs |
| `features/main/front_controller.py` | Nuevo: front controller CLI/MCP |
| `features/main/app.py` | Nuevo: `AppComposition` + `WorkflowAPI` expuesto |
| `features/cli_interface/application/handlers.py` | Consume `features/main` en vez de `get_module_service("workflow")` directo |
| `features/cli_interface/mcp.py` | Consume `features/main` |
| `core/engine/mcp.py` | Consume `features/main` |
| `tests/conftest.py` | `register_mock_tasks` se convierte en `register_sprint_mocks` para delta_tracking |
| `tests/test_workflow_e2e_real_bdd.py` | Usa `features/main.compose_app()` |
| `libs/*/__init__.py` | Cada paquete de libs expone `register_module(registry)` para discovery automático |

### Patrón de discovery para `libs/` (estilo NestJS AppModule)

Hoy `_register_libs_services` registra servicios DI uno por uno en `core/engine/bootstrap.py`:

```python
def _register_libs_services(registry):
    registry.register_service("cognitive_searcher", _build_searcher)
    registry.register_service("cognitive_graph", _build_graph)
    registry.register_service("path_resolver", ...)
    registry.register_service("prompt_manager", ...)
    registry.register_service("mcp_server", ...)
    registry.register_service("workflow", ...)
    registry.register_service("task_registry", ...)
    registry.register_service("side_effect_engine", ...)
```

Esto es frágil: cada vez que se agrega un servicio en `libs/`, hay que recordar registrarlo en este archivo.

**Solución:** replicar el patrón de `_discover_mcp_modules` para `libs/`:

```python
# features/main/bootstrap.py — FUTURO
def _discover_libs_services(registry):
    """Escanea libs/* y registra register_module(registry) de cada paquete.
    
    Cada paquete de libs expone una función register_module(registry) en su __init__.py.
    El discovery la encuentra automáticamente, igual que _discover_mcp_modules.
    """
    libs_pkg = importlib.import_module("libs")
    libs_dir = os.path.dirname(os.path.abspath(libs_pkg.__file__))
    for _, mod_name, is_pkg in pkgutil.iter_modules(libs_pkg.__path__):
        if not is_pkg or mod_name.startswith("_"):
            continue
        module_path = f"libs.{mod_name}"
        outcome = _register_libs_module_core(module_path, registry)
        if not is_successful(outcome):
            logger.warning("main: no se pudo registrar libs.%s", module_path)

@safe
def _register_libs_module_core(module_path, registry):
    mod = importlib.import_module(module_path)
    register_fn = getattr(mod, "register_module", None)
    if register_fn:
        register_fn(registry)
```

Cada paquete de `libs/` expone `register_module(registry)` en su `__init__.py`:

```python
# libs/cognitive_graph/__init__.py
def register_module(registry):
    registry.register_service("cognitive_graph", lambda: CognitiveGraph(...))
    registry.register_service("cognitive_searcher", lambda: CognitiveGraphSearcher(...))

# libs/path_resolver/__init__.py
def register_module(registry):
    registry.register_service("path_resolver", PathResolver)

# libs/mcp_server/__init__.py
def register_module(registry):
    registry.register_service("mcp_server", lambda: McpServer())
```

**Ventaja:** 
- No hay archivo centralizado que registrar manualmente
- Cada paquete de `libs/` es responsable de exponer sus servicios
- `features/main` solo escanea y registra automáticamente
- Igual que NestJS AppModule: cada módulo exporta su configuración, el framework la descubre

---

## 6. failure_scenarios

| # | Fallo | Causa raíz | Mitigación | Detección |
|---|---|---|---|---|
| 1 | `core/engine` importa `features/` por error | Un developer agrega `from features.delta_tracking import ...` en `core/engine/` | Linter rule: forbid imports de `features/` dentro de `core/engine/` | CI lint: `grep -r "features\." core/engine/` |
| 2 | `features/main` olvida registrar una task de dominio | Discovery automático falla o `_discover_and_register_tasks` no escanea el directorio correcto | Test de completitud: `EXPECTED_TASKS` incluye todas las tasks de cada módulo | `test_registry_completeness_bdd.py` |
| 3 | `features/main` olvida registrar un provider de contexto | Discovery automático falla o `_discover_and_register_providers` no escanea el directorio correcto | Test de completitud: `EXPECTED_PROVIDERS` incluye business, ckg, cpg, delta, trace, project | `test_context_registry_completeness_bdd.py` |
| 4 | `features/main` olvida registrar un módulo MCP | Discovery automático falla o `_discover_and_register_mcp_modules` no escanea el directorio correcto | Test de completitud: `EXPECTED_MCP_MODULES` incluye engine, delta_tracking, code_analysis, etc. | `test_mcp_modules_completeness_bdd.py` |
| 5 | Side effect de dominio no puede acceder a DeltaGraph | El side effect pudo acceder antes porque `delta_tracking` importaba el engine; ahora es puro y recibe infraestructura por DI desde `features/main` | Documentar que side effects reciben `ctx` + `data` con repositorios inyectados por `features/main` | Code review + test de side effect aislado |
| 6 | `features/task_execution/` queda vacío o con código huérfano | Las tasks/side effects se movieron a `delta_tracking/`, pero el directorio viejo no se elimina | Eliminar `features/task_execution/` o mover solo tasks/side effects genéricos del engine a `core/engine/` | Linter: no debe haber imports de `features.task_execution` desde `core/engine` |
| 7 | `WorkflowAPI` sigue usando `bootstrap()` viejo | `WorkflowAPI._get_orch()` no migra a `features/main` | `WorkflowAPI` consume `features/main.compose_app()` o recibe el `OrchestrationService` por DI | Test: `WorkflowAPI.status()` ejecuta con `features/main` |
| 8 | Tests instancian `OrchestrationService()` sin composición | Constructor sin defaults no detectado por falta de type check | Type check estricto + test de smoke que instancia `OrchestrationService` sin args debe fallar | CI type check + `test_orchestrator_smoke_bdd.py` |
| 9 | `libs/*` no expone `register_module(registry)` | Un developer agrega un paquete en `libs/` pero olvida exponer `register_module` en `__init__.py` | Linter/CI verifica que todo paquete de `libs/` tiene `register_module` exportable | `test_libs_discovery_bdd.py` |
| 10 | Servicio de `libs/` no se registra en el DI container | El discovery de `features/main` no escanea correctamente `libs/*` o el paquete tiene un error en `register_module` | Test de completitud: `EXPECTED_LIBS_SERVICES` incluye cognitive_searcher, cognitive_graph, path_resolver, mcp_server, prompt_manager | `test_libs_discovery_bdd.py` |

---

## 7. Checklist de segundo orden

1. ¿Qué módulos importan esto?
   - `features/main/bootstrap.py` (composition root final)
   - `features/delta_tracking/` (modelo de desarrollo, registra tasks y side effects)
   - `core/engine/` (deja de importar domain modules, se vuelve neutro)
   - `features/cli_interface/` y `core/engine/mcp.py` (consumen `features/main`)

2. ¿Qué side effects dispara?
   - Los side effects de dominio (`transform_spec`, `create_adr_from_spec`) se ejecutan en el `SideEffectEngine` poblado por `features/main`.
   - Los side effects genéricos (`emit_event`, `capture_decision`) siguen en el engine.

3. ¿Qué eventos de dominio publica?
   - Los eventos de sprint (`sprint_planned`, `sprint_retro_completed`, etc.) se disparan desde `delta_tracking` via `emit_event`.
   - Los eventos de workflow (`DATA_SUBMITTED`, `STATE_TRANSITIONED`) se disparan desde el engine.

4. ¿Qué tests dependen de este comportamiento?
   - Tests de delta_tracking: usan `features/main.compose_app()` o `bootstrap()` + `register_sprint_tasks()`.
   - Tests del engine: usan `OrchestrationService` con `InMemoryTaskRegistry` genérico (sin tasks de sprint).
   - Tests E2E: usan `features/main.compose_app()` completo.

5. ¿Qué cambia en MCP adapter, CLI, CKG, DeltaGraph, Trace?
   - MCP adapter y CLI consumen `features/main` en vez de `get_module_service("workflow")` directo.
   - CKG/DeltaGraph/Trace reciben eventos reales de ejecución de tasks de sprint (no mocks).
   - El engine neutro no cambia su interacción con CKG/DeltaGraph/Trace; sigue usándolos vía providers de contexto.

---

## 8. Implementación por fases

### Fase 1: Mover engine a `core/engine` (sin cambiar arquitectura externa)

1. Crear `core/engine/` y mover todo `features/engine/` manteniendo imports relativos.
2. Actualizar todos los imports de `features.engine.*` a `core.engine.*`.
3. Verificar que ningún archivo en `core/engine/` importe `features/` (linter).
4. Eliminar `features/engine/`.

### Fase 2: Engine Neutro + Delta-Tracking puro

1. Crear `core/engine/` y mover todo `features/engine/` manteniendo imports relativos.
2. Actualizar todos los imports de `features.engine.*` a `core.engine.*` en toda la codebase.
3. Verificar que ningún archivo en `core/engine/` importe `features/` (linter).
4. Eliminar `features/engine/`.
5. **IMPORTANTE:** NO mover `features/task_execution/tasks/planning.py` ni side effects a `delta_tracking` todavía. Primero hay que garantizar que `delta_tracking` puede ser puro sin dependencias de engine.
6. Eliminar de `delta_tracking` cualquier import a `features.engine.*` (WorkflowStateRepository, SessionStore). Esa lógica pasa a `features/main`.
7. Actualizar `core/engine/bootstrap.py`: no registra tasks de dominio, solo services DI genéricos.

### Fase 3: Feature Main

1. Crear `features/main/bootstrap.py` con `compose_app()`.
2. Mover funciones de discovery (`_discover_and_register_tasks`, `_discover_and_register_side_effects`, `_discover_and_register_providers`, `_discover_and_register_mcp_modules`) a `features/main/bootstrap.py`. `features/main` escanea `features/*/tasks/`, `features/*/side_effects/`, `features/*/context/providers.py` y `features/*/mcp.py`.
3. Agregar `_discover_libs_services(registry)` en `features/main/bootstrap.py`: escanea `libs/*`, importa cada paquete, y llama a `register_module(registry)` si existe. Cada paquete de `libs/` debe exponer `register_module(registry)` en su `__init__.py`.
4. `core/engine/bootstrap.py` se limpia: no hace discovery de `features/` ni `libs/`, solo construye infraestructura genérica. `_register_libs_services` se elimina.
5. Crear `features/main/front_controller.py` con handlers comunes CLI/MCP.
6. Actualizar entry points (CLI, MCP, WorkflowAPI) para consumir `features/main`.

### Fase 4: Delta-Tracking puro (sin dependencias de engine)

1. Mover `features/task_execution/tasks/planning.py` → `features/delta_tracking/tasks/planning.py`
2. Mover `features/task_execution/tasks/implementation.py` → `features/delta_tracking/tasks/implementation.py`
3. Mover `features/task_execution/tasks/verification.py` → `features/delta_tracking/tasks/verification.py`
4. Mover `features/task_execution/tasks/review.py` → `features/delta_tracking/tasks/review.py`
5. Mover side effects de dominio a `features/delta_tracking/side_effects/`
6. Actualizar imports en toda la codebase.
7. Eliminar `features/task_execution/` o mover solo tasks/side effects genéricos del engine a `core/engine/`.

### Fase 5: Limpieza final

1. Eliminar composition roots paralelos.
2. Migrar tests al nuevo patrón.
3. Eliminar `register_mock_tasks()` global; cada módulo tiene su propio `register_*_mocks()`.
4. Verificar con linter que `core/engine/` no importa `features/` y que `delta_tracking` no importa `core/engine`.

---

## 9. Referencias

- ADR-WALKER-036: `delta_tracking` es el SSOT y SRP de todo lo relacionado con el spring.
- ADR-WALKER-043: Schemas de tools definidos una sola vez (ToolRegistry).
- ADR-WALKER-016: DIP: task_execution define puertos, code_analysis implementa.
- INV-010: ModuleRegistry y TaskRegistry son SSOT separados.
- `capa_rout.md`: Mapa de duplicidad y arquitectura objetivo (D1: composition roots paralelos → 1).
- `cognitive_graph` como modelo de infraestructura para memorias superiores.
