# ADR-WALKER-065: Tasks como workflow actions y MCP/CLI como protocolos de consumo (Command/Handler Bus)

- **Status:** Aprobado
- **Aprobado:** 2026-08-28 (ritual de revisión: `failure_scenarios` FS-1..FS-5, métricas por fase M-01..M-06 y checklist de segundo orden presentes)
- **Fecha:** 2026-08-27
- **Autor:** Walker (agente) + PO
- **Complementa:** ADR-WALKER-060 (NestJS-inspired: guards/interceptors/módulos), ADR-WALKER-051 (gaps de responsabilidades), ADR-WALKER-050 (boundaries de features/main), ADR-WALKER-045 (composition root único), ADR-WALKER-062 (id+contrato por resolve_locator), ADR-WALKER-055 (primitivas compartidas R1/R4)
- **Amplía:** el ADR-060 en el plano **estructural**: el 060 adoptó patrones NestJS sin tocar la ubicación física. Este ADR resuelve *"¿dónde viven las tasks, los protocolos y las librerías?"* bajo el mismo modelo de 5 capas.

---

## 1. Problema

Después del pase composition root → `app/` (ADRs 045/062), `features/main/` quedó como feature orquestador mezclando **protocolos** (delivery) y **capa de aplicación** (tasks, prompts, facade, ports). Paralelamente, `shared/core/` mezcla el **motor** (`engine/`) con **librerías genéricas** (`mcp_server.py`, `context_resolver.py`, `projection_store.py`, `cognitive_searcher.py`) que no son dominio.

### 1.1 Inventario de lo mal ubicado

**En `features/main/` (mezcla protocolo + aplicación):**

| Bloque | Qué es realmente | Dónde vive (decisión) |
|---|---|---|
| `task_execution/` (57 py, 6.5k líneas) | Capa de aplicación: workflow actions | **`tasks/`** (raíz, hermano de `features/`) |
| `task_execution/ports/cpg_analysis_port.py` | Contrato DIP (`ICpgAnalysisPort`) | **`shared/core/engine/domain/ports.py`** (contrato del motor) |
| `prompts/` (9 py, 1.4k líneas) | Cross-cutting: implementa `IPromptCollector` + templates; **glue de orquestación** (importa `features`/`app`/`engine.infrastructure`) | **`features/main/prompts/`** (orquestador; NO lib) |
| `api/workflow_facade.py` | Orquestación submit/transition (app) | **`app/`** |
| `engine_ports.py` (4 adaptadores) | Cableado fase 5 | **`app/`** → disuelto en ADR-066 D4 (adaptadores ahora en `features/*/module.py`) |
| `current_app.py` | Vestigio deprecado (ADR-050 Fase 6) | **Eliminar** |
| `cli_interface/`, `mcp_adapter.py`, `front_controller.py` | Delivery puro (protocolos) | **`protocols/`** (raíz, hermano de `features/`) |

**En `shared/core/` (motor mezclado con libs):**

| Archivo | Qué es realmente | Dónde vive (decisión) |
|---|---|---|
| `mcp_server.py` | Transporte MCP (router JSON-RPC) — *"libs, infra transversal… NO importa features"* (su propio docstring) | **`protocols/mcp/`** (junto a `mcp_adapter.py`) — NO es lib |
| `context_resolver.py` | Primitiva neutra R1: resuelve `project_id/repo_root/data_dir`, *"capa neutral — sin imports de features"* | **`shared/libs/`** |
| `projection_store.py` | Primitiva neutra R4: I/O de proyecciones JSON | **`shared/libs/`** |
| `cognitive_searcher.py` | Adapter CQRS-read del grafo — *"shared/infrastructure/cognitive.py… el CognitiveGraph se INYECTA"* | **`shared/libs/cognitive_graph/searcher.py`** |

### 1.2 El reencuadre conceptual

Las **tasks no son de los features**: son un concepto del workflow (workflow actions, SPEC-014/ADR-049). Un feature define Application Services y puertos; las tasks **orquestan** esos services por nombre. MCP y CLI no son "features": son **protocolos de consumo** del mismo catálogo. Tres consumidores → **un solo registro**:

