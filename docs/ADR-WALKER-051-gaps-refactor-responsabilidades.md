# ADR-WALKER-051: Cierre de gaps del refactor arquitectónico por responsabilidades (ADR-048 + ADR-050)

> **Status:** Accepted
> **Fecha:** 2026-08-14 (refinado: 2026-08-15)
> **Autor:** Kilo (agente) + PO
> **Complementa:** ADR-WALKER-048b (`schemas/adrs/ADR-WALKER-048.json` — patrón canonical de capa de aplicación), ADR-WALKER-050 (boundaries de `features/main`), SPEC-011 (unificación de capa de aplicación), SPEC-012 (refinamiento de features/main), ADR-WALKER-018 (context gates), ADR-WALKER-049 (side-effect naming semántico)
> **Amplía:** la decisión del ADR-050 §2.1 ("cross-feature orchestrators: de main hoy, reutilizable futuro") — este ADR define la división objetivo de main (§2.6) y la extracción a `orchestration/` como el destino concreto de esa columna "futuro".
> **Contexto de creación:** durante la auditoría post-migración de los ADRs 048/050 se detectó que la migración fue **estructural pero no de responsabilidades**: la estructura de carpetas canonical existe, pero la capa de tasks sigue conteniendo lógica de negocio, los orchestrators importan Use Cases directos, y el sprint workflow quedó sin cablear. Posteriormente, la implementación de SPEC-014/Fase 4 reveló violaciones adicionales: features definiendo tools MCP directamente, nombres técnicos en herramientas públicas, y orquestación multi-feature inline en side-effects.

---

## 1. Problema

El refactor definido por ADR-048 (patrón canonical: Use Cases + Application Services + Domain Services) y ADR-050 (boundaries de main: composition root + orchestrators + tasks thin adapters) completó la migración **estructural** — los 5 features tienen `use_cases/` + `services/`, main tiene `composition/` + `api/` + `orchestrators/`, `cognitive_registry.py` se movió a `libs/` — pero una auditoría de consumo reveló **gaps que mantienen vivo el anti-patrón** que ambos ADRs pretenden eliminar.

**Síntoma central:** la migración movió archivos de lugar, no responsabilidades de capa. El caso testigo es `kg.py`: cumple la métrica `wc -l < 200` (20 líneas), pero la lógica simplemente se mudó a `kg_helpers.py` (361 líneas) **dentro de la misma capa de tasks**.

### 1.1 Inventario de gaps verificado en código

| # | Gap | Severidad | Evidencia |
|---|-----|-----------|-----------|
| G1 | Orchestrators importan Use Cases directos de features y engine (INV-013) | **CRÍTICA** | `orchestrators/prepare_project_service.py:109-113` y `orchestrators/check_health_service.py:28-32` |
| G2 | Tasks del sprint huérfanas: el workflow de sprint no tiene handlers registrados | **CRÍTICA** | `register_delta_tracking_tasks()` existe en `delta_tracking/tasks/__init__.py:10` pero **nadie lo invoca** |
| G3 | Extracción de kg cosmética: la lógica vive en `kg_helpers.py` dentro de `tasks/`, no en el Application Service | ALTA | `kg.py` = 20 líneas (✓ métrica) pero `kg_helpers.py` = 361 líneas en la capa de tasks; `agent_context` registrada 2 veces (`kg_metadata.py:146` + `kg_agent_context.py:142`) |
| G4 | Otras tasks no son thin adapters (lógica de negocio en la capa de tasks) | ALTA | `kg_extract.py` 513, `capture_task.py` 397, `project_init.py` 330, `ckg_analyze.py` 247, `phase_intelligence.py` 245 |
| G5 | `core/engine` importa `features.*` directo (inversión de dependencia) | MEDIA | `resolve.py`, `legacy/utils.py`, `engine.py`, `mcp.py`, `loader.py`, `task_dispatcher.py`, `prompt_context_builder.py`, `context/query_manager.py`, `context/composer.py` |
| G6 | Application Services de features no registrados como servicios DI | MEDIA | `composition/registry.py:_register_libs_services` registra infra (`task_registry`, `workflow`, `cognitive_searcher`, `project_service`, `delta_spring_service`…) pero no `SprintApplicationService`, `CkgApplicationService`, `TraceService` |
| G7 | Features definen tools MCP/REST directamente (`mcp.py`) | ALTA | `cognitive_knowledge/mcp.py`, `code_analysis/mcp.py`, `project/mcp.py`, `event_tracing/mcp.py` definen `ToolDefinition`/`RestRoute` — viola boundary de transporte |
| G8 | Nombres de tools/tasks/side-effects con conceptos técnicos (ckg_, cpg_, kg_, delta_) | ALTA | `cpg_analyze`, `ckg_analyze`, `save_to_ckg`, `build_initial_ckg`, `scan_code_and_build_cpg`, `kg_search`, `kg_multi`, `kg_extract`, `kg_task_context`, `kg_diagnose`, `kg_qa_evidence`, `kg_sprint_evidence`, `kg_schema`, `ckg_status`, `delete_ckg_nodes`, `index_delta`, `reindex_delta`, `restore_cpg_snapshot`, `sync_delta` |
| G9 | Tasks/side-effects acceden a infraestructura de main directamente | MEDIA | `tasks/verification.py` importa `verify_engine`; `tasks/workflow_tools.py` importa schemas de `infrastructure/`; `tasks/capture_task.py` importa `project_context`/`decision_resolution` de infraestructura |
| G10 | Side-effects orquestan múltiples features inline | ALTA | `side_effects/create_adr.py` importa `spring_service` + `ckg_service` + `ProjectService` directamente |
| G11 | Tasks acceden a puertos crudos de features en vez de Application Services | MEDIA | `cpg_analyze` y `review_code` usan `ICpgAnalysisPort` en vez de `CpgApplicationService` |
| G12 | Tags técnicos en metadata de tools expuestos al agente | BAJA | `cpg_analyze`: `["cpg", "unified", "analysis"]`, `kg_search`: `["kg", "cognitive-graph"]`, etc. |

