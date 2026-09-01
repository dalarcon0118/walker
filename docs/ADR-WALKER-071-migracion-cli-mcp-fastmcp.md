# ADR-WALKER-071 — MCP como superficie única + migración a FastMCP

## 0. Contexto y objetivo

Walker tenía **una sola superficie** en `protocols/cli/cli_interface/` que mezclaba
CLI y MCP, con el anti-patrón documentado en ADR-070: el "circuito CLI→MCP→REST→CLI".
El objetivo de este ADR es **migrar el MCP a FastMCP** como framework y **consolidar el
MCP como la superficie única de capacidades** (ADR-069 §8.1: FastMCP es el estándar de
facto del ecosistema MCP). El CLI actual se mantiene como **wrapper compartido** que
reusa el mismo `AppContainer`, sin convertirse en pieza autónoma.

**Decisiones del usuario (a lo largo del hilo):**
1. Mover todo el monorepo a `mcp/` (movimiento físico en filesystem) → **DONE**
   (commit `395729b`, 553 renames, historial preservado).
2. Analizar si extraer un CLI autónomo tipo git era necesario → **NO**: el MCP ya
   expone `prepare/status/list/info` como tools, así que **solo el MCP basta**.
3. El `walker-client` autónomo con `.walker` global queda descartado por ahora
   (solo tendría sentido con uso humano-directo, y requeriría un registro global propio).
4. **Crear `fast_mcp/` desde cero** (paquete importable, guion bajo) con FastMCP como
   framework, **migrando** lo que existe en `mcp/` hacia ahí **sin dejar shims ni
   código sucio**, con el **objetivo final de eliminar todo lo que no esté en
   `fast_mcp/`**, en fases (cada fase con su capa de test).
5. **`fast_mcp` es AUTÓNOMO desde el día 1 (decisión del usuario):** `fast_mcp/` debe
   aprender a **arrancar y funcionar SIN depender del MCP anterior**. Concretamente:
   - **Sin `_call_tool`** del adapter legacy (`mcp/protocols/mcp/mcp_adapter.py`).
   - **Sin `mcp/` en `sys.path` ni imports de `mcp/protocols/*`** (el transporte
     CLI/MCP legacy). `fast_mcp` solo depende del **dominio**: `app/`, `features/`,
     `shared/` (composition root + task_dispatcher + tool_catalog).
   - **Entry-point autónomo**: `bin/walker-mcp` → `fast_mcp/main.py::run(stdio)`.
   - **Objetivo final: eliminar `mcp/` por completo** (incluido `mcp/protocols/*`):
     todo el transporte legacy desparece; solo queda `fast_mcp/` sirviendo el dominio.

## 0bis. Estado git ya normalizado (hecho)

- Move del monorepo completo a `mcp/` commiteado como `R100` (553 renames).
- `mcp/sqlite:` (carpeta basura con `:` con rutas pytest temporales) **eliminado**.
- `docs/capa_rout.md` y `docs/workflow-reconciliation-design.md` movidos a `docs/`.

## 1. Diagnóstico verificado

### 1.1 Movimiento físico sin `git mv` → estado git

El monorepo completo se movió a `mcp/` **a nivel de filesystem**, pero NO con
`git mv`. Por eso git lo ve así:

- Raíz: `app/`, `features/`, `shared/`, `protocols/`, `config/`, `data/`, `schemas/`,
  `prompts/` → **`D` (deleted)**.
- `mcp/` → **`??` (untracked)**, con 560 entradas.
- `cli/` → **vacío** (creado, sin contenido).

**Consecuencia para la migración:** para que git registre el movimiento como `R100`
(rename, preservando historial) hay que hacer `git mv` (o `git add -A` + `git rm`),
NO un simple copy. El orden importa para no perder el historial.

### 1.2 Imports internos dentro de `mcp/`

Los imports dentro de `mcp/` **NO** usan prefijo `mcp.`; siguen como:
`from app.`, `from features.`, `from shared.`, `from protocols.`,
`from config.`, `from data.`.

Esto implica que **`mcp/` debe estar en `sys.path` como la raíz del paquete**
(no la raíz de `walker/`). `bin/walker-mcp` ya hace exactamente esto:

```python
_WALKER_ROOT = os.path.dirname(_SCRIPT_DIR)  # bin/../ 
sys.path.insert(0, _WALKER_ROOT)             # raíz de walker
from protocols.mcp.mcp_adapter import serve_stdio
```

(Nota: hoy `_WALKER_ROOT` = raíz de `walker/`, porque `protocols` estaba en raíz.
Tras el move, `protocols` está en `mcp/protocols`, así que el entry point deberá
apuntar a `mcp/` como raíz de path, no a la raíz de walker.)

### 1.3 El MCP server actual es un adapter propio (no FastMCP)

`mcp/protocols/mcp/mcp_server.py` (250 líneas) define un `McpServer` propio:
`register`, `tool()`, `register_tools`, `resource()`, `prompt()`, `compose()`,
`list_tools`, `list_resources`, `list_prompts`, `call_tool`, `_publish_events`,
dispatch. Se monta via `serve_stdio()` en `bin/walker-mcp`.

**Verificado (mcp==1.23.3):** el framework **FastMCP ya está disponible** en el
official Python SDK: `mcp/server/fastmcp/` (`from mcp.server.fastmcp import FastMCP`),
con `add_tool`/`@tool`/`resource`/`prompt`/`run(transport="stdio")`. No hace falta
instalar un paquete separado.

El composition root actual (`mcp/protocols/mcp/mcp_adapter.py::_build_server`):
1. `AppContainer.build()` → puebla el service locator del engine.
2. `McpServer(registry=ToolRegistry(), tracer=TraceLogger(...))`.
3. `_sync_module_registry_to_server` → registra tools desde `tool_catalog.list_tools()`.
4. `_register_help_resource` → recurso dinámico `walker://help/{tool}`.

El **dispatch real NO cambia** en cuanto a lógica: `_call_tool` (Camino A: route_def /
Camino B: task pública) converge en `_ROUTER.dispatch`, que a su vez envuelve el
**dispatch del dominio** `execute_public_task` (`shared/core/engine/application/
task_dispatcher.py`) y los handlers del action_catalog. FastMCP reemplaza el
transporte (loop stdio JSON-RPC custom en `serve_stdio`) y la gestión de
tools/resources/prompts.