```
CLI (argv) ──────────┐
MCP (JSON-RPC 2.0) ──┤→ translate protocol → TaskRegistry.execute(name, ctx, data)
Workflow (.action) ──┘        │
                              ▼
                    ActionCatalog[name] → handler (tasks/)
                              │ consume Application Services
                              ▼
                    features/{code_analysis, delta_tracking, ...}
```

### 1.3 Evidencia en el código actual

- El contrato ya vive en el dominio correcto: `shared/core/engine/domain/registry.py` (`TaskRegistry` ABC) + `workflow_action.py` (`WorkflowAction` + `ActionCatalog`). `shared/core/engine/domain/ports.py` ya tiene `IPromptCollector`.
- MCP ya exporta tasks vía `shared/core/engine/application/tool_catalog.list_tools()` — el bus ya es la fuente de tools.
- `features/delta_tracking/tasks/{implementation,review,verification}.py` son **shims ADR-051 G2** que re-exportan desde `features.main.task_execution.tasks.*` → features de dominio importando main.
- `features/code_analysis/infrastructure/adapters/walker_cpg_adapter.py` importa `features.main.task_execution.ports.cpg_analysis_port` → puerto de dominio anidado en main.
- `shared/core/engine/infrastructure/side_effects.py:526` despacha handlers por la ruta `features/main/task_execution/side_effects/`.
- `shared/core/mcp_server.py` recibe un `ToolRegistry` de `features/cli_interface/domain/services.py` (docstring) → acoplado a la capa de protocolo, no al motor.

---

## 2. Decisión

**Adoptar el patrón Command/Handler Bus (CQRS-lite) con un layout de 5 capas donde `shared/` es el paraguas de código compartido (motor + librerías):**

```
walker/
├── app/                          # composition root (wiring only) — workflow_facade.py (+ module.py); engine_ports.py disuelto en ADR-066 D4
├── shared/
│   ├── core/engine/              # MOTOR (workflow domain) — SOLO el motor
│   │   ├── domain/               #   registry.py, workflow_action.py, state_machine, gates, ports.py
│   │   ├── application/tool_catalog.py   # list_tools() = catálogo derivado del bus
│   │   └── infrastructure/       #   in_memory_task_registry, side_effects dispatcher
│   └── libs/                     # LIBRERÍAS genéricas (sin dueño de dominio, sin importar features/app)
│       ├── cognitive_graph/      #   storage CPG/CKG/Delta/Trace + searcher.py
│       ├── context/              #   context models
│       ├── router/               #   REST router
│       ├── context_resolver.py   #   primitiva R1
│       └── projection_store.py   #   primitiva R4
├── tasks/                        # WORKFLOW ACTIONS (capa aplicación, hermano de features/)
│   ├── bootstrap.py → register_all(registry)
│   ├── tasks/  side_effects/  application/  infrastructure/  context/
├── features/                     # BOUNDED CONTEXTS (dominio + App Services) — SOLO estos
│   ├── code_analysis/            #   infrastructure/adapters implementa ICpgAnalysisPort
│   ├── delta_tracking/
│   ├── cognitive_knowledge/      #   domain sobre shared/libs/cognitive_graph
│   ├── event_tracing/
│   └── project/
└── protocols/                    # INPUT ADAPTERS (hermano de features/)
    ├── mcp/  → mcp_adapter.py + mcp_server.py (transporte)
    ├── cli/  → cli_interface/
    └── rest/ → front_controller.py
```

### D1 — `features/main/task_execution/` → `tasks/` (raíz)

Las tasks son la capa de aplicación del workflow (legítimamente importan features: orquestan sus Application Services). El motor (`shared/core/engine`) tiene prohibido importar features — las tasks no son del motor, son la **capa intermedia** que el motor invoca por nombre. `task_execution/ports/cpg_analysis_port.py` se mueve a `shared/core/engine/domain/ports.py` (el contrato DIP vive con el motor, no con el consumidor — cierra ADR-051 G11).