---

## 2. Decisión

**Cerrar los 12 gaps para que el refactor de ADR-048/050/049 sea de responsabilidades y no solo de estructura: tasks como thin adapters reales, orchestrators consumiendo Application Services, sprint workflow cableado, engine con DI, Application Services registrados en el composition root, features sin tools MCP/REST, nombres de negocio en herramientas públicas, y main reducido a shell (composition + transporte) con los orchestrators extraídos a una capa de aplicación reutilizable `orchestration/` (§2.6).**

### 2.1 Regla de transporte MCP/REST (cierra G7)

**MCP y REST son mecanismos de transporte, no de dominio.** Solo `features/main` puede exponer tools MCP y rutas REST. Los bounded contexts (`features/*`) son módulos puros de dominio: exportan use cases/services, **nunca** tools ni rutas.

```python
# ANTES (violación):
# features/cognitive_knowledge/mcp.py
module = ModuleDefinition(
    name="cognitive_knowledge",
    tools=[ToolDefinition(name="save_to_ckg", ...)],
    routes=[RestRoute(path="{project_id}/save_to_ckg", ...)],
)

# DESPUÉS (correcto):
# features/cognitive_knowledge/application/services/ckg_service.py
class CkgApplicationService:
    def documenting_knowledge(self, ...): ...
    def search_knowledge(self, ...): ...

# features/main/task_execution/tasks/documenting_knowledge.py
# features/main/task_execution/tasks/search_knowledge.py
# features/main/cli_interface/mcp_adapter.py (único transporte MCP)
```

**Consecuencia:** eliminados `mcp.py` de todos los features (`cognitive_knowledge`, `code_analysis`, `project`, `event_tracing`, `delta_tracking`). El catálogo MCP se construye desde el `TaskRegistry` (tasks públicas de `features/main/task_execution/tasks/`) via `tool_catalog.py`.

### 2.2 Regla de naming semántico (cierra G8)

Los nombres de tools/tasks/side-effects deben usar **vocabulario de negocio**, nunca conceptos técnicos (`ckg_`, `cpg_`, `kg_`, `delta_`, `_cpg`, `_ckg`).

| Nombre técnico (ANTES) | Nombre semántico (DESPUÉS) | Concepto de negocio |
|------------------------|----------------------------|----------------------|
| `save_to_ckg` | `documenting_knowledge` | Guardar conocimiento |
| `ckg_analyze` | `analyze_knowledge` | Analizar conocimiento |
| `cpg_analyze` | `analyze_code_structure` | Analizar estructura de código |
| `kg_search` | `search_knowledge` | Buscar conocimiento |
| `kg_multi` | `search_memories` | Buscar en múltiples memorias |
| `kg_extract` | `extract_code_knowledge` | Extraer conocimiento de código |
| `kg_schema` | `memory_schema` | Esquema de memoria |
| `kg_task_context` | `task_code_context` | Contexto de código de tarea |
| `kg_diagnose` | `diagnose_failure` | Diagnosticar fallo |
| `kg_qa_evidence` | `verify_evidence` | Verificar evidencia QA |
| `kg_sprint_evidence` | `sprint_evidence` | Evidencia de sprint |
| `build_initial_ckg` | `initialize_knowledge_base` | Inicializar base de conocimiento |
| `scan_code_and_build_cpg` | `build_code_index` | Construir índice de código |
| `ckg_status` | `update_knowledge_status` | Actualizar estado de conocimiento |
| `delete_ckg_nodes` | `remove_knowledge` | Eliminar conocimiento |
| `index_delta` | `index_sprint_tasks` | Indexar tareas de sprint |
| `reindex_delta` | `rebuild_sprint_index` | Reconstruir índice de sprint |
| `restore_cpg_snapshot` | `restore_code_snapshot` | Restaurar snapshot de código |
| `sync_delta` | `sync_sprint_state` | Sincronizar estado de sprint |

**Tags técnicos:** reemplazar `["cpg", ...]`, `["kg", ...]`, `["delta", ...]` por tags de dominio (`["knowledge", "search"]`, `["code", "analysis"]`, `["sprint", "planning"]`).

### 2.3 Regla de consumo de Application Services (cierra G1, G9, G10, G11)

Tasks y side-effects consumen **Application Services** (o Domain Services) de features. Nunca importan use cases directos, nunca acceden a infraestructura de features o de main directamente.

```python
# ANTES (violación INV-013 + G9):
# tasks/verification.py
from features.main.task_execution.infrastructure.verify_engine import invalidate_verify_cache

# tasks/cpg_analyze.py
from features.code_analysis.application.ports import ICpgAnalysisPort

# side_effects/create_adr.py
from features.delta_tracking import spring_service
from features.cognitive_knowledge.application.services.ckg_service import CkgApplicationService
from features.project import ProjectService

# DESPUÉS (correcto):
# tasks/verification.py
from features.main.orchestration.verification_service import VerificationService

# tasks/cpg_analyze.py
from features.code_analysis.application.services.cpg_service import CpgApplicationService

# side_effects/create_adr.py
from features.main.orchestration.adr_orchestrator_service import AdrOrchestratorService
```

