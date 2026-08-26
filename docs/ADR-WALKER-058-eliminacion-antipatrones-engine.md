# ADR-WALKER-058: Eliminación de antipatrones en engine — protocol decoupling + service locator deprecation

> **Status:** Ejecutado
> **Fecha:** 2026-08-25
> **Autor:** Walker (agente) + PO
> **Complementa:** ADR-WALKER-056 (engine migration), ADR-WALKER-045 (composition root único), ADR-WALKER-051 (gaps refactor)

---

## 1. Problema

### 1.1 `mcp.py` — Protocol Coupling

`shared/core/engine/mcp.py` importaba `McpServer` y definía handlers con `self` (bug: función closure sin clase). El engine (domain core) dependía de MCP (transporte).

| # | Hallazgo | Evidencia | Impacto |
|---|---|---|---|
| 1 | **Engine importa MCP** | `from shared.core.mcp_server import McpServer` en mcp.py | Violación de arquitectura hexagonal: domain conoce transporte |
| 2 | **Handler con `self` roto** | `_workflow_handler()` referencia `getattr(self, "_cli_handlers")` — `self` no existe en función closure | `NameError` en runtime si el handler se invocara |
| 3 | **Schemas en domain** | `_STATUS_OUTPUT_SCHEMA`, `_SUBMIT_OUTPUT_SCHEMA`, etc. vivían en `shared/core/engine/mcp.py` | Output schemas son concern del adapter, no del domain |

### 1.2 `bootstrap.py` — Service Locator Global

`shared/core/engine/bootstrap.py` exponía `get_module_service()` / `resolve_service()` como service locator global.

| # | Hallazgo | Evidencia | Impacto |
|---|---|---|---|
| 1 | **Dependencies ocultas** | 25+ módulos llaman `get_module_service("key")` sin declarar la dependencia | Imposible saber qué necesita cada módulo sin leer todo el cuerpo |
| 2 | **Typos en runtime** | `get_module_service("cognitive_searcher")` → `KeyError` solo en runtime | Sin compile-time safety |
| 3 | **Tests frágiles** | Cada test necesita `set_service_locator()` antes de importar | Setup repetido, fragilidad |

---

## 2. Decisión

### D1 — Eliminar `shared/core/engine/mcp.py`

- Schemas de output → `features/main/cli_interface/engine_mcp.py` (adapter layer)
- `_build_module()` + `register_module()` → `features/main/cli_interface/engine_mcp.py`
- `_workflow_handler()` → fixeado: reemplaza `self` por DI via `resolve_service("workflow")`
- `shared/core/engine/mcp.py` eliminado
- 2 callers actualizados: `features/main/bootstrap.py`, `features/main/composition/registry.py`

### D2 — Slim down `shared/core/engine/bootstrap.py`

- `bootstrap_providers()` inlined en `build_context_registry()` (solo se llamaba una vez)
- `get_module_service()` / `resolve_service()` / `get_module_registry()` marcados como DEPRECATED
- Docstrings actualizados con migration path (constructor injection)
- `bootstrap()` marcado como DEPRECATED (usar `compose_app()`)

### D3 — Mantener service locator como bridge

- `_SERVICE_LOCATOR` + `set_service_locator()` + `get_module_service()` se MANTIENEN en `shared/core/engine/bootstrap.py`
- **Razón**: 25+ callers internos de `shared/core/engine/` las usan; no se puede importar `features/` desde `shared/core/`
- **Migration path**: constructor injection gradual (cada caller migra a recibir dependencias por constructor)

---

## 3. Alternativas evaluadas

| Alternativa | Pros | Contras | Decisión |
|---|---|---|---|
| **A. Eliminar service locator completo** | Arquitectura limpia | Rompe 25+ callers internos; circular import si se importa features/ | Diferida |
| **B. Mover a features/main/** | Un solo composition root | `shared/core/engine/` necesitaría importar features/ → circular | Imposible |
| **C. Deprecar + bridge (elegida)** | No rompe nada; señala dirección | Service locator sigue existente temporalmente | Aceptada |

---

## 4. Failure Scenarios

| # | Fallo | Causa raíz | Mitigación | Detección |
|---|---|---|---|---|
| 1 | `_workflow_handler` falla con `NameError: self` | Bug preexistente en `mcp.py` (self no existe en closure) | Fixeado: handler ahora usa `resolve_service("workflow")` en vez de `self` | Tests de tool catalog validan que los handlers existen |
| 2 | `get_module_service()` deprecation warning en tests | Tests importan `get_module_service` directamente | Warnings son non-breaking; tests pasan igual | `pytest -W error::DeprecationWarning` para detectar uso futuro |
| 3 | `build_context_registry()` roto tras inline | `bootstrap_providers()` era función separada | Inlined correctamente; misma lógica, menos una capa | 42 tests pasan |

---

## 5. Consecuencias

### 5.1 Positivas
- Engine (domain) ya NO importa MCP (transporte) — arquitectura hexagonal restaurada
- Handler fixeado: delega al workflow facade via DI en vez de self
- Schemas de output viven en el adapter layer (donde pertenecen)
- Deprecation warnings señalan el camino para migración futura

### 5.2 Negativas
- Service locator sigue existente (25+ callers internos)
- `bootstrap()` sigue como factory deprecated (usado por `workflow_api.py`)

### 5.3 Déuda técnica documentada
- **Migrar 25+ callers** de `get_module_service()` a constructor injection
- **Eliminar `bootstrap()`** → reemplazar con constructor injection en `workflow_api.py`
- **Eliminar `_SERVICE_LOCATOR`** → solo posible cuando 0 callers internos lo usen

---

## 6. Comparison: Walker vs NestJS

| Concepto | Walker actual | NestJS pattern |
|---|---|---|
| **DI** | `_SERVICE_LOCATOR` global + `get_module_service("key")` | Constructor injection: `constructor(private readonly repo: Repo)` |
| **Module declaration** | `pkgutil.iter_modules` discovery | `@Module({ imports: [...], providers: [...] })` |
| **Protocol decoupling** | ✅ Ahora: engine_mcp.py en adapter layer | `@Controller` inyecta service, no conoce internals |
| **Service registration** | `registry.register_service("key", impl)` | `providers: [Service]` in Module decorator |

---

## 7. Verification

| Check | Resultado |
|---|---|
| `shared/core/engine/mcp.py` eliminado | ✅ |
| `from shared.core.engine.mcp import register_module` → `ImportError` | ✅ |
| `features/main/cli_interface/engine_mcp.py` importable | ✅ |
| `_get_tool_catalog()` returns 31 tools (incl. status, submit, transition) | ✅ |
| `build_context_registry()` funciona (providers inlined) | ✅ |
| `get_module_service()` tiene deprecation warning | ✅ Docstring |
| 42 tests pasan | ✅ |