Efectos:
- `features/delta_tracking/tasks/*.py` (shims ADR-051 G2) → import directo a `tasks.*` y **eliminar los shims**.
- `features/code_analysis/.../walker_cpg_adapter.py` → `from shared.core.engine.domain.ports import ICpgAnalysisPort`.
- `shared/core/engine/infrastructure/side_effects.py:526` → ruta `tasks/side_effects/`.

### D2 — `features/main/prompts/` permanece en `features/main/` (REVERTIDO F2)

`prompts` implementa `IPromptCollector` (puerto del motor) y lo consumen motor + app + mcp, **pero** su código es *glue de orquestación*: `collector.py` importa `features.project.ProjectService`, `app.bootstrap._build_searcher` y `app.container.AppContainer`; `protocol_builder.py` importa `app.container.AppContainer`; `loader.py` importa `features.project.ProjectService`; `manager.py` importa `shared.core.engine.infrastructure.config`. Esas importaciones hacia arriba violan D8 (la capa más baja no puede importar `features`/`app`/`engine.infrastructure`). Al moverlo a `shared/libs/` se rompía la regla, así que **F2 se revirtió**: `prompts` queda en `features/main/prompts/` como parte del orquestador, donde acoplarse a `app`/`features` es aceptable. No es un bounded context de dominio ni una librería neutral.

### D3 — `features/main/engine_ports.py` → `app/engine_ports.py`

Adaptadores de puertos del engine delegando a features. Solo los importa `app/bootstrap.py:420` → cableado del composition root (ADR-045).

### D4 — `features/main/api/workflow_facade.py` → `app/workflow_facade.py` + unificar duplicado

`WorkflowFacade` es capa de aplicación consumida solo por el CR. **Deuda:** `_build_workflow_facade` duplicado (docstring idéntico) en `app/bootstrap.py:320` y `app/composition/registry.py:576`. Al mover, unificar en la de `app/composition/registry.py` como canónica; `app/bootstrap.py` delega.

### D5 — Eliminar `features/main/current_app.py`

Deprecado en su propio docstring. Solo lo usan `features/main/__init__.py` y `tests/test_container_lifetime_bdd.py:114-119` → ajustar ambos a `AppContainer`.

### D6 — Regla de transporte (reafirma ADR-051 G7)

**MCP y CLI son protocolos, no features.** Solo `protocols/` traduce protocolo → bus; los bounded contexts (`features/*`) exportan use cases/services, nunca tools MCP ni rutas REST. El catálogo MCP/CLI es vista **derivada** de `TaskRegistry` (vía `tool_catalog.list_tools()`).

### D7 — `shared/core/*.py` no-motor → `shared/libs/`; `mcp_server.py` → `protocols/mcp/`

- `context_resolver.py`, `projection_store.py` → `shared/libs/` (primitivas neutras R1/R4).
- `cognitive_searcher.py` → `shared/libs/cognitive_graph/searcher.py` (adapter CQRS-read del grafo).
- `mcp_server.py` NO es lib: es el transporte MCP acoplado al `ToolRegistry` de la capa protocolo → `protocols/mcp/` junto a `mcp_adapter.py`.

### D8 — Regla de acyclicidad de `shared/`

`shared/core/` queda = **solo `engine/`**. `shared/libs/` es la capa más baja junto al motor: **no importa** `features/`, `app/`, ni `shared/core/engine/infrastructure/` (sí `shared/core/engine/domain/`). Cualquier cosa en `shared/core/` que no sea `engine/` es sospechosa y debe ir a `shared/libs/`.

---

## 3. Alternativas evaluadas

| Alternativa | Pros | Contras | Decisión |
|---|---|---|---|
| **A. Libs en raíz `libs/`** | Más visible como capa top | `shared/` ya es el paraguas de compartidos; añade una raíz más sin ganancia | Rechazada (D7/D8) |
| **B. Tasks/protocols anidados en `features/`** (ADR-065 v0) | Menor churn | Genera 3 paths `features/` distintos y `features/` deja de significar "bounded context" | Rechazada |
| **C. Tasks en cada feature** | Cercanía al dominio | Tasks multi-feature sin dueño; MCP/CLI deben agregar catálogos; era el anti-patrón ADR-051 G1-G2 | Rechazada |
| **D. Capa de aplicación peer + bus único + libs en shared** (elegida) | Un registro, 3 consumidores, `shared/` como paraguas limpio, `features/` semánticamente preciso | Mover ~60 archivos + reescribir imports | **Aceptada** |