**Regla:** si un side-effect orquesta 2+ features, debe llamar a un **Orchestrator Service** en `orchestration/`, no hacer la orquestación inline.

### 2.4 Regla de thin adapters (cierra G3, G4)

Tasks y side-effects son **thin adapters**: delegan en Application Services u Orchestrators. No contienen lógica de negocio.

```python
# ANTES (violación):
# tasks/kg.py (775 líneas)
# tasks/kg_helpers.py (361 líneas dentro de tasks/)

# DESPUÉS (correcto):
# tasks/documenting_knowledge.py (< 50 líneas)
def documenting_knowledge(ctx, data):
    svc = get_module_service("ckg_service")
    return svc.documenting_knowledge(...)

# features/cognitive_knowledge/application/services/ckg_service.py
class CkgApplicationService:
    def documenting_knowledge(self, ...):
        # lógica de negocio aquí
```

**Métrica:** `wc -l tasks/<task>.py < 200` es necesaria pero no suficiente. La métrica real es: **0 imports de repositorios/searchers/features en `tasks/`**.

### 2.5 Regla de orquestación multi-feature (cierra G10, ADR-050)

Si una task/side-effect necesita orquestar 2+ features, debe consumir un **Orchestrator Service** en `orchestration/`:

| Task/Side-effect actual | Servicio orquestador objetivo | Features orquestados |
|------------------------|------------------------------|----------------------|
| `tasks/gathering.py` | `GatheringService` | CPG × CKG |
| `tasks/kg_extract.py` | `ExtractKnowledgeService` | CPG × CKG |
| `tasks/project_init.py` | `ProjectInitializationService` | Project × CKG × CPG |
| `tasks/resolve_project_sprint.py` | `ProjectSprintResolverService` | Project × Delta × Engine |
| `side_effects/create_adr.py` | `AdrOrchestratorService` | Delta × CKG × Project |
| `side_effects/transform_spec.py` | `SpecTransformService` | Delta × Project |

### 2.6 División objetivo de `features/main` (cierra el rol dual de main)

El ADR-050 §2.1 clasifica los cross-feature orchestrators como *"de main hoy, reutilizable futuro"*. Según mejores prácticas (clean/hexagonal), main no debe tener dos roles: **composition root + transporte** son de main; la **orquestación cross-feature es capa de aplicación** y debe vivir en un módulo reutilizable aparte.

**Principio:** `main` queda como *shell* delgado (lo que es "de Walker, no reutilizable"); nace `orchestration/` como capa de aplicación reutilizable que absorbe los orchestrators.

```
walker/
├── features/                     ← BOUNDED CONTEXTS (dominio puro, uno por feature)
│   ├── delta_tracking/
│   ├── code_analysis/
│   ├── cognitive_knowledge/
│   ├── project/
│   └── event_tracing/
│
├── orchestration/                ← NUEVA CAPA DE APLICACIÓN cross-context (reutilizable)
│   ├── __init__.py
│   ├── prepare_project_service.py    ← era main/cli_interface/application/orchestrators/
│   ├── check_health_service.py       ← era main/cli_interface/application/orchestrators/
│   ├── intelligence_service.py       ← era main/task_execution/application/
│   ├── gathering_service.py          ← orquesta CPG × CKG para DESIGN
│   ├── extract_knowledge_service.py  ← orquesta CPG × CKG para KNOWLEDGE
│   ├── adr_orchestrator_service.py   ← orquesta Delta × CKG × Project
│   ├── project_sprint_resolver.py    ← orquesta Project × Delta × Engine
│   └── ...
│
├── features/main/                ← SOLO COMPOSITION ROOT + TRANSPORTE (shell delgado)
│   ├── composition/              ← wiring (container, registry, lifecycle)
│   ├── api/                      ← facades públicas (walker_app, workflow_facade, front_controller)
│   ├── cli_interface/            ← transporte (mcp_adapter, handlers, contracts, presenter, error_mapping)
│   ├── task_execution/           ← task layer (tasks/, side_effects/, context/)
│   │   ├── tasks/                ← THIN ADAPTERS (sin lógica)
│   │   ├── side_effects/         ← THIN ADAPTERS (sin orquestación multi-feature)
│   │   └── context/              ← Context providers
│   └── prompts/                  ← subsistema autónomo
│
├── shared/                       ← infraestructura cross-cutting (CognitiveGraph, path_resolver, DTOs)
└── libs/                         ← librerías (cognitive_graph registry)
```

**Reglas de dependencia después del split:**

```
transport (main/cli_interface) ──► orchestration/ ──► features/<x>/application/services/
    └── composition root cablea TODO (incluye orchestration/)       │
                                                                    ▼
                                                             features/<x>/use_cases/ (privados)
```

1. **El transporte y las tasks de main no importan features** — consumen `orchestration/` o Application Services. **Excepción explícita: el composition root (`composition/registry.py`) SÍ importa features** — es su función cablearlas.
2. **orchestration/ importa solo Application Services de features** — nunca Use Cases (INV-013), nunca infraestructura directa
3. **tasks consumen orchestration/ o Application Services** — nunca use cases
4. **shared/libs no importan nada de features ni orchestration** — infraestructura pura

### 2.7 Patrón canonical de capa de aplicación (ADR-WALKER-048b)

Todo feature de Walker adopta la estructura `use_cases/ + services/ + domain/services/`:

```
features/<feature>/
├── application/
│   ├── use_cases/              ← Núcleo hexagonal (privado al módulo)
│   │   ├── create_entity.py    │   • Una clase = una acción atómica
│   │   ├── query_entity.py     │   • Recibe puertos, devuelve entidades/Result
│   │   └── update_entity.py    │   • No sabe de HTTP, MCP, CLI, dicts
│   │                            │   • NUNCA se registra en container.py
│   │                            │   • NUNCA se importa desde fuera del módulo
│   ├── services/               ← Fachada pública (registrada en main)
│   │   └── entity_service.py   │   • Orquesta use cases
│   │                            │   • Traduce entidades ↔ dicts
│   │                            │   • Punto de entrada para MCP/CLI/engine
│   │                            │   • NO contiene lógica de negocio
│   ├── ports/                  ← Interfaces (puertos)
│   │   └── entity_repository.py│   • Contratos que implementa infrastructure
│   ├── presenters.py           ← Entidad → dict para API
│   └── projections.py          ← Proyecciones legacy
├── domain/
│   ├── entities/               ← Entidades del dominio
│   ├── services/               ← Lógica pura sin IO
│   │   └── entity_rules.py     │   • Invariantes, validaciones, transiciones
│   └── values/                 ← Value objects
└── infrastructure/
    └── repositories/           ← Implementaciones de puertos
```

**Reglas de responsabilidad:**

| Capa | Responsabilidad | Visibilidad |
|------|-----------------|-------------|
| **Use Cases** | Lógica de negocio atómica. Una clase = una acción. Recibe puertos, devuelve entidades o `Result[T, E]`. | Interna al módulo |
| **Application Services** | Fachada pública. Orquesta use cases, traduce entidades ↔ dicts. Punto de entrada para MCP/CLI/engine. | Pública |
| **Domain Services** | Lógica pura sin IO: invariantes, validaciones, transiciones de estado. | Pública |
| **Infrastructure** | Persistencia, sistemas externos, implementación de puertos. | Pública |

**Reglas de consumo:**

- **Use Cases** son implementación interna. Nadie fuera del feature los importa directamente (INV-013).
- **Application Services** y **Domain Services** son la API pública del feature.
- **El composition root** (`main/container.py`) registra solo Application Services y Domain Services. NUNCA registra Use Cases individuales.
- **Tasks y side-effects** consumen Application Services, no Use Cases directos.

---

## 3. failure_scenarios

| # | Fallo | Causa raíz | Mitigación | Detección |
|---|-------|-----------|------------|-----------|
| 1 | Los orchestrators siguen importando Use Cases directos después de la migración | Refactor estructural sin revisión de consumo; el patrón viejo se copia por inercia | Lint rule: prohibir `from features.<x>.application.use_cases` y `from core.engine.application.use_cases` desde `features/main/`; code review enfocado | `grep -rn "application.use_cases\|core.engine.application.use_cases" features/main/` |
| 2 | El sprint workflow ejecuta `plan`/`implement`/`review`/`verify` y no encuentra handler | Tasks del sprint en `delta_tracking/tasks/` nunca registradas (nadie llama `register_delta_tracking_tasks`) | Mover tasks del sprint a `task_execution/tasks/` + invocar su registro en `register_all()`; test de completitud del TaskRegistry | Smoke: `AppContainer.build()` → verificar que las 4 tasks del sprint están en `task_registry.list()` |
| 3 | La lógica de kg se vuelve a acumular en la capa de tasks (kg_helpers crece) | Extracción "cosmética": la lógica se muda a un helper hermano en la misma capa | Métrica de capa, no de archivo: grep de imports de searcher/repos en `tasks/`; checklist "si una task o helper > 200 líneas, extraer a services/" | `wc -l features/main/task_execution/tasks/*.py` + `grep -l "searcher\|Repository" features/main/task_execution/tasks/*.py` |
| 4 | `agent_context` se registra dos veces y el segundo registro pisa al primero | Dos puntos de registro para la misma task (`KG_TASKS` + `register` propio) | Un solo registro por task: eliminar el `register()` propio de `kg_agent_context.py` y dejar solo `KG_TASKS` (o viceversa); warning "already registered" visible | Smoke de registro con duplicados detectados |
| 5 | El engine vuelve a importar features directo tras la limpieza | `core/engine` no tiene guard de boundaries; la conveniencia de importar directo gana | Lint rule: prohibir `from features.` en `core/engine/`; service locator como único puente | `grep -rn "from features\." core/engine/` |
| 6 | Los services DI se registran con factories que retornan clases/módulos | Refactor incompleto de factories (ADR-050 §2.6) | Test de tipado: toda factory registrada retorna instancia | Smoke: `registry.resolve("ckg_service")` es instancia, no clase |
| 7 | Features vuelven a definir tools MCP después de la limpieza | No hay lint/test que prohiba `mcp.py` en features/ | CI grep: 0 archivos `mcp.py` en `features/` (excepto `features/main/`) | `find features/ -name "mcp.py"` |
| 8 | Nombres técnicos (`cpg_`, `ckg_`, `kg_`, `delta_`) reaparecen en tools nuevas | No hay política de naming semántico documentada ni validada en CI | ADR-049 + este ADR como referencia; code review checklist incluye naming | `grep -rn '"cpg_\|"ckg_\|"kg_\|"delta_' features/main/task_execution/` |
| 9 | Tasks/side-effects acceden a `task_execution/infrastructure/` | No hay boundary explícito entre task layer e infra | Lint rule: prohibir imports de `task_execution/infrastructure` desde `task_execution/tasks` y `task_execution/side_effects` | `grep -rn "task_execution.infrastructure" features/main/task_execution/tasks/ side_effects/` |
| 10 | Side-effects orquestan 2+ features inline | No hay regla de que la orquestación multi-feature va en `orchestration/` | Code review checklist: "si el handler importa 2+ features, mover a orchestration/" | `grep -rn "from features\." features/main/task_execution/side_effects/` |
| 11 | Tags técnicos en metadata de tools | No hay regla de tags de dominio | Code review checklist: tags deben ser de dominio (`["knowledge", "search"]`), no técnicos (`["kg", "cognitive-graph"]`) | `grep -rn '"kg"\|"cpg"\|"delta"' features/main/task_execution/tasks/*.py` |
| 12 | `orchestration/` se convierte en otro monolith (lógica de negocio en vez de orquestación) | Falta de disciplina; se agrega lógica de negocio en vez de orquestar | Code review: orchestrators solo orquestan, no tienen lógica de negocio propia | Review checklist + métrica `wc -l orchestration/*.py < 300` |

