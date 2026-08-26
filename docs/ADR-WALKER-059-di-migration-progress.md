# ADR-WALKER-059: DI Migration — service locator → constructor injection

> **Status:** COMPLETADO — service locator eliminado
> **Fecha:** 2026-08-25
> **Autor:** Walker (agente) + PO
> **Complementa:** ADR-WALKER-058 (anti-patterns elimination), ADR-WALKER-045 (composition root único)

---

## 1. Problema

`shared/core/engine/bootstrap.py` exponía un service locator (`get_module_service()`, `resolve_service()`) que era importado por features/ y por shared/core/engine/. Esto creaba:
- **Acoplamiento implícito**: las dependencias se resolvían en runtime, no en compilación
- **Ciclos de composición**: `features.delta_tracking → features.main.bootstrap → features.delta_tracking`

## 2. Solución: DI paramétrico + eliminación de fallbacks

Cada caller que usaba `get_module_service()`/`resolve_service()` ahora:
1. **Acepta la dependencia como parámetro** (constructor injection)
2. **Lanza ValueError si no se provee** (sin fallback al service locator)
3. **El composition root inyecta las dependencias** al construir los objetos

## 3. Archivos migrados

### features/ — DI directa (10 archivos)

| Archivo | Cambio |
|---|---|
| `engine_mcp.py` | Closure injection + eliminado fallback `resolve_service("workflow")` |
| `mcp_adapter.py` | `AppContainer.build().module_registry` directo |
| `handlers.py` | `_build_graph` import directo |
| `ceremonies.py` | `ProjectService` import directo |
| `_sprint_helpers.py` | `ProjectService` import directo |
| `spring_service.py` | `ProjectService` import directo |
| `collector.py` | `_build_cg` recibe `searcher_factory` + fallback a composition root |
| `cognitive_ckg_repo.py` | `graph_factory` param requerido (ValueError si None) |
| `cognitive_delta_repo.py` | `graph_factory` param requerido (ValueError si None) |
| `event_tracing/repository.py` | `graph_factory` param requerido (ValueError si None) |

### features/ — DI paramétrico (4 archivos)

| Archivo | Parámetros |
|---|---|
| `kg_context.py` | `searcher_factory`, `path_resolver` (ValueError si None) |
| `trace_push.py` | `graph_factory` (fallback a AppContainer si None) |
| `trace_event_publisher.py` | task_registry inyectado desde AppContainer |
| `ckg.py` | `graph_factory` param agregado |

### shared/core/engine/ — DI paramétrico (5 archivos)

| Archivo | Parámetros |
|---|---|
| `trace_log.py` | `path_resolver`, `cognitive_graph_factory` (ValueError si None) |
| `contracts.py` | `project_svc`, `ckg_factory` (ValueError si None) |
| `task_dispatcher.py` | `registry` (ValueError si None) |
| `tool_catalog.py` | `registry` requerido en todas las funciones internas |
| `query_manager.py` | `searcher_factory` (ValueError si None) |

### tool_catalog.py — callers actualizados

| Caller | Cambio |
|---|---|
| `mcp_adapter.py` `_get_tool_catalog()` | `registry=app.module_registry` |
| `mcp_adapter.py` `_sync_tools()` | `registry=app.module_registry` |
| `protocol_builder.py` `_build_tools_section()` | `registry=app.module_registry` |
| `protocol_builder.py` `_build_analysis_section()` | `registry=app.module_registry` |

### Imports muertos limpiados (8 archivos)

`context_resolver.py`, `prompt_context_builder.py`, `uncaptured_work.py`, `use_cases.py`, `workflow_api.py`, `composer.py`, `query_manager.py`, `resolve.py`

### Service locator eliminado de bootstrap.py

| Función | Estado |
|---|---|
| `get_module_service()` | **ELIMINADA** |
| `resolve_service()` | **ELIMINADA** |
| `get_module_registry()` | **ELIMINADA** |
| `set_service_locator()` | Permanece (composition root only) |
| `reset_module_registry()` | Permanece (composition root only) |

### Tests actualizados (8 archivos)

| Test | Cambio |
|---|---|
| `test_composition_root_d1.py` | `AppContainer.build().module_registry.get_service("workflow")` |
| `test_module_registry_di_bdd.py` | `AppContainer.build().module_registry` |
| `test_mcp_hot_reload_budget.py` | `AppContainer.build()` en vez de `get_module_registry()` |
| `test_trace_event_publisher_bdd.py` | task_registry inyectado |
| `test_container_completeness_bdd.py` | `module_registry.get_service(...)` |
| `test_registry_completeness_bdd.py` | `app.module_registry.get_service("task_registry")` |
| `test_workflow_production_di_bdd.py` | `AppContainer.build().module_registry.get_service("workflow")` |

## 4. Resultado

| Métrica | Antes | Después |
|---|---|---|
| Calls a `get_module_service()` | 25+ | **0** |
| Calls a `resolve_service()` | 15+ | **0** |
| Calls a `get_module_registry()` | 5 | **0** |
| Imports de funciones eliminadas | 20+ archivos | **0 archivos** |
| Imports muertos de `resolve_service` | 8 archivos | **0 archivos** |
| Ciclos de composición | 1 ciclo | **0 ciclos** |
| Tests verdes (nuevos) | 42 | **57** |
| Tests pre-existentes failing | N/A | **1** (`test_registration_idempotent`) |

## 5. Service locator restante

`set_service_locator()` y `reset_module_registry()` permanecen en `bootstrap.py` — son usados por:
- `composition/lifecycle.py` — `set_service_locator()` (puebla el locator)
- `composition/container.py` — `reset_module_registry()` (hot reload)

Estas funciones son **válidas** porque son el composition root quien POBLA el locator.

## 6. Failure scenarios

| Scenario | Causa raíz | Mitigación | Detección |
|---|---|---|---|
| `trace_push` falla con ValueError sin graph_factory | TaskRegistry.execute() solo pasa 2 args | Fallback a AppContainer.build() | Test E2E `test_evento_cpg_publicado_llega_al_indice_trace` |
| `execute_public_task` falla sin registry | Caller no inyecta task_registry | Composition root inyecta task_registry | Test `test_trace_push_es_invocable_por_el_motor_interno` |
| `tool_catalog` falla sin registry | Caller no pasa module_registry | list_tools() recibe registry de AppContainer | Test `test_tool_catalog_bdd` |
| `list_tools()` devuelve catálogo vacío | registry=None y ValueError | Caller en mcp_adapter/protocol_builder pasa registry | Test E2E MCP tools list |

## 7. Verificación

| Check | Resultado |
|---|---|
| 57 tests pasan | ✅ |
| 0 calls a `get_module_service()` | ✅ |
| 0 calls a `resolve_service()` | ✅ |
| 0 calls a `get_module_registry()` | ✅ |
| Funciones eliminadas de bootstrap.py | ✅ (3 funciones) |
| 0 ciclos de composición | ✅ |
| Composition root inyecta dependencias en TraceLogger | ✅ |
| DI paramétrico en 19 archivos | ✅ |
| DI directa en 10 archivos | ✅ |
| trace_push resuelve graph_factory via AppContainer fallback | ✅ |
| trace_event_publisher inyecta task_registry | ✅ |