---

## 4. failure_scenarios

**FS-1 — Shims ADR-051 mal eliminados rompen imports legacy**
- **fallo:** un consumidor externo sigue importando `features.main.task_execution.*` tras la migración y revienta en runtime.
- **causa raíz:** reescribir solo los importers conocidos (grep) sin rastrear paths viejos.
- **mitigación:** grep completo de `features.main.task_execution` antes y después; el final debe dar 0 (salvo docstrings históricas). `features.main.prompts` es válido (vive en el orquestador).
- **detección:** `grep -rn 'features\.main\.task_execution'` en CI + arranque `bin/walker` + self-test MCP post-migración.

**FS-2 — El workflow pierde tasks por nombre al mover `register_all`**
- **fallo:** el grafo ejecuta `.action` pero el registro nuevo no puebla el bus → `TaskNotFound`.
- **causa raíz:** `register_all` ya no se invoca desde el composition root tras el movimiento a `tasks/`.
- **mitigación:** el CR es el único caller de `register_all`; test de completitud del registry con EXPECTED-set (patrón AGENTS.md) que valide `implement`, `verify`, `code_search`, etc.
- **detección:** `test_container_completeness_bdd.py` ampliado + smoke `bin/walker routes`.

**FS-3 — Regla de layering rota tras el `git mv`**
- **fallo:** tras mover `task_execution` → `tasks/`, algún archivo importa `protocols/` o `features/main/` (delivery) y el grafo de acyclicidad detecta ciclo dominio→delivery.
- **causa raíz:** mover por `git mv` sin re-auditar imports internos.
- **mitigación:** `test_import_acyclicity.py` (escanea `app.*` + `features.*`) + añadir regla `shared/libs/` no importa `features/`,`app/`,`shared/core/engine/infrastructure/`.
- **detección:** test de acyclicidad + grep de imports dominio→delivery.

**FS-4 — `mcp_server.py` desubicado rompe el arranque MCP**
- **fallo:** al mover `mcp_server.py` de `shared/core/` a `protocols/mcp/`, el import en `mcp_adapter.py` (o `ToolRegistry` de `cli_interface/domain/services.py`) queda roto.
- **causa raíz:** `mcp_server.py` depende del `ToolRegistry` de la capa protocolo; moverlo a `shared/libs/` (como lib) rompería la regla D8 (lib no debe importar protocolo).
- **mitigación:** moverlo a `protocols/mcp/` (no a `shared/libs/`) y validar que `mcp_adapter.py` lo resuelve.
- **detección:** `python -m protocols.cli.cli_interface.mcp --self-test` (31 tools) post-migración.

**FS-5 — `ICpgAnalysisPort` duplicado tras mover el puerto**
- **fallo:** el puerto queda tanto en `tasks/ports/` como en `shared/core/engine/domain/ports.py` → import circular o shadowing.
- **causa raíz:** mover el puerto al motor pero no borrar el original en `tasks/ports/`.
- **mitigación:** eliminar `tasks/ports/cpg_analysis_port.py` y actualizar `walker_cpg_adapter.py` a `shared.core.engine.domain.ports`.
- **detección:** grep `cpg_analysis_port` devuelve 1 definición en `shared/core/engine/domain/ports.py`.

---

## 5. Checklist de segundo orden