---

## 4. Checklist de segundo orden

1. **¿Qué módulos importan esto?**
   - `features/main/task_execution/tasks/*` (todos los adapters)
   - `features/main/task_execution/side_effects/*` (todos los handlers)
   - `features/main/task_execution/application/*` (orchestrators internos)
   - `features/main/cli_interface/application/orchestrators/*` (orchestrators cross-feature)
   - `features/main/composition/registry.py` (register_all — debe registrar también los services de `orchestration/`)
   - `features/main/cli_interface/mcp_adapter.py` (sincroniza catálogo MCP)
   - `core/engine/**` (via service locator)
   - Todos los features (`cognitive_knowledge`, `code_analysis`, `delta_tracking`, `project`, `event_tracing`) — expuestos via Application Services

2. **¿Qué side effects dispara?**
   - Ninguno nuevo. Los side effects existentes (`register_task_execution_side_effects`) se mantienen.
   - Los side effects que orquestan 2+ features se mueven a `orchestration/` como services; el side-effect queda como thin adapter.

3. **¿Qué eventos de dominio publica?**
   - Ninguno nuevo. El workflow sigue emitiendo desde los mismos lugares.

4. **¿Qué tests dependen de este comportamiento?**
   - `tests/test_agent_e2e_bdd.py`
   - `tests/test_cli_walker_bdd.py`
   - `tests/test_rag_pipeline_bdd.py`
   - `tests/test_agent_session_bdd.py`
   - `tests/test_container_lifetime_bdd.py`
   - `tests/test_registry_completeness_bdd.py`
   - `tests/test_workflow_e2e_bdd.py`
   - `tests/test_workflow_e2e_real_bdd.py`
   - `tests/test_side_effect_outbox_bdd.py`
   - `tests/test_mcp_project_id_resolution_bdd.py`
   - `tests/test_create_adr_bdd.py`
   - `tests/test_cpg_indexed_bdd.py`

5. **¿Qué cambia en MCP adapter, CLI, CKG, DeltaGraph, Trace?**
   - MCP adapter: ahora el catálogo viene 100% del `TaskRegistry` (sin `mcp.py` en features). Las tools mantienen nombres semánticos.
   - CLI: sin cambios de contrato; los comandos mantienen su comportamiento.
   - CKG: `CkgApplicationService` absorbe la lógica de búsqueda/transformación de `kg_helpers`.
   - DeltaGraph: `SprintApplicationService` pasa a ser el punto de entrada de tasks del sprint.
   - Trace: sin cambios.

---

## 5. Implementación por fases

### Fase 1: Limpiar transporte MCP/REST de features (G7)

1. Eliminar `mcp.py` de todos los features (`cognitive_knowledge`, `code_analysis`, `project`, `event_tracing`, `delta_tracking`).
2. Verificar: `find features/ -name "mcp.py"` → 0 (excepto `features/main/cli_interface/mcp.py`).
3. Verificar: `list_tools()` devuelve tools desde `TaskRegistry` únicamente.

### Fase 2: Renombrar tools/tasks/side-effects a nombres semánticos (G8)

1. Renombrar tasks públicas MCP:
   - `cpg_analyze` → `analyze_code_structure`
   - `ckg_analyze` → `analyze_knowledge`
   - `kg_search` → `search_knowledge`
   - `kg_multi` → `search_memories`
   - `kg_extract` → `extract_code_knowledge`
   - `kg_schema` → `memory_schema`
   - `kg_task_context` → `task_code_context`
   - `kg_diagnose` → `diagnose_failure`
   - `kg_qa_evidence` → `verify_evidence`
   - `kg_sprint_evidence` → `sprint_evidence`
   - `build_initial_ckg` → `initialize_knowledge_base`
   - `scan_code_and_build_cpg` → `build_code_index`
   - `log_rejection_to_ckg` → `record_rejection`
2. Renombrar side-effects:
   - `ckg_status` → `update_knowledge_status`
   - `delete_ckg_nodes` → `remove_knowledge`
   - `index_delta` → `index_sprint_tasks`
   - `reindex_delta` → `rebuild_sprint_index`
   - `restore_cpg_snapshot` → `restore_code_snapshot`
   - `sync_delta` → `sync_sprint_state`
3. Actualizar workflow grafo (`core/engine/workflow/nodes/*.json`) con nombres canónicos.
4. Actualizar tags técnicos en metadata por tags de dominio.
5. Actualizar tests que referencian nombres antiguos.

### Fase 3: Eliminar imports de infraestructura desde tasks/side-effects (G9)