**PUNTO CRÍTICO (decisión del usuario, §0 #5):** el dispatch que `fast_mcp` reutiliza es
**el del dominio** (`task_dispatcher.execute_public_task`, `action_catalog`,
`ToolDefinition.handler` — todos en `app/`/`features/`/`shared/`), **NO** `_call_tool`
ni `_ROUTER` de `mcp/protocols/` (transporte legacy a eliminar). El resolver
path→handler del `_ROUTER` (protocols/cli) se **porta al dominio** o se reemplaza por
la invocación directa del handler, de modo que `fast_mcp` jamás importa `protocols/`.

### 1.4 El CLI hoy NO es ligero

13 de 15 archivos de `protocols/cli/cli_interface/` importan `mcp*`
(`mcp_client`, `mcp_client_holder`, `mcp_config_service`, `reloader`...). El CLI es
un cliente de su propio MCP. En la decisión final, el CLI **no se extrae** (solo MCP).

### 1.5 Tensión de integración de schema (descubierta al investigar FastMCP)

FastMCP **deriva el schema de las tools de las firmas tipadas de las funciones**
(vía `Tool.from_function` + pydantic `model_json_schema`), **NO acepta un JSON schema
arbitrario** de forma directa. Walker, en cambio, tiene **`inputSchema` declarativos
como SSOT** en `tool_catalog.list_tools()`.

**Decisión (usuario): usar el `inputSchema` SSOT directo**, adjuntando el schema
declarativo a `Tool.parameters` del `Tool` devuelto por `add_tool(...)` — sin
re-inferirlo de la firma (evita duplicar/divergir el SSOT). Cada tool se registra
con una `fn` que envuelve el dispatch SSOT `_call_tool(name, kwargs)`.

## 2. Decisión

**Layout definitivo: SOLO MCP (el CLI no se extrae como pieza autónoma), migrado a
una carpeta limpia `fast_mcp/` con FastMCP** (ver §3 para el árbol detallado).

```
walker/
  fast_mcp/                  # NUEVO: paquete importable (MCP server FastMCP)
    server.py                 # build_server(): registra tools/resources desde el SSOT
    main.py                   # run(stdin/out); phases/ → fases de migración
  mcp/                       # MONOREPO actual, fuente transitoria (a migrar y eliminar)
    app/ features/ shared/ protocols/ config/ data/ schemas/ prompts/
  bin/
    walker-mcp                # → fast_mcp/ (FastMCP), con sys.path a mcp/
    walker                    # wrapper que reusa el mismo AppContainer (no pieza independiente)
```

> **Decisión de layout:** la carpeta es `fast_mcp/` (guion bajo), no `fast-mcp/`
> (guion). Un directorio con guion no es un identifier de módulo Python válido; el
> guion bajo lo hace importable por pytest y por imports cruzados.

**Restricción fundamental de la migración (decisión del usuario):**
- `fast_mcp/` se construye **desde cero** con FastMCP, **sin shims ni código sucio**.
- Se **migra incrementalmemente** (por fases) lo que existe en `mcp/` hacia `fast_mcp/`.
- Al terminar, **todo lo que no esté en `fast_mcp/` se elimina**.

### 2.1 Decisión: el CLI actual cubre lo que ya hace el MCP (no se extrae)

Tras analizar, el CLI de "lectura/registro" (`prepare`, `status`, `list`, `info`)
**ya existe como tools MCP** (`initialize_project`, `list_projects`, `show_dashboard`,
etc., expuestos vía `tool_catalog.list_tools()`), y el MCP `prepare` puede registrar
proyectos desde cero con `ProjectService.resolve_paths` + registro global, **sin**
necesitar un CLI previo para bootstrap.

Conclusión (decisión tomada): **solo el MCP basta**. El CLI actual se mantiene como
wrapper que comparte `AppContainer` con el MCP (mismo monorepo), **sin invertir** en
convertirlo en un paquete autónomo tipo git. La idea futura del `walker-client`
autónomo con `.walker` global queda descartada por ahora (no aporta valor mientras
el MCP ya expone las mismas tools; un CLI autónomo solo tendría sentido si el flujo
de uso fuera humano-directo sin agente, y requeriría su propio registro global).

> Nota: la carpeta `cli/` vacía creada en la raíz durante la exploración preliminar
> se **descarta** de la migración (no se extrae nada a ella). El CLI vive en
> `protocols/cli/` como hoy.

### 2.2 Migración a FastMCP (arquitectura concreta)

El nuevo server vive en `fast_mcp/` y usa FastMCP como framework. **No se
reimplementa el dominio**: el composition root, el dispatch de dominio y el SSOT de
tools se reutilizan desde `app/`/`features/`/`shared/`. **`fast_mcp` NO depende del
transporte legacy**: no importa `mcp/protocols/*` (ni `_call_tool`, ni `_ROUTER`, ni
`McpServer`, ni `serve_stdio`). Esa capa (`mcp/protocols/*`) es lo que se elimina al
cerrar la migración.

- **Composition root** (`fast_mcp/server.py::build_server`): construye `FastMCP("walker")`,
  registra tools/resources desde el SSOT y monta las fases (`phases.install`).
  El `AppContainer.build()` (en `app/`) sigue siendo el composition root del dominio;
  `fast_mcp` lo consume **sin pasar por `protocols/`**.
- **Tools**: se registran programáticamente desde `tool_catalog.list_tools()` (SSOT).
  Cada tool: `fn` = handler del **dominio** (no `_call_tool` del adapter legacy):
  - **Camino A** (tool con route_def / ToolDefinition.handler): invoca el handler de
    dominio directamente (via `ToolDefinition.handler` o el controller de dominio),
    construyendo flags con el `flags_builder` del `RestRoute` — sin `_ROUTER`.
  - **Camino B** (task pública): invoca `shared.core.engine.application.task_dispatcher.
    execute_public_task(name, project_id, chain_dir, task_data, channel="mcp")` — el
    core SSOT del dispatch, que NO vive en `protocols/`.
  `Tool.parameters` = `inputSchema` SSOT (sin re-inferir de la firma, R-03bis).
- **Dispatch del dominio (SSOT)**: `execute_public_task` (`shared/.../task_dispatcher.py`)
  + `action_catalog` + `ToolDefinition.handler`. El resolver path→handler del `_ROUTER`
  (protocols/cli) se **porta al dominio** como un adaptador de invocación dentro de
  `fast_mcp/` (capa fina), de modo que `fast_mcp` jamás importa `protocols/`.
- **Resource `walker://help/{tool}`**: registrado con `add_resource_template` (mismo
  contenido desde `build_tool_help`, SSOT).
- **Transporte**: `run(transport="stdio")` para agents locales (reemplaza el loop
  JSON-RPC custom de `serve_stdio`). Streamable HTTP opcional.
- **Namespace**: si hace falta granularidad, se puede pasar una `server=`/sub-prefijo
  por dominio; por defecto se exponen todas las tools del SSOT.

### 2.3 ¿Qué pasa con el `McpServer` propio actual?

Con la decisión de `fast_mcp/` **desde cero**, el `McpServer` propio de
`mcp/protocols/mcp/mcp_server.py` queda **fuera de `fast_mcp/`** y por tanto
**candidato a eliminarse**. El nuevo server no depende de él.

El `DAG gating` y el `_publish_events` que vivían ahí (si son necesarios) se incurren
**dentro** del dispatch SSOT (`_call_tool`), que se reutiliza — no se reimplementan.
Si algún comportamiento de gating no queda cubierto por el dispatch existente, se
porta al nuevo server antes de eliminar `mcp/`.

## 3. Mapeo de features base

Todas las tools se exponen **planas** vía `tool_catalog.list_tools()`, que fusiona
dos fuentes (SSOT, ADR-043): tools del `ModuleRegistry` y tasks públicas del
`TaskRegistry` (son `shared/tasks/actions/*`). El dispatch `_call_tool` mantiene la
misma bifurcación para ambas. El mapeo por fase agrupa las tools **por feature** para
migrarlas progresivamente.

```
walker/
  fast_mcp/                       # NUEVO: paquete importable
    __init__.py
    server.py                     # build_server() — assembly (Fase 0 hecho)
    main.py                       # run(transport="stdio")
    registration.py               # puente FastMCP (schema SSOT + arg_model)
    composition.py                # cableado de contexts (workflow + memories)
    phases/
      __init__.py                 # PHASES[] + install() — ensamblado por fase
      phase1_prepare.py           # Fase 1: prepare/list/status
      phase2_memories.py          # Fase 2: memorias CKG/CPG (14 tools)
      phase3_tasks.py             # Fase 3: submit/transition/help/list_projects
    features/
      project/                    # Fase 1: prepare/list/status (domain/app/infra)
      code_analysis/              # Fase 2: CPG, ast-grep
      cognitive_knowledge/        # Fase 2: CKG
      memories/                   # Fase 2: handler registry + dispatch
      workflow/                   # Fase 3: engine (domain/app/infra/handlers)
    shared_kernel/                # kernel compartido
  tests/test_fast_mcp_migration_bdd.py   # Fase 0: skeleton (verde)
```

### Tools por feature (inventario verificado)

| Feature | Tools públicas (name) | Fase |
|---|---|---|
| **workflow core** | `prepare` `list` `status` | **1** |
| **workflow engine** | `submit` `transition` `help` `list_projects` | **3** |
| **proyecto/init** | `detect_project_context`* `build_project_config`* `create_first_sprint`* | deferred |
| **indexación CPG** | `build_code_index` `index_repo` `update_incremental_code` | 2 |
| **conocimiento CKG** | `extract_knowledge` `knowledge_query` `knowledge_metadata` `knowledge_agent_context` `session_knowledge` | 2 |
| **análisis de código** | `cpg_*` `code_context` `inspect_codebase` `read_code` `code_search` | 2 |
| **sprint/planning** | `sprint_planning` `resolve_project_sprint` | deferred |
| **ejecución** | `implement_task` `run_tests` `verify_predicates` `review_code` `capture` `refine_*` `research_topic` `trace_push` `intelligence` | deferred |
| **integración** | `update_project` `context_gathering` `initial_sample` | deferred |

\* scope internal — no expuestas al agente, pero forman parte del bootstrap de prepare.

## 3bis. Arquitectura objetivo del paquete (escalamiento — Opción A confirmada)

**Decisión del usuario**: la migración es COMPLETA y el dominio (~59.5k LOC) vive
**DENTRO de `fast_mcp/`** (Opción A). Se ejecuta por fases (§4). El riesgo del
crecimiento sin división se resuelve portando la MEJOR idea del legacy — los
**bounded contexts con capas** — y reemplazando la peor — el service locator por
string (34 servicios, ~1.7k LOC de plumbing DI) — por **composición explícita**.

### Estructura objetivo

```
fast_mcp/
├── main.py / server.py          superficie (transporte + assembly)
├── registration.py              puente FastMCP (schema SSOT + arg_model)
├── phases/                      declaración de tools por fase (fail-open)
├── composition.py               ★ ÚNICO lugar que cablea contexts entre sí
├── shared_kernel/               kernel: paths, registro global, contracts comunes
└── features/                    ★ BOUNDED CONTEXTS (división de responsabilidad)
    ├── project/                 prepare/list/status (domain/application/infrastructure)
    ├── code_analysis/           CPG, ast-grep
    ├── cognitive_knowledge/     CKG
    ├── delta_tracking/          sprints, specs
    ├── event_tracing/           trace
    └── workflow/                engine, task registry, side effects, registries
```

### Reglas de coherencia (enforcement automático, no confianzas)

1. `features/X` **NO importa** `features/Y` — comunicación solo vía
   `composition.py` (inyección por constructor) o interfaces en `shared_kernel`.
2. `*/domain/` es **puro**: sin infra, sin otros contexts, sin transporte.
3. La superficie (`phases/`) solo recibe objetos **ya cableados** por `composition.py`.
4. Los **registries viven dentro de su context** (tasks en `workflow/`, etc.) —
   pluggability local, no global.
5. Un **test de arquitectura** (import-linter) falla si se viola 1–3.

### Regla de port del dominio (qué entra y qué no)

| Entra (lógica ~51.5k LOC) | NO entra (plumbing ~1.7k LOC + protocols 6.2k) |
|---|---|
| CPG, CKG, graph libs, engine (estado, gates, orquestadores), tasks, delta, trace | `module_system/` (ModuleManifest NestJS), `app/composition/` (34 servicios por string), `AppContainer` singleton, lazy factories anti-ciclo, `resolve_service` (roto), `protocols/*` (transporte) |
| Ports del engine como `typing.Protocol` + inyección por constructor | Service locator global |
| Registries explícitos por context (tasks, side effects, providers) | Auto-registro global por import |

### Benchmark de MCPs comparables (investigación oct-2026)

| Proyecto | Dominio de walker que representa | Lección adoptada |
|---|---|---|
| **Task Master** (36 tools) | workflow/tasks | MCP **fino sobre core package** — valida phases finas + dominio dentro; **carga selectiva de tools** por env (36 ≈ 21k tokens) |
| **Serena** (LSP) | análisis de código | tools desacopladas del transporte; ops pesadas **lazy/en background**; memoria por proyecto (`.serena/memories/` ≈ `.cognitive/`) |
| **Memory MCP** (oficial) | knowledge graph | minimalismo: manager + JSONL + annotations (`readOnlyHint`) + resources |
| **Code-Index-MCP** | indexación CPG/search | plugin-per-language; índices centralizados por hash; SLAs medidos |

Walker es el único que combina workflow + memoria + análisis en un server; la
arquitectura de contexts + composición explícita es consistente con los 4.

### Presupuesto de contexto medido (real, no estimado)

Fuente: `tests/test_context_budget_bdd.py` (catálogo SSOT del legacy).
- 31 tools FULL (schemas+output+metadata): **~7.797 tokens** (Task Master: 21k).
- Sin outputSchema/metadata: **~2.981 tokens**. Promedio: 251/tool.
- Por fase: core(3) ≈ 731 · +memorias ≈ 3.000 · full ≈ 7.800.
- Conclusión: sin necesidad urgente de carga selectiva; si hace falta, el
  mecanismo natural es `PHASES` (enabled por fase) o un `WALKER_TOOLS` env.


## 4. Plan por fases (cada fase = su capa de test)

La migración es **incremental y siempre funcional**: el server arranca completo
(composition root + FastMCP), pero cada fase **activa un subconjunto de tools** y
expone su propia capa de test. El engine NO se crece por fases (el dispatch
`_call_tool` y `tool_catalog` soportan todo desde el día 1); lo que se incrementa es
el **registro de tools en el server + su verificación**.

### Fase 0 — Skeleton FastMCP (DONE ✅)
- **Qué:** paquete `fast_mcp/` vacío: `server.py::build_server()` + `main.py` +
  `phases/` (contract `install(server)`) + health tool `walker_ping`. Sin fases activas.
- **Limpieza:** `fast_mcp/` como paquete importable (no `fast-mcp/`); `pythonpath = ["mcp", "."]`
  para que la suite resuelva tras el move.
- **Capa de test:** `tests/test_fast_mcp_migration_bdd.py` — **5 tests verdes**:
  paquete importable, build_server()→FastMCP, health tool presente, round-trip, y
  guarda anti-falso-verde (las tools del engine NO aparecen si la fase no está activa).

### Fase 1 — Workflow core (prepare / list / status) — DONE ✅ (autónoma)
- **Qué (resultado real):** port de los 3 casos de uso AL paquete:
  `fast_mcp/core.py` (paths, registro global, resolución git-like, detección) +
  `fast_mcp/use_cases.py` (prepare/list/status). Tools `prepare` `list` `status`
  con inputSchema declarativo en `Tool.parameters` (R-03bis vía
  `registration.register_domain_tool`). **Cero imports de `mcp/`**; `mcp/` fuera
  de sys.path (bin + server). Sin `walker_ping` ni tools extra (decisión del
  usuario: exactamente 3 — `submit/transition/help` van en Fase 3).
- **Validación:** audits AST + runtime (cero módulos de mcp/ cargados), stdio
  E2E (`test_fast_mcp_stdio_validation_bdd`, `test_fast_mcp_protocol_session_bdd`),
  y **ejecución real desde el agente** (`walker_list` → 7 proyectos;
  `walker_status` → health de walker).
- **Datos:** registro global migrado a su hogar final `data/projects.json`
  (guard idempotente `test_registry_migration_bdd`); mismo contrato
  `project.json` / `.workflow-state.json` que el legacy.
- **Nota:** una primera implementación (oct-2026) fue un **shim** que delegaba
  en `AppContainer`/`execute_public_task` con `mcp/` en sys.path (cargaba 18
  módulos `protocols.*` en runtime); **violaba §0 #4/#5** y fue reemplazada
  por este port real.### Fase 2 — Memorias (CKG/CPG) que leen y escriben en prepare — DONE ✅
- **Qué (resultado real):** port de memorias CKG/CPG a `fast_mcp/`:
  - `fast_mcp/features/cognitive_knowledge/` (domain + application + infrastructure):
    `CkgApplicationService` (fachada), `CodeKnowledgeGraph` (dominio), `CognitiveCkgRepository`
    (infra via CognitiveGraph), `WalkerCkgAdapter` (puerto task_execution), use cases
    (`SaveToCkgUseCase`, `GatherCkgContextUseCase`, `CheckCkgHealthUseCase`, `IndexRepoUseCase`,
    `CollectKnowledgeUseCase`, `ResearchTopicUseCase`, `GetCkgNodesForTaskUseCase`).
  - `fast_mcp/features/code_analysis/` ya estaba portado (Fase 0/1): CpgService,
    CodeContextService, CodeAnalysisService, use cases, domain entities.
  - `fast_mcp/shared_kernel/cognitive_graph/` ya estaba portado: CognitiveGraph,
    FileBackend, Index, ODM.
  - `fast_mcp/composition.py` extendido con `memory_tool_runner()` y
    `_dispatch_memory_tool()` — despacha las 14 tools a los application services.
  - `fast_mcp/phases/phase2_memories.py` — registra las 14 tools con
    `register_domain_tool()` + inputSchema declarativo (R-03bis).
- **Tools (14):** indexación (`build_code_index` `index_repo` `update_incremental_code`),
  conocimiento (`extract_knowledge` `knowledge_query` `memory_schema` `recall_knowledge`
  `documenting_knowledge` `search_memories` `agent_context`) y análisis
  (`code_context` `inspect_codebase` `read_code` `code_search`).
- **Criterio de salida:** server arranca con 17 tools (3 Fase 1 + 14 Fase 2),
  inputSchema declarativo verificado, cero imports de `mcp/` en fase2_memories.py.
- **Capa de test:** 13 tests en `tests/test_fast_mcp_phase2_bdd.py` (superficie,
  schemas, autonomía, despacho) + tests existentes de Fase 1 (10) = 23 verdes.
- **Pendiente (mejora continua):** stale checks en `status` (CPG/CKG health),
  port completo de `task_intelligence` para `agent_context`, tests E2E con
  proyecto real indexado.

### Fase 2.1 — Clean design refactor (dentro de Fase 2)
- **Qué:** refactor de arquitectura limpio sobre el port de Fase 2:
  1. **Handler Registry Pattern** (`fast_mcp/features/memories/`): `_dispatch_memory_tool`
     (god function 150+ líneas) → `MemoryContext` + `handlers/__init__.py` (registry)
     + `handlers/code_analysis.py` (6 handlers) + `handlers/knowledge.py` (8 handlers).
     Dispatch centralizado con error handling uniforme, lazy import + cache.
  2. **Service Split** (`cognitive_knowledge/application/services/`):
     `CkgApplicationService` (god object 600 líneas) → `CkgWriteService` (182 líneas,
     mutaciones) + `CkgReadService` (199 líneas, queries) + `CkgExtractService`
     (201 líneas, CPG cross-feature) + `ckg_common.py` (56 líneas, helpers compartidos).
     `CkgApplicationService` queda como facade thin de 135 líneas (delegación pura).
  3. **Dead code removal**: EventBus (15 líneas, nunca instanciado), IKnowledgeQueryService
     (25 líneas, nunca implementado), `save_knowledge` method (42 líneas, nunca llamado),
     `ckg_application_service` factory (3 líneas, nunca llamado), 6 trivial use cases
     (220 líneas, 33-40 líneas cada una de ceremonia pura) inlineadas en el servicio.
  4. **Law of Demeter fix**: 3 handlers (`recall_knowledge`, `search_memories`,
     `memory_schema`) que accedían a `ckg._repo._cg.*` ahora usan métodos públicos
     `ckg.schema()`, `ckg.search_many()`, `ckg.list_indexes()`.
- **Resultado:** 26 archivos → 17 archivos (-35%), 1,827 líneas → 1,669 líneas (-9%),
  god object eliminado, 0 LoD violations, 0 dead abstractions.
- **Capa de test:** 32 tests verdes (10 migration + 13 phase2 + 9 handler registry).
  Tests de handler registry: `tests/test_fast_mcp_handler_registry_bdd.py`.
- **Decisiones de diseño:**
  - `CodeKnowledgeGraph` (93 líneas) se mantiene como objeto de dominio enfocado
    (no se fusiona en el servicio — riesgo/beneficio desfavorable).
  - Empty `__init__.py` se mantienen (requeridos para packages Python).
  - `index_repo.py` (122 líneas, lógica real: git clone + outline parsing)
    se mantiene como use case independiente.

### Fase 3 — Tasks + workflow completo — DONE ✅

**Resultado real (4 Sprints, domain-first):**

**Sprint 1 — Domain layer** (`fast_mcp/features/workflow/domain/`):
- Port de `entities.py` (401 LOC — simplificado: sin `generate_flows()`, `SubState.from_dict()`
  con defaults), `ports.py` (206 LOC — 8 puertos, reducido de 16), `values.py` (93 LOC),
  `execution.py` (74 LOC), `events.py` (44 LOC), `guards.py` (36 LOC), `errors.py` (67 LOC),
  `enums.py` (24 LOC).
- Domain services: `advance.py` (60 LOC), `block_resolver.py` (72 LOC — simplificado:
  solo estrategia "force"), `transition.py` (48 LOC), `validation.py` (59 LOC).
- Eliminados: `contract_validator.py` (443 LOC), `graph_analyzer.py` (157 LOC),
  `session.py` (91 LOC), `workflow_action.py` (141 LOC), 5 domain services
  (compensation_engine, conflict_resolver, escalation_registry, signal_bus, field_resolver).
- **Result:** 1,190 LOC (de 4,982 originales, -76%), 14 archivos (de 30, -53%).
- **Tests:** 52 domain tests (39 originales + 13 post-review fixes).

**Sprint 2 — Application layer** (`fast_mcp/features/workflow/application/`):
- `bundle.py` (41 LOC — EngineBundle, project-scoped VO, frozen).
- `orchestrator.py` (509 LOC — OrchestrationService: submit/transition/status/help,
  colapsado de 4 sub-orchestrators + 6 use cases).
- `task_dispatcher.py` (65 LOC — bridge MCP → domain).
- `state_manager.py` (36 LOC — ensure_state, store_submit_data).
- Eliminados: 6 use cases (1,400 LOC), 4 sub-orchestrators (1,471 LOC),
  step_orchestrator, error_handler, prompt_service, event_dispatch_service,
  side_effect_saga, context_resolver, workflow_api.
- **Result:** 656 LOC (de 4,491 originales, -85%), 4 archivos (de 18, -78%).
- **Tests:** 32 application tests (mocked ports).

**Sprint 3 — Infrastructure adapters** (`fast_mcp/features/workflow/infrastructure/`):
- `noop_tracer.py` (70 LOC — NoopTracer, stub silencioso).
- `noop_side_effects.py` (29 LOC — NoOpSideEffectEngine, retorna Success).
- `default_workflow_io.py` (68 LOC — ejecución de scripts vía subprocess).
- `graph_repository.py` (216 LOC — carga workflow.json con import resolution,
  validación de invariantes en load).
- `state_repository.py` (131 LOC — CAS + reconciliación sub_state_index en load,
  atomic write vía .tmp + os.replace()).
- `workflow_store_adapter.py` (31 LOC — IWorkflowStore unificado).
- **Result:** 550 LOC (de 1,180 originales, -53%), 7 archivos (de 12, -42%).
- **Tests:** 23 infrastructure tests (filesystem real + CAS).

**Sprint 4 — Handlers + composition wiring**
(`fast_mcp/features/workflow/handlers/` + `composition.py` + `phase3_tasks.py`):
- `context.py` (106 LOC — WorkflowContext, resuelve dependencias per-project).
- `handlers/__init__.py` (80 LOC — registry + dispatch, mismo patrón que Phase 2).
- `handlers/workflow.py` (85 LOC — 5 handlers: submit, transition, status, help, list_projects).
- `composition.py` extendido con `workflow_tool_runner()`.
- `phase3_tasks.py` actualizado: registra 4 tools (submit, transition, help, list_projects)
  con inputSchema declarativo (R-03bis). `status` se mantiene en Phase 1 (evita conflicto).
- **Result:** 271 LOC, 3 archivos nuevos + 2 editados.
- **Tests:** 26 handler/composition tests.

**Total Fase 3:** 2,684 LOC, 133 tests nuevos, 152 tests totales.

**Tools registradas por Fase 3 (4):** `submit` `transition` `help` `list_projects`.

**Fase 3 NO incluye (deferred a Sprint 5):**
- `delta_tracking/` (6,428 LOC) — usa ISprintService (stub retorna vacío).
- `event_tracing/` (1,324 LOC) — usa ITracer (NoopTracer).
- `shared/tasks/` (6,643 LOC) — task registry + side effects.
- `context/` (1,256 LOC) — DI container, reemplazado por composition.py.
- Sprint/planning tools, ejecución tools, intelligence tools.

**Criterio de salida:** ✅ Flujo E2E por MCP: prepare → submit → transition → help
sin imports de `mcp/`. 152 tests verdes. Zero `mcp/` imports en workflow/.

**Capa de test:** `tests/test_fast_mcp_workflow_domain_bdd.py` (52),
`tests/test_fast_mcp_workflow_application_bdd.py` (32),
`tests/test_fast_mcp_workflow_infrastructure_bdd.py` (23),
`tests/test_fast_mcp_workflow_handlers_bdd.py` (26).

### Fase 4 — Cierre: eliminación de `mcp/`
- **Qué:** con Fase 3 verde, **eliminar `mcp/` por completo** (el dominio ya
  vive en `fast_mcp/` — Opción A): ~65k LOC de fuente transitoria fuera,
  `pyproject.toml` sin `pythonpath=["mcp", ...]`, tests que solo probaban el
  legacy retirados (los que portaron, ya corren contra fast_mcp), ADR-016 y
  cross-references actualizadas. `data/` ya está en su hogar final.
- **Capa de test:** audits de autonomía + arquitectura en verde SOLO contra
  `fast_mcp/`; ningún import de `mcp/` en la suite; import de
  `mcp/protocols/mcp/mcp_server` imposible.

## 4. Reglas de diseño (non-negotiables)

- R-01: El CLI (`protocols/cli`) se mantiene como wrapper que comparte `AppContainer`;
  NO se convierte en paquete autónomo. El MCP es la superficie única de tools.
- R-02: `prepare/status/list/info` se sirven como **tools MCP** (ya existen:
  `initialize_project`, `list_projects`, `show_dashboard`, ...). No se duplican en un
  binario CLI separado.
- R-03: El MCP vive en `fast_mcp/` (paquete importable) y usa **FastMCP** como framework;
  tools thin, lógica en dominio. `mcp/` es fuente transitoria que se **elimina** al
  completar la migración (todo lo que no esté en `fast_mcp/` se descarta).
- R-03bis: El `inputSchema` declarativo de `tool_catalog` es el **SSOT** del schema de
  cada tool; se adjunta a `Tool.parameters` del FastMCP server, **sin re-inferir** de
  la firma (evita duplicar/divergir).
- R-03ter: El **dispatch del dominio** es SSOT y se reutiliza en `fast_mcp/`:
  `execute_public_task` (`shared/core/engine/application/task_dispatcher.py`),
  `action_catalog` y `ToolDefinition.handler`. FastMCP solo aporta transporte +
  registro, no reimplementa el despacho. **`fast_mcp` NUNCA importa el transporte
  legacy** (`mcp/protocols/*`: ni `_call_tool`, ni `_ROUTER`, ni `McpServer`, ni
  `serve_stdio`) — esa capa se elimina al cerrar la migración (§0 #5).
- R-04: El dominio (`shared/core/engine`) nunca importa superficies (ADT-067, INV-W068-09).
- R-05: Toda la resolución de rutas usa `app/path_resolver` como **SSOT de rutas**
  (registro global `data/projects.json`).
- R-06: El move se registra en git como **rename** (historial), no como copy.

## 5. failure_scenarios

```json
[
  {
    "fallo": "FastMCP re-infiere el schema de la firma y el agente ve un schema distinto al inputSchema SSOT de tool_catalog",
    "causa_raiz": "FastMCP deriva schema de firmas tipadas, no acepta JSON schema arbitrario",
    "mitigacion": "adjuntar el inputSchema SSOT a Tool.parameters tras add_tool (R-03bis), sin re-inferir",
    "deteccion": "test e2e list_tools: cada tool expone exactamente el inputSchema de tool_catalog"
  },
  {
    "fallo": "una tool se registra en FastMCP pero el dispatch falla (perdio el DAG gating / _publish_events del McpServer propio)",
    "causa_raiz": "nuevo server que no reusa el dispatch",
    "mitigacion": "el handler de cada tool llama a _call_tool(name, kwargs), que converge en _ROUTER.dispatch (R-03ter); no se reimplementa",
    "deteccion": "test e2e call_tool por cada tool y verificacion de events/gating"
  },
  {
    "fallo": "al ajustar bin/walker-mcp a fast_mcp/, los imports 'from app.'/'from protocols.' dejan de resolver",
    "causa_raiz": "sys.path apuntaba a la raiz de walker, no a mcp/",
    "mitigacion": "bin/walker-mcp inserta mcp/ en sys.path como raiz de import; verificar boot",
    "deteccion": "boot MCP y test de import aislado"
  },
  {
    "fallo": "el primer proyecto no se puede preparar solo con el MCP (sin CLI previo)",
    "causa_raiz": "bootstrap dependia de un CLI separado",
    "mitigacion": "la tool MCP initialize_project resuelve paths via ProjectService.resolve_paths + registro global; no requiere CLI",
    "deteccion": "test e2e: prepare un proyecto nuevo via tool MCP y verificar data/projects.json"
  },
  {
    "fallo": "una tool MCP (status/prepare/list/info) no resuelve el proyecto correctamente al usar path_by_CWD vs path_global del registro",
    "causa_raiz": "divergencia en como se resuelve project_id vs project_root",
    "mitigacion": "toda resolucion de rutas via app/path_resolver como SSOT (R-05); el registro global (data/projects.json) resuelve project_root desde project_id",
    "deteccion": "test e2e: llama status/list via tool MCP y verifica que coincide con el project_root real del disco"
  },
  {
    "fallo": "tras completar la migracion, queda codigo de mcp/ colgado (McpServer propio, serve_stdio, module.py) sin migrar a fast_mcp/",
    "causa_raiz": "limpieza incompleta al final de la migracion",
    "mitigacion": "cierre: eliminar de mcp/ todo lo no reutilizado; el objetivo es que solo quede fast_mcp/",
    "deteccion": "grep de McpServer/serve_stdio/module no usado; import de mcp/protocols/mcp/mcp_server desaparece"
  },
  {
    "fallo": "el layout 'fast-mcp/' (con guion) no es importable como paquete Python y rompe pytest/imports cruzados",
    "causa_raiz": "un directorio con guion no es un identifier valido de modulo",
    "mitigacion": "renombrar a fast_mcp/ (con guion bajo) como paquete importable; el entry point y tests importan fast_mcp.server.build_server",
    "deteccion": "import fast_mcp exitoso desde la suite; 'import fast-mcp' falla (esperado)"
  },
  {
    "fallo": "la suite de tests no resuelve los imports de mcp/ tras el move (from app/shared/protocols)",
    "causa_raiz": "pythonpath apuntaba a la raiz ('.'), pero el codigo ahora vive en mcp/",
    "mitigacion": "pythonpath incluye mcp y . en pyproject.toml; fast_mcp/ y mcp/ ambos resolubles",
    "deteccion": "conftest importa shared/app sin ImportError; suite completa colecta"
  },
  {
    "fallo": "CognitiveCkgRepository no puede resolver graph_factory y lanza ValueError en runtime",
    "causa_raiz": "graph_factory no se inyecta desde el composition root y el fallback no esta disponible",
    "mitigacion": "_resolve_graph_factory() construye CognitiveGraph con MemoryConnection; fallback a None con error claro",
    "deteccion": "test_fase2_composition_memory_runner_disponible verifica que el runner se construye sin errores"
  },
  {
    "fallo": "una tool de Fase 2 (knowledge_query, inspect_codebase, etc.) falla en runtime porque el CPG/CKG no esta disponible",
    "causa_raiz": "proyecto no preparado o .cognitive/ corrupto",
    "mitigacion": "cada runner en _dispatch_memory_tool devuelve dict con key error sin levantar excepcion; fail-open",
    "deteccion": "tests de despacho (test_fase2_read_code_sin_contexto_devuelve_error, test_fase2_code_search_sin_query_devuelve_error)"
  },
  {
    "fallo": "inputSchema de una tool de Fase 2 no tiene properties y FastMCP rechaza el tools/list",
    "causa_raiz": "schema declarativo incompleto (falta type=object o properties)",
    "mitigacion": "_valid_input_schema() en tool_catalog normaliza; test_fase2_tools_tienen_input_schema verifica todas",
    "deteccion": "test de superficie: cada tool tiene inputSchema con type=object y properties"
  },
  {
    "fallo": "phase2_memories.py importa modulos de mcp/ (features.cognitive_knowledge del legacy) en vez de fast_mcp/",
    "causa_raiz": "copia sin actualizar imports al portar del legacy",
    "mitigacion": "test_fase2_no_importa_nada_de_mcp hace audit AST de phase2_memories.py",
    "deteccion": "test de autonomia: cero imports forbidden en fase2_memories.py"
  },
  {
    "fallo": "CkgApplicationService facade se desincroniza de los servicios focused (CkgWriteService, CkgReadService, CkgExtractService) despues de un cambio",
    "causa_raiz": "la facade delega a los servicios pero si se agrega un metodo nuevo al servicio sin actualizar la facade, los consumidores que usan la facade no lo ven",
    "mitigacion": "la facade es thin delegation pura (metodos = 1 linea de delegacion); los handlers importan directamente del servicio focused, no de la facade",
    "deteccion": "test de superficie: CkgApplicationService expone todos los metodos de los 3 servicios"
  },
  {
    "fallo": "handler imports CkgApplicationService en vez del servicio focused, perdiendo la ventaja del split",
    "causa_raiz": "惯性: los handlers existentes importaban CkgApplicationService antes del split",
    "mitigacion": "handlers actualizados para importar CkgWriteService/CkgReadService/CkgExtractService directamente",
    "deteccion": "grep de imports: handlers no deben importar CkgApplicationService (facade backward-compat only)"
  },
  {
    "fallo": "MemoryContext.resuelve servicios percall pero los handlers tambien resuelven via CkgApplicationService, causando doble resolucion",
    "causa_raiz": "MemoryContext.ckg() resuelve CKG, pero handler de knowledge_query llama a CkgReadService.gather_context(chain_dir) que resuelve CKG otra vez",
    "mitigacion": "aceptable en la fase actual (lazy resolution es idempotente); optimizacion futura: inyectar servicios via MemoryContext",
    "deteccion": "no hay bug visible, pero doble resolucion es overhead medible en profiles"
  },
  {
    "fallo": "StateRepository.save() pierde actualizaciones concurrentes (lost update) porque no verifica revision",
    "causa_raiz": "CAS removido del orchestrator (Sprint 2) y no implementado en repository",
    "mitigacion": "StateRepository implementa CAS: compara state.revision con stored_revision antes de guardar; incrementa en save; retorna StateConflictError en mismatch",
    "deteccion": "test: guardar estado con revision vieja retorna StateConflictError"
  },
  {
    "fallo": "GraphRepository no resuelve imports del workflow.json modular (import.nodes, import.edges) y retorna grafo vacio",
    "causa_raiz": "workflow.json real usa formato modular, no inline nodes/edges",
    "mitigacion": "GraphRepository implementa _resolve_imports + _build_graph (port completo del legacy)",
    "deteccion": "test: load workflow.json con import section retorna grafo con nodos y edges"
  },
  {
    "fallo": "StateRepository.load() retorna estado con sub_state_index inconsistente con current_sub_state",
    "causa_raiz": "archivos .workflow-state.json del legacy pueden tener inconsistencias",
    "mitigacion": "StateRepository reconcilia sub_state_index en load (GAP-1 fix)",
    "deteccion": "test: load estado con index desincronizado corrige automaticamente"
  },
  {
    "fallo": "WorkflowState.from_dict falla al cargar estado del disco (formato legacy v4)",
    "causa_raiz": "archivos .workflow-state.json del mcp/ usan formato v4, no v5",
    "mitigacion": "from_dict acepta campos faltantes con defaults; roundtrip test verifica fidelidad",
    "deteccion": "test de roundtrip: to_dict -> from_dict -> to_dict produce mismo output"
  },
  {
    "fallo": "Handler de submit lanza excepcion y FastMCP la propaga como error crudo al agente",
    "causa_raiz": "dispatch() no captura excepciones del handler",
    "mitigacion": "dispatch() envuelve cada handler en try/except; retorna {error: str(e)}",
    "deteccion": "test: handler que lanza retorna dict con 'error' key, no exception"
  },
  {
    "fallo": "OrchestrationService es god object (645 LOC) portado sin simplificar",
    "causa_raiz": "port as-is de 4 sub-orchestrators + 6 use cases",
    "mitigacion": "colapsado a 509 LOC: submit/transition/status/help son metodos, no clases separadas",
    "deteccion": "metrica: OrchestrationService < 550 LOC"
  },
  {
    "fallo": "NoopTracer permite side effects a ejecutarse en produccion",
    "causa_raiz": "stub no compensa; side effects reales se ejecutan sin compensacion",
    "mitigacion": "NoOpSideEffectEngine retorna Result.success con side_effect_id generado pero no ejecuta nada",
    "deteccion": "test: side_effects.run() retorna success sin efectos colaterales"
  },
  {
    "fallo": "Atomic write en StateRepository falla y deja archivo .tmp huérfano",
    "causa_raiz": "crash durante os.replace() o permisos insuficientes",
    "mitigacion": "write a .tmp + os.replace() es atomico en POSIX; .tmp se limpia en next load",
    "deteccion": "test: simular crash despues de write verifica que estado original no se corrompe"
  },
  {
    "fallo": "Circular import entre workflow/ y project/ al resolver IProjectContext",
    "causa_raiz": "workflow/__init__.py importa project/ al construir el bundle",
    "mitigacion": "IProjectContext se inyecta via composition.py; workflow/ solo conoce el puerto",
    "deteccion": "test de autonomia: workflow/ no importa project/ directamente"
  },
  {
    "fallo": "Phase 3 tools no se registran porque composition.py falla al construir WorkflowContext",
    "causa_raiz": "algun adapter lanza en __init__ (filesystem no disponible)",
    "mitigacion": "fail-open: phase3_tasks.py envuelve install() en try/except; Phase 1+2 siguen",
    "deteccion": "test: server arranca con 17+ tools aunque Phase 3 falle"
  }
]
```

## 6. Estado / aprobación

**Fase 3 COMPLETADA. Pendiente Fase 4 (cierre).**
- ✅ Move a `mcp/` commiteado como rename (commit `395729b`, 553 renames); `mcp/sqlite:` eliminado.
- ✅ FastMCP verificado disponible (`mcp==1.23.3`, `mcp.server.fastmcp.FastMCP`).
- ✅ Server actual mapeado (McpServer propio + `_call_tool` dispatch + `_build_server`).
- ✅ Decisión de schema: `inputSchema` SSOT directo en `Tool.parameters` (R-03bis).
- ✅ Decisión de layout: paquete **`fast_mcp/`** (renombrado de `fast-mcp/` — imp. nombre guión no es importable).
- ✅ **Fase 0 (skeleton)**: `fast_mcp/server.py::build_server()` + `main.py` + `phases/`
  + health tool. **5 tests verdes** (`tests/test_fast_mcp_migration_bdd.py`).
- ✅ Fix `pythonpath = ["mcp", "."]` en `pyproject.toml` (la suite no resolvía tras el move).
- ⚠️ **Bug preexistente revelado** (no de esta migración): `_canonical_system_registry` en
  `tool_catalog` promete fail-open (convention-001) pero **lanza** `ValueError: task_registry
  no inyectado` cuando el registry inyectado no expone el `task_registry` como servicio DI.
  Quedó visible al arreglar el path (`test_tool_catalog_bdd.py`). **Impacta la validación
  de list_tools de las fases 1-3** → resolver en Fase 1 (fail-open cuando no hay task_registry).
- ✅ **Fase 1 ACTIVA y AUTÓNOMA (REFACTORIZADA tras violación detectada)**: las **3
  tools del workflow core** (`prepare` / `list` / `status` — título §3 Fase 1)
  **VIVEN dentro de `fast_mcp/`** (`fast_mcp/core.py` + `fast_mcp/use_cases.py`,
  stdlib only) — portadas del legacy con el MISMO contrato de datos
  (`data/projects.json`, `project.json`, `.workflow-state.json`). **CERO imports
  de `mcp/`** y **`mcp/` NO está en sys.path** (ni en `server.py` ni en
  `bin/walker-mcp`). El audit de test verifica a nivel AST y de `sys.modules`
  que no se carga ni un módulo de `mcp/`.
  **Decisión de alcance (usuario): EXACTAMENTE 3 tools** — sin `walker_ping`
  (artefacto del skeleton Fase 0, eliminado) y sin `submit`/`transition`/`help`
  (pertenecen a Fase 3; su port se hará ahí). El test
  `test_server_expone_exactamente_las_3_tools_del_adr` blindo la superficie
  exacta (igualdad de conjuntos, no inclusión).
  La primera implementación (oct-2026) era un **shim** que delegaba en
  `AppContainer.build()`/`execute_public_task` con `mcp/` en sys.path — cargaba
  18 módulos `protocols.*` en runtime; **violaba §0 #4/#5** ("sin shims",
  "sin mcp/ en sys.path") y fue reemplazada por el port real.
  Registro global migrado a su hogar final `data/projects.json` (guard
  idempotente en `test_registry_migration_bdd.py`; el legacy lo tenía bajo
  `mcp/data/` porque su walker_root se resolvía desde dentro de `mcp/`).
  Capa de test: 7 tests en `tests/test_fast_mcp_migration_bdd.py` + stdio/protocolo
  (`test_fast_mcp_stdio_validation_bdd.py`, `test_fast_mcp_protocol_session_bdd.py`,
  `test_registry_migration_bdd.py`).
- ✅ **Entry-point standalone**: `bin/walker-mcp` → `fast_mcp/main.py::run(stdio)`
  — solo añade la raíz de walker a sys.path (para `fast_mcp.*`), nunca `mcp/`.
- ✅ **Registrado y validado en opencode**: el MCP `walker` apunta a
  `bin/walker-mcp`; el agente ejecutó `walker_list` (7 proyectos) y
  `walker_status` (health de walker) por MCP real.
- ✅ **Decisión de escalamiento (usuario): Opción A — port TOTAL a `fast_mcp/`**,
  ejecutado por fases (§4). Arquitectura objetivo y reglas de coherencia: §3bis
  (bounded contexts + composition explícita + import-linter; NO se porta el DI
  plumbing de mcp/ — ~1.7k LOC de service locator/manifests). Benchmark de MCPs
  comparables (Serena, Task Master, Memory MCP, Code-Index) y presupuesto de
  contexto medido (31 tools ≈ 7.8k tokens) en §3bis.
- ✅ **Fase 2 COMPLETADA + CLEAN DESIGN REFACTOR**: port de memorias CKG/CPG a
  `fast_mcp/` + refactor de arquitectura limpia.
  `fast_mcp/features/cognitive_knowledge/` (domain + application + infrastructure)
  + `fast_mcp/features/code_analysis/` (ya existente) + `fast_mcp/shared_kernel/cognitive_graph/`
  (ya existente). **14 tools de memorias** registradas en `phase2_memories.py` con
  inputSchema declarativo (R-03bis). `composition.py` extendido con
  `memory_tool_runner()` + handler registry (`fast_mcp/features/memories/`).
  **Service split**: CkgWriteService (182L) + CkgReadService (199L) + CkgExtractService
  (201L) + ckg_common.py (56L), facade thin (135L). **Dead code removed**: EventBus,
  IKnowledgeQueryService, 6 trivial use cases, save_knowledge, ckg_application_service factory.
  **LoD fix**: 0 violations (antes 3 handlers accedían a `ckg._repo._cg.*`).
  **32 tests verdes** (10 migration + 13 phase2 + 9 handler registry). Server arranca
  con **17 tools** (3 Fase 1 + 14 Fase 2). Cero imports de `mcp/` en handlers (audit AST).
  Pendiente: stale checks en `status`, port completo de `task_intelligence` para `agent_context`.
- ✅ **Fase 3 COMPLETADA (4 Sprints, domain-first)**: port del workflow engine a
  `fast_mcp/features/workflow/` (domain + application + infrastructure + handlers).
  **Sprint 1 — Domain**: 1,190 LOC (de 4,982, -76%), 14 archivos, 8 puertos
  (de 16, -50%), 4 domain services (de 9, -56%). Eliminados: contract_validator,
  graph_analyzer, session, workflow_action, 5 domain services.
  **Sprint 2 — Application**: 656 LOC (de 4,491, -85%), 4 archivos (de 18, -78%).
  OrchestrationService colapsado de 4 sub-orchestrators + 6 use cases a 1 clase
  con métodos submit/transition/status/help. Eliminados: 6 use cases, 4 sub-orchestrators,
  step_orchestrator, error_handler, prompt_service, event_dispatch_service, side_effect_saga,
  context_resolver, workflow_api.
  **Sprint 3 — Infrastructure**: 550 LOC (de 1,180, -53%), 7 archivos (de 12, -42%).
  NoopTracer (70L), NoOpSideEffectEngine (29L), DefaultWorkflowIO (68L),
  GraphRepository (216L — import resolution + validación), StateRepository (131L — CAS +
  reconciliación + atomic write), WorkflowStoreAdapter (31L).
  **Sprint 4 — Handlers + Composition**: 271 LOC, 3 archivos nuevos + 2 editados.
  WorkflowContext (106L), handlers/workflow.py (85L — 5 handlers), handlers/__init__.py
  (80L — registry + dispatch). composition.py extendido con workflow_tool_runner().
  phase3_tasks.py registra 4 tools (submit, transition, help, list_projects) con
  inputSchema declarativo. `status` se mantiene en Phase 1 (evita conflicto de registro).
  **Tools añadidas por Fase 3 (4):** `submit` `transition` `help` `list_projects`.
  **152 tests verdes** (52 domain + 32 application + 23 infrastructure + 26 handlers).
  **2,684 LOC** en `fast_mcp/features/workflow/`. Zero imports de `mcp/`.
  **CAS (optimistic concurrency)** en StateRepository: check revision + increment en save.
  **Reconciliación** en StateRepository.load(): corrige sub_state_index ↔ current_sub_state.
  **Import resolution** en GraphRepository: carga workflow.json modular (nodes + edges files).
  **Atomic write** en StateRepository: write a .tmp + os.replace() (no corrupción en crash).
  **Sprint 2 orchestrator actualizado**: eliminado `_bump_revision()` (CAS movido a repository).
  **Deferred a Sprint 5**: delta_tracking (6,428L), event_tracing (1,324L), shared/tasks
  (6,643L), sprint/planning tools, ejecución tools, intelligence tools.
  Capa de test: 4 archivos de tests (`workflow_domain_bdd` 52, `workflow_application_bdd` 32,
  `workflow_infrastructure_bdd` 23, `workflow_handlers_bdd` 26).
- ⏳ Fase 4 (cierre): eliminar `mcp/` por completo.
`failure_scenarios` presente. Pendiente de aprobar.

## 7. Cross-references

- ADR-070: propuesta lee-vs-transforma (base conceptual de esta migración).
- ADR-069 §8: estudio del ecosistema MCP real (FastMCP, mcp_shared, dominio-centro).
- ADR-016: bounded context `cli_interface` (a actualizar al final).
- ADR-045/062: composition root y puertos (FastMCP se cablea en main.py).
- ADR-067: engine anémico — el dominio no importa superficies.
- ADR-069 T4: `app/path_resolver` puede subir a `shared/` como utilidad pura.
- docs/DESIGN-PHASE3-workflow-engine.md: diseño v2 de Fase 3 (10 fixes aplicados).
- docs/DESIGN-SPRINT2-application-layer.md: diseño de Sprint 2 (application layer).
- docs/DESIGN-SPRINT3-infrastructure-adapters.md: diseño de Sprint 3 (infrastructure).
- docs/DESIGN-SPRINT4-handlers-composition.md: diseño de Sprint 4 (handlers + composition).
- SDK oficial: `mcp/server/fastmcp/` (`mcp==1.23.3`) — `FastMCP.add_tool`/`@tool`/`resource`,
  `Tool.parameters` (schema servido al agente), `Tool.from_function` (infiere schema de la firma).
- `fast_mcp/`: paquete nuevo de la migración (`server.py::build_server`, `phases/`);
  fuente transitoria `mcp/` (imports sin prefijo `mcp.`, requiere `mcp/` en sys.path).