| Pregunta | Respuesta |
|---|---|
| ¿Qué módulos importan esto? | `app/bootstrap.py`, `app/composition/registry.py`, `protocols/{cli,rest,mcp}`, `features/delta_tracking/tasks/*` (shims), `features/code_analysis/.../walker_cpg_adapter.py`, `shared/core/engine/infrastructure/side_effects.py`, tests |
| ¿Qué side effects dispara? | `git mv` + reescritura de imports; eliminación de shims ADR-051; unificación de fábrica duplicada; borrado de `current_app.py`; split `shared/core` → `shared/libs` |
| ¿Qué eventos de dominio publica? | Ninguno nuevo: el bus (TaskRegistry/ActionCatalog) ya es la fuente; MCP/CLI derivan de `tool_catalog` |
| ¿Qué tests dependen? | `test_import_acyclicity.py`, `test_container_completeness_bdd.py`, `test_container_lifetime_bdd.py` (current_app), tests de tasks y MCP/CLI |
| ¿Qué cambia en MCP, CLI, CKG, Delta, Trace? | MCP/CLI: sin cambio funcional (mismo bus); CKG/Delta/Trace: sin cambio (resuelven por `resolve_locator`); cambian rutas de import y `shared/libs/` gana las primitivas neutras |

---

## 6. Migración

| Fase | Acción | Esfuerzo |
|---|---|---|
| **F1** | `git mv features/main/task_execution` → `tasks/` + sed `features.main.task_execution`→`tasks`; mover `task_execution/ports/cpg_analysis_port.py` → `shared/core/engine/domain/ports.py` | 2-3h + validación |
| **F2** | **REVERTIDO (2026-08-28):** `prompts` NO se mueve; permanece en `features/main/prompts/` (glue de orquestación; violaría D8 como lib). La migración original F2 se deshizo. | 0 (ya estaba en `features/main/`) |
| **F3** | `git mv` de `engine_ports.py` + `api/workflow_facade.py` → `app/`; unificar `_build_workflow_facade` | 30min |
| **F4** | Eliminar `current_app.py`; ajustar `features/main/__init__.py` y tests | 30min |
| **F5** | `git mv` de `shared/core/context_resolver.py`, `projection_store.py` → `shared/libs/`; `cognitive_searcher.py` → `shared/libs/cognitive_graph/searcher.py`; `mcp_server.py` → `protocols/mcp/` | 1h |
| **F6** | Ampliar test de completitud del registry; añadir regla `shared/libs/` al test de acyclicidad; validar routes + self-test MCP | 1h |
| **F7** | `git mv` de `features/main/cli_interface` → `protocols/cli/cli_interface`; `features/main/mcp_adapter.py` → `protocols/mcp/mcp_adapter.py`; `features/main/front_controller.py` → `protocols/rest/front_controller.py` + sed de imports (cierra gap del diagrama §2) | 1h |

**Estado tras la migración:**

```
walker/
├── app/                              # composition root — workflow_facade.py; engine_ports.py disuelto en ADR-066 D4
├── shared/
│   ├── core/engine/                  # MOTOR (dominio puro, SOLO engine/)
│   └── libs/                         # LIBRERÍAS genéricas (context_resolver, projection_store, cognitive_graph, context, router)
├── tasks/                            # WORKFLOW ACTIONS (register_all en bootstrap.py)
├── features/                         # BOUNDED CONTEXTS (code_analysis, delta_tracking, cognitive_knowledge, event_tracing, project)
└── protocols/                        # INPUT ADAPTERS (mcp/ con mcp_adapter+mcp_server, cli/, rest/)

> **Nota (post-ADR-066 D4):** `app/engine_ports.py` fue disuelto; los 4 adaptadores de puerto (proyecto/delta/sprint/prompts) ahora se declaran en `ModuleManifest` (`features/{project,delta_tracking,main/prompts}/module.py`) y se resuelven con `module_registry.resolve_port(...)`.
```

---

### Métricas de calidad por fase (obligatorio, AGENTS.md)

Cada fase declara ≥3 métricas aplicables (M-01..M-06). Las no listadas por una fase
se consideran **no aplicables a esa fase** (reasignadas a la fase que sí las toca); no se omiten en silencio.
Formato: `- [ ] M-0X <criterio medible para esta fase> — anti-patrón a evitar: <...>`.