1. Migrar `tasks/verification.py`: mover lógica de `verify_engine` a `VerificationService` en `orchestration/`.
2. Migrar `tasks/workflow_tools.py`: mover schemas a contracts/shared.
3. Migrar `tasks/capture_task.py`: mover `project_context` y `decision_resolution` a services.
4. Migrar `tasks/cpg_analyze.py` y `review.py`: consumir `CpgApplicationService` en vez de `ICpgAnalysisPort`.
5. Verificar: `grep -rn "task_execution.infrastructure" features/main/task_execution/tasks/ side_effects/` → 0.

### Fase 4: Mover orquestación inline a Orchestrators (G10, G1, G11)

1. **create_adr**: crear `AdrOrchestratorService` en `orchestration/` que coordine `SpringService` + `CkgApplicationService` + `ProjectService`. El side-effect queda como thin adapter.
2. **gathering**: crear `GatheringService` en `orchestration/` que resuelva CPG + CKG.
3. **kg_extract**: crear `ExtractKnowledgeService` en `orchestration/`.
4. **project_init**: crear `ProjectInitializationService` en `orchestration/`.
5. **resolve_project_sprint**: crear `ProjectSprintResolverService` en `orchestration/`.
6. Verificar: `grep -rn "from features\." features/main/task_execution/side_effects/` → 0 (solo orchestration/).

### Fase 5: Extraer lógica de tasks a Application Services (G3, G4)

1. Mover `_build_ckg_node` y lógica de búsqueda de `kg_helpers.py` a `CkgApplicationService`.
2. Extraer lógica de `kg_extract.py`, `capture_task.py`, `project_init.py`, `ckg_analyze.py`, `phase_intelligence.py` a los Application Services de los features correspondientes.
3. Verificar: todas las tasks < 200 líneas y sin imports de repos/searchers.

### Fase 6: Registrar Application Services en DI (G6)

1. Registrar `sprint_service`, `ckg_service`, `trace_service`, `cpg_service` en `composition/registry.py` (factories → instancias).
2. Migrar tasks a `get_module_service`.
3. Verificar: smoke de registry con factories que retornan instancias.

### Fase 7: Extraer orchestrators a `orchestration/` (§2.6)

1. Crear `orchestration/` con `prepare_project_service.py`, `check_health_service.py`, `intelligence_service.py` (movidos desde main, sin cambios de lógica).
2. Actualizar imports en `cli_interface/` (handlers, workflow_facade) y tasks que consumían los orchestrators.
3. Registrar los services de `orchestration/` en el composition root (`composition/registry.py`).
4. Eliminar `main/cli_interface/application/orchestrators/` y `main/task_execution/application/intelligence_service.py` (archivos viejos).
5. Verificar: `grep -rn "from features.main" orchestration/` → 0 (orchestration no depende de main); `AppContainer.build()` OK.

### Fase 8: Cierre de boundaries del engine (G5)

1. Migrar imports de `features.*` en `core/engine/**` a service locator.
2. Evaluar eliminación de `core/engine/resolve.py` (duplica `conflict_resolver`).
3. Verificar: `grep -rn "from features\." core/engine/` → 0.

---

## 6. Validación post-implementación

### 6.1 Modelo de validación por capas

La validación combina **architecture tests** (L1), **contract tests** (L2), **integration tests** (L3) y **E2E/BDD** (L4). Cada capa responde una pregunta distinta:

| Capa | Pregunta que responde | Naturaleza |
|------|----------------------|------------|
| **L1 — Architecture** | ¿Se respetan los boundaries y convenciones estructurales? | Automática (tests + CI) |
| **L2 — Contract** | ¿Cada componente cumple su rol (thin adapter, orchestrator, service)? | Automática (tests unitarios de contrato) |
| **L3 — Integration** | ¿Las capas se conectan correctamente? | Automática (tests de integración) |
| **L4 — E2E/BDD** | ¿El negocio funciona de punta a punta? | Automática (tests E2E) + Humana (code review) |

**Regla de aprobación**: L1 es **obligatoria** para aprobar el ADR. L2-L4 son progresivas por módulo/feature.

---

### 6.2 L1 — Architecture Tests (boundaries y naming)

Tests automáticos en `tests/architecture/` que validan reglas estructurales. Usan `ast` para análisis de imports, no `grep`.

| ID | Criterio | Test | Implementación |
|----|----------|------|----------------|
| L1-01 | Features no definen transporte MCP/REST | `test_no_mcp_in_features.py` | `find features/*/mcp.py` → 0 (excepto `main/`) |
| L1-02 | Tasks no importan use_cases de features | `test_no_use_case_imports_in_tasks.py` | AST: 0 imports de `features.<x>.application.use_cases` desde `tasks/` |
| L1-03 | Engine no importa features | `test_no_feature_imports_in_engine.py` | AST: 0 `from features.` en `core/engine/**` |
| L1-04 | Tasks/side-effects no importan infra de main | `test_no_infra_imports_in_tasks.py` | AST: 0 imports de `task_execution.infrastructure` desde `tasks/` y `side_effects/` |
| L1-05 | Side-effects solo importan de `orchestration/` | `test_side_effects_import_only_orchestration.py` | AST: imports desde `features.` en `side_effects/` → solo `orchestration/` |
| L1-06 | Nombres semánticos en tools públicas | `test_semantic_naming_convention.py` | Names de tasks/side-effects sin prefijos técnicos (`cpg_`, `ckg_`, `kg_`, `delta_`) |
| L1-07 | Tags de dominio en metadata | `test_domain_tags_only.py` | Metadata de tools sin tags técnicos |
| L1-08 | `orchestration/` no depende de main | `test_orchestration_independent.py` | 0 imports de `features.main` desde `orchestration/` |
| L1-09 | Composition root registra solo Services | `test_composition_root_registers_services_only.py` | 0 Use Cases individuales en `register_all()` |
| L1-10 | Factories DI retornan instancias | `test_di_factories_return_instances.py` | `registry.resolve("x_service")` es instancia, no clase |
| L1-11 | Thin adapters (métrica de capa, no solo archivo) | `test_tasks_are_thin_adapters.py` | 0 imports de `repositorios`, `searchers`, `CognitiveGraph` en `tasks/` |
| L1-12 | Sin handlers huérfanos | `test_no_orphan_handlers.py` | Todo handler registrado en `ActionCatalog` tiene al menos 1 referencia en `nodes/*.json` o es compensación interna |

**Ejemplo de implementación (L1-02)**:

```python
# tests/architecture/test_no_use_case_imports_in_tasks.py
import ast
import pathlib

def test_tasks_do_not_import_feature_use_cases():
    """Tasks deben consumir Application Services, no Use Cases directos."""
    root = pathlib.Path("features/main/task_execution/tasks")
    violations = []
    for py in root.glob("*.py"):
        if py.name.startswith("_"):
            continue
        tree = ast.parse(py.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and "application.use_cases" in node.module:
                    violations.append(f"{py}: importa {node.module}")
    assert not violations, f"Tasks importan Use Cases: {violations}"
```

---

### 6.3 L2 — Contract Tests (pruebas de rol por componente)

Cada componente (task, side-effect, orchestrator, service) tiene un test que valida **qué consume y qué produce**, sin importar dónde está físicamente.

| ID | Rol | Criterio | Test |
|----|-----|----------|------|
| L2-01 | **Task (thin adapter)** | Consume 1 Application Service/orchestrator; retorna `TaskResult` o dict estándar | `tests/unit/tasks/test_<task>.py`: mock del service, assert 1 llamada, assert contrato salida |
| L2-02 | **Side-effect (thin adapter)** | Consume 1 Application Service/orchestrator; retorna envelope `{ok, result/error, summary}` | `tests/unit/side_effects/test_<effect>.py`: mock del service, assert 1 llamada, assert envelope |
| L2-03 | **Orchestrator** | Orquesta 2+ services; no tiene lógica de negocio propia | `tests/unit/orchestration/test_<orchestrator>.py`: mock de 2+ services, assert secuencia de llamadas |
| L2-04 | **Application Service** | Orquesta use cases internos; traduce entidades ↔ dicts; no importa infraestructura | `tests/unit/features/<feature>/test_<service>_service.py`: mock de repos, assert orquestación |
| L2-05 | **ActionCatalog** | Resuelve names → handlers; soporta aliases; no hay colisiones | `tests/unit/test_action_catalog.py`: register + resolve + alias + conflictos |
| L2-06 | **ErrorHandler** | Unifica fallos: task → TaskFailedError; side-effect → SideEffectFailedError → TaskFailedError | `tests/unit/test_error_handler.py`: simulate fallos, assert misma clase final |
| L2-07 | **ToolCatalog** | Fusiona ModuleRegistry + TaskRegistry; no hay discrepancias | `tests/unit/test_tool_catalog.py`: list_tools() == TaskRegistry.public_tasks() ∪ ModuleRegistry.tools() |

**Regla de rol**: si un componente importa más de lo que su rol permite, el test de contrato falla. Por ejemplo, una task que importa 2 services diferentes viola L2-01.

---

### 6.4 L3 — Integration Tests (contratos entre capas)

Validan que las capas se conectan sin fallos en runtime.

| ID | Criterio | Test | Validación |
|----|----------|------|------------|
| L3-01 | Registry/tool_catalog consistency | `tests/integration/test_tool_catalog_consistency.py` | TaskRegistry.public ∩ ModuleRegistry.tools = ∅; list_tools() = unión |
| L3-02 | ModuleRegistry resuelve servicios DI | `tests/integration/test_module_registry_di.py` | `get_module_service("ckg_service")` retorna instancia; no KeyError |
| L3-03 | ActionCatalog completeness | `tests/integration/test_action_catalog_completeness.py` | actions + side_effects + compensations ⊆ catálogo |
| L3-04 | Workflow grafo ⊆ catálogo (bidireccional) | `tests/integration/test_workflow_catalog_bidirectional.py` | Todo `action`, `side_effects[].type`, `compensation[].type` en `nodes/*.json` existe en `ActionCatalog` |
| L3-05 | Composition root smoke | `tests/integration/test_composition_root_smoke.py` | `AppContainer.build()` sin warnings de handlers duplicados ni servicios faltantes |
| L3-06 | Sprint workflow cableado | `tests/integration/test_sprint_workflow_handlers.py` | `plan_sprint`, `implement`, `review_code`, `verify_predicates` en `task_registry` |
| L3-07 | MCP tools ejecutan sin error de protocolo | `tests/integration/test_mcp_tool_invocation.py` | `list_tools()` > 0; `tools/call` con tool válida retorna resultado, no error -32000 |

---

### 6.5 L4 — E2E/BDD (comportamiento del negocio)

Validan flujos completos del negocio sin importar la implementación interna.