**F1 — `task_execution` → `tasks/` + puerto al motor**
- [ ] M-01 (escritura): cada `git mv` es atómico por módulo y se valida (grep 0 de la ruta vieja) antes de avanzar al siguiente — anti-patrón a evitar: Transaction Script (moves dispersos sin validación intermedia).
- [ ] M-03 (dominio): `ICpgAnalysisPort` vive en `shared/core/engine/domain/ports.py` (una responsabilidad: contrato DIP del motor), sin duplicado en `tasks/ports/` — anti-patrón a evitar: Anemic Domain Model (puerto sin dueño) / Duplicate Code.
- [ ] M-06 (configuración): imports por id+contrato (`resolve_locator`), sin rutas hardcodeadas a `features.main` — anti-patrón a evitar: Accidental Complexity.
**F2 — `prompts` permanece en `features/main/prompts/` (no es lib, F2 revertido)**

- [ ] M-02 (estrategias): `IPromptCollector` es SSOT del collector; sin re-implementaciones dispersas — anti-patrón a evitar: Shotgun Surgery.
- [ ] M-03 (dominio): prompts como contenido + collector, con una sola impl, consumido por motor+app+mcp — anti-patrón a evitar: Data Clumps (templates duplicados).
- [ ] M-06 (configuración): `features.main.prompts` acoplado a `app`/`features` es **aceptable** (es el orquestador); D8 aplica solo a `shared/libs/` — anti-patrón a evitar: Not Invented Here (forzar lib a lo que es glue).

**F3 — `engine_ports`/`workflow_facade` → `app/` + unificar fábrica**
- [ ] M-02 (estrategias): adapters en `app/` (composition root) es SSOT de cableado; sin wiring en features — anti-patrón a evitar: Shotgun Surgery.
- [ ] M-03 (dominio): `WorkflowFacade` única instancia exportada como servicio por `get_service` — anti-patrón a evitar: God Class (facade duplicada).
- [ ] M-05 (concurrencia): `_build_workflow_facade` unificada en un único punto canónico — anti-patrón a evitar: Magic Numbers (factory dispersa).

**F4 — eliminar `current_app.py`**
- [ ] M-01 (escritura): borrado atómico + ajuste de `features/main/__init__.py` y tests en el mismo commit — anti-patrón a evitar: Hidden Dependency (imports colgando).
- [ ] M-03 (dominio): `AppContainer` es el único contenedor (una responsabilidad) — anti-patrón a evitar: Anemic Domain Model.
- [ ] M-06 (configuración): sin referencias a `current_app` en config — anti-patrón a evitar: Accidental Complexity.

**F5 — `shared/core/*.py` no-motor → `shared/libs/` + `mcp_server.py` → `protocols/mcp/`**
- [ ] M-02 (estrategias): `shared/libs/` es la única ubicación de primitivas neutras; precedencia clara sobre `shared/core` — anti-patrón a evitar: Duplicate Code.
- [ ] M-06 (configuración): `mcp_server.py` en `protocols/mcp/` resuelto por la capa protocolo, no como lib — anti-patrón a evitar: Not Invented Here (forzar lib).
- [ ] M-04 (eventos): `shared/libs/` no publica eventos de dominio; desacoplado de features — anti-patrón a evitar: Distributed Big Ball of Mud.

**F6 — completitud del registry + acyclicidad `shared/libs/`**
- [ ] M-01 (escritura): test de completitud con EXPECTED-set falla el build si falta provider — anti-patrón a evitar: Hidden Dependency.
- [ ] M-04 (eventos): acyclicidad del grafo de `imports` validada en arranque, no solo post-hoc — anti-patrón a evitar: Eventual Consistency sin safeguards (ciclos latentes).
- [ ] M-05 (concurrencia): regla `shared/libs/` no importa features/app validada automáticamente — anti-patrón a evitar: Magic Numbers (chequeos manuales).

## 7. Verificación

| Check | Resultado esperado |
|---|---|
| `grep -rn 'features\.main\.task_execution'` | 0 resultados (code); `features.main.prompts` es válido |
| `grep -rn 'cpg_analysis_port'` | 1 definición en `shared/core/engine/domain/ports.py` |
| `bin/walker routes` | 23 endpoints, exit 0 |
| `python -m protocols.cli.cli_interface.mcp --self-test` | 31 tools, exit 0 |
| `test_import_acyclicity.py` (SCC del grafo) | 0 ciclos; `shared/libs/` no importa `features/`/`app/`/`engine/infrastructure` |
| `_build_workflow_facade` | 1 única definición |
| `from features.main import AppContainer` (lazy) | sin eager hub |