| ID | Criterio | Test | Validación |
|----|----------|------|------------|
| L4-01 | Workflow E2E | `tests/test_workflow_e2e_bdd.py` | Flujo completo project_init → sprint → design → implement → verify |
| L4-02 | Workflow real engine | `tests/test_workflow_e2e_real_bdd.py` | Flujo con engine real, state repo, side-effects |
| L4-03 | MCP agent E2E | `tests/test_agent_e2e_bdd.py` | Agente invoca tools MCP, recibe respuestas correctas |
| L4-04 | CLI walker | `tests/test_cli_walker_bdd.py` | Comandos prepare, list, submit, transition funcionan |
| L4-05 | Knowledge lifecycle | `tests/test_knowledge_e2e_bdd.py` | documenting_knowledge → search_knowledge → supersede funciona |
| L4-06 | Sprint planning E2E | `tests/test_sprint_planning_e2e_bdd.py` | plan_sprint → add_tasks → advance_sprint_task |
| L4-07 | Code analysis E2E | `tests/test_cpg_indexed_bdd.py` | build_code_index → fingerprint_match → cpg_indexed event |

---

### 6.6 Políticas de migración sin breaking changes (M)

Estas no son tests, sino **reglas de gobernanza** validadas en code review + CI.

| ID | Política | Validación |
|----|----------|------------|
| M1 | Aliases durante transición | Renombres mantienen alias `deprecated=True` en `ActionCatalog` hasta release mayor |
| M2 | Sin eliminación sin plan | Handlers eliminados = antes marcados `deprecated=True` + reemplazo canónico registrado |
| M3 | Versionado semántico de tools MCP | Breaking changes en CHANGELOG + version bump en tool metadata |
| M4 | Feature flags para `orchestration/` | `orchestration/` se activa vía config; fallback a orquestación inline con log warning |
| M5 | Smoke test obligatorio en CI | `python -c "from features.main.container import AppContainer; AppContainer.build()"` pasa sin warnings |
| M6 | Deprecation warning en runtime | Handlers/tools deprecated emiten `DeprecationWarning` cuando se invocan |

---

### 6.7 Checklist de aprobación por capa

#### Aprobación L1 (obligatoria para aprobar ADR)

- [ ] `pytest tests/architecture/ -v` → 0 fallos
- [ ] `find features/ -name "mcp.py"` → solo `features/main/cli_interface/mcp.py`
- [ ] `grep -rn "application.use_cases" features/main/` → 0
- [ ] `grep -rn "from features\." core/engine/` → 0
- [ ] `grep -rn "task_execution.infrastructure" features/main/task_execution/` → 0

#### Aprobación L2 (por módulo)

- [ ] Cada task/side-effect nuevo tiene su test de contrato en `tests/unit/tasks/` o `tests/unit/side_effects/`
- [ ] Cada orchestrator nuevo tiene su test en `tests/unit/orchestration/`
- [ ] Cada Application Service nuevo tiene su test en `tests/unit/features/<feature>/`

#### Aprobación L3 (por release)

- [ ] `pytest tests/integration/ -v` → 0 fallos
- [ ] `AppContainer.build()` sin warnings

#### Aprobación L4 (por release)

- [ ] `pytest tests/test_workflow_e2e_bdd.py tests/test_workflow_e2e_real_bdd.py -q` → 0 fallos
- [ ] `pytest tests/test_agent_e2e_bdd.py -q` → 0 fallos

---

### 6.8 Métricas de salud arquitectónica (dashboard)

Estas métricas se actualizan en CI y se muestran en el dashboard del proyecto:

| Métrica | Fuente | Target |
|---------|--------|--------|
| Architecture tests pass rate | `pytest tests/architecture/` | 100% |
| Thin adapters ratio | `wc -l` + AST imports | 100% tasks/side-effects < 200 líneas y sin imports de repos |
| Orquestation coverage | `grep -rn "from features\." side_effects/` | 100% orquestación en `orchestration/` |
| Naming compliance | `test_semantic_naming_convention.py` | 100% tools sin prefijos técnicos |
| E2E pass rate | `pytest tests/ -k e2e` | 100% |
| Registry consistency | `test_tool_catalog_consistency.py` | 0 discrepancias |
| DI health | `test_module_registry_di.py` | 0 KeyError, 100% factories retornan instancias |
| Sprint workflow completeness | `test_sprint_workflow_handlers.py` | 4/4 handlers registrados |

---
## 7. Referencias

- ADR-WALKER-045: `features/main` como composition root final.
- ADR-WALKER-046: `AppContainer` lifetime y request scoping.
- ADR-WALKER-047: Registry conflation layer violations — modelo refinado (TaskRegistry = SSOT, ModuleRegistry = DI).
- ADR-WALKER-048: Recuperación determinista de memoria (prompt context) — capas estables.
- ADR-WALKER-048b (`schemas/adrs/ADR-WALKER-048.json`): Patrón canonical de capa de aplicación — Use Cases + Application Services + Domain Services.
- ADR-WALKER-049: Side-effect naming semántico — nombres de negocio, no técnicos.
- ADR-WALKER-050: Refinamiento de `features/main` — boundaries entre composition root, cross-feature orchestrators y librería reutilizable.
- SPEC-011: Unificación de capa de aplicación (Use Cases + Application Services + Domain Services).
- SPEC-012: Refinamiento de features/main.
- SPEC-014: Workflow Actions — ActionCatalog + naming unificado.
- `capa_rout.md` (`docs/design/`): D1 (composition roots paralelos), D2 (dispatch), D3 (tasks usan servicios de módulo).
- `project.md`: INV-013 (Use Cases internos, Application Services públicos), INV-014 (main no es un feature, boundaries estrictos).
- Seemann, M. — *Dependency Injection in .NET* (patrón Composition Root): el composition root es dumb wiring, sin lógica de negocio.
- Clean/Hexagonal Architecture — *Dependency Rule*: las dependencias apuntan hacia adentro; la orquestación cross-context es capa de aplicación, no del composition root.