---

## 8. Addendum — Revisión 2026-08-28 (F2 revertido)

Tras ejecutar F1–F6, el intento F2 de mover `prompts` a `shared/libs/` expuso que `prompts` **no es una librería neutral**: `collector.py`, `protocol_builder.py`, `loader.py` y `manager.py` importan estáticamente `features.project`, `app.bootstrap`, `app.container` y `shared.core.engine.infrastructure.*`. Eso viola D8 (la capa más baja no importa hacia arriba) y lo detectó el test F6 `test_shared_libs_sin_imports_hacia_arriba`.

**Decisión:** revertir F2. `prompts` permanece en `features/main/prompts/` como glue de orquestación, donde acoplarse a `app`/`features`/`engine.infrastructure` es aceptable. El diagrama de layout (§2) y la tabla de bloques se corrigen en consecuencia.

**Estado de la migración (rama `adr-065-f1-f6`):**
- F1 `task_execution` → `tasks/` ✓
- F2 revertido: `prompts` en `features/main/prompts/` ✓
- F3 `engine_ports`+`api/workflow_facade` → `app/` + `_build_workflow_facade` unificada ✓
  - F4 `current_app.py` eliminado ✓ (eliminado físicamente 2026-08-28; grep confirma 0 referencias externas; `features/main` expone `AppContainer` vía `__getattr__` perezoso a `app.container`)
- F5 libs → `shared/libs/`; `mcp_server` → `protocols/mcp/` ✓
- F6 test de acyclicidad `shared/libs/` añadido (ahora verde: `shared/libs` sin imports hacia arriba) + routes/self-test validados (31 tools) ✓
- F7 `cli_interface`→`protocols/cli/cli_interface`, `mcp_adapter`→`protocols/mcp/`, `front_controller`→`protocols/rest/` ✓
- D8 `shared/core` = SOLO engine: `cognitive_graph/`, `memories/`, `path_resolver/` movidos de `shared/core/` a `shared/libs/` (2026-08-28, ~74 sitios de import reescritos vía `shared.core.X`→`shared.libs.X`); `shared/core/` ahora contiene **únicamente `engine/`**. `shared/libs/cognitive_graph/` consolidó el `searcher.py` preexistente. Validado por `tests/test_import_acyclicity.py::test_shared_libs_sin_imports_hacia_arriba` (PASS) + `compileall` + `import shared.core.engine` + `AppContainer.build` + MCP self-test (31 tools).
- **Limpieza post-crítica (2026-08-28, cierra divergencias de la revisión de estructura):**
  - `libs/cognitive_graph/cognitive_registry.py` → **`app/cognitive_registry.py`**. No es una lib: importa `features.project` (violaba D8 al caer en `shared/libs/`). Al ubicarlo en el composition root se elimina también el `libs/` top-level duplicado.
  - `tasks/tasks/` → **`tasks/actions/`** (elimina nesting redundante `tasks.tasks`).
  - `orchestration/` → **`app/orchestration/`** (la 6ª capa queda documentada como orquestación cross-feature dentro del composition root; ya no es un dir top-level fantasma).
  - **P5 (ADR-066):** `ModuleManifest` extendido a `protocols/cli/cli_interface`, `protocols/rest` y `app/orchestration` (sus `module.py` se agregan a `module_paths` en `app/bootstrap.py::_register_module_manifests`). DI declarativa 100% de módulos. Validado: `AppContainer.build` + `test_import_acyclicity.py` (4/4).

**Gap conocido CERRADO (F7, 2026-08-28):** el diagrama objetivo colocaba `cli_interface/`, `mcp_adapter.py`, `front_controller.py` en `protocols/`. La fase F7 los movió a `protocols/cli/cli_interface`, `protocols/mcp/mcp_adapter.py` y `protocols/rest/front_controller.py`; ahora el diagrama (§2) y el código coinciden. El entry point MCP quedó en `protocols.cli.cli_interface.mcp` (validado: 31 tools).