# ADR-WALKER-050: Refinamiento de `features/main` — boundaries entre composition root, cross-feature orchestrators y librería reutilizable

> **Status:** Accepted
> **Fecha:** 2026-08-14
> **Autor:** Kilo + PO
> **Complementa:** ADR-WALKER-045 (composition root obligatorio), ADR-WALKER-046 (AppContainer lifetime), ADR-WALKER-047 (registry conflation), ADR-WALKER-048 (deterministic retrieval / capas estables), ADR-WALKER-048b (patrón canonical de capa de aplicación), SPEC-011 (unificación de capa de aplicación)

---

## 1. Problema

`features/main` concentra demasiadas responsabilidades sin boundaries claros. A su vez, los features de Walker usan estructuras inconsistentes para la capa de aplicación: algunos usan directorios `use_cases/`, otros archivos únicos, algunos tienen `services/`, otros no. El composition root importa use cases directos acoplándose a la estructura interna de features. No hay reglas claras sobre qué va en cada capa.

| Síntoma | Impacto |
|---------|---------|
| Dos composition roots paralelos (`bootstrap.py` + `container.py`) | Estados inconsistentes, singleton duplicado, imports rotos silenciosos |
| Tasks y side-effects importan Use Cases directos de features | Acoplamiento a la estructura interna de features; refactor futuro rompe main |
| `kg.py` tiene 775 líneas de lógica de negocio | God class dentro de una task; no es un thin adapter |
| `project_use_cases.py` contiene Application Services con nombre engañoso | Confusión: `PrepareProjectUseCase` no es un use case, es un orchestrator cross-feature |
| `_compose()` es un God Method de 60 líneas | Mezcla wiring, discovery, facade construction y service locator injection |
| Factories inconsistentes en DI | Algunas retornan clases, otras módulos, otras instancias; el consumer no sabe qué recibe |
| `cognitive_registry.py` vive en main | Configuración de índices de CognitiveGraph que no pertenece a main |
| `current_app.py` expone singleton global | Estado oculto, difícil de testear, acoplamiento implícito |
| `code_analysis/application/use_cases.py` es archivo único con 5 clases | Debería ser directorio `use_cases/` |
| `cognitive_knowledge/application/use_cases.py` es archivo único sin `services/` | Le falta fachada pública |
| `project/application/use_cases.py` + `service.py` duplican responsabilidades | Mezcla de implementación y fachada en archivos separados sin criterio claro |
| `event_tracing/application/search_service.py` es función suelta sin estructura | Sin `use_cases/` ni `services/` canonical |

**Preguntas sin respuesta:**
- ¿Qué código de `main` es código de `main` y qué es librería reutilizable?
- ¿Dónde están los gaps de responsabilidad que siguen ensuciando main?
- ¿Cuál es el patrón canonical de capa de aplicación que deben adoptar todos los features?

---

## 2. Decisión

**Refinar `features/main` boundaries: separar composition root (wiring), cross-feature orchestrators (application services), task layer (thin adapters) y subsistemas autónomos (prompts). Establecer reglas explícitas de qué es de main, qué es reutilizable y qué debe migrar a features individuales.**

### 2.1 Regla de propiedad de código

| Tipo | ¿De main? | ¿Reutilizable? | Razón |
|------|-----------|----------------|-------|
| **Composition root** (`container.py`, `bootstrap.py`) | ✅ Sí | ❌ No | Solo main conoce todos los features |
| **API facades** (`app.py`, `front_controller.py`, `workflow_facade.py`) | ✅ Sí | ❌ No | Son la cara pública de Walker |
| **Cross-feature orchestrators** (`TaskIntelligenceService`, `PrepareProjectService`, `CheckHealthService`) | ✅ Sí (hoy) | ⚠️ Futuro | Coordinan features sin pertenecer a uno |
| **Task adapters** (`task_execution/tasks/`) | ✅ Sí | ❌ No | Son verbos del workflow de Walker |
| **Side-effect handlers** (`task_execution/side_effects/`) | ✅ Sí | ❌ No | Son eventos del workflow de Walker |
| **CLI/MCP transport** (`cli_interface/`) | ✅ Sí | ❌ No | Son entry points de Walker |
| **Prompts** (`prompts/`) | ✅ Sí | ⚠️ Parcial | `PromptManager` y `protocol_builder` son genéricos; los templates son específicos |
| **Cognitive registry** (`cognitive_registry.py`) | ❌ No | ✅ Sí | Define schemas de índices; debe vivir en `libs/cognitive_graph/` o `shared/` |
| **Context providers** (`task_execution/context/providers.py`) | ✅ Parcial | ⚠️ Estructura | Los providers específicos de Walker son de main; la estructura del registry es reutilizable |

### 2.2 Regla de naming y estructura

**Cambiar `project_use_cases.py` por `orchestrators/`:**

```python
# ANTES: features/main/cli_interface/application/project_use_cases.py
class PrepareProjectUseCase:  # ← nombre engañoso
class CheckHealthUseCase:     # ← nombre engañoso

# DESPUÉS: features/main/cli_interface/application/orchestrators/
class PrepareProjectService:   # ← application service cross-feature
class CheckHealthService:      # ← application service cross-feature
```

**Cambiar `tasks/` para que sean thin adapters:**

```python
# ANTES: tasks/planning.py importa Use Cases directos
from features.delta_tracking.application.use_cases_sprint import PlanSprintUseCase

# DESPUÉS: tasks/planning.py consume Application Service
from features.delta_tracking.services import SprintApplicationService
svc = SprintApplicationService(...)
svc.plan_sprint(...)
```

### 2.3 Regla de eliminación de composition root legacy

- `bootstrap.py` y `compose_app()` se eliminan una vez que `container.py` y `AppContainer.build()` sean el único punto de construcción.
- `app.py` y `front_controller.py` migran de `AppComposition` a `AppContainer`.
- `current_app.py` se mantiene como puente transitorio hacia `AppContainer`.

### 2.4 Regla de extracción de lógica desde tasks

- `tasks/kg.py` (775 líneas) se refactoriza: la lógica de negocio (búsqueda multi-memoria, filtrado, scoring) se extrae a `cognitive_knowledge/application/services/ckg_service.py`.
- `tasks/kg.py` se convierte en un thin adapter que consume el `CkgApplicationService`.

### 2.5 Regla de separación de `_compose()`

`container.py::_compose()` se divide en:

```python
# composition/registry.py
def register_all(module_registry, task_registry, side_effect_engine, context_registry, workflow_facade):
    _register_libs_services(module_registry)
    _register_feature_services(module_registry)
    _register_cross_feature_services(module_registry)
    _register_task_execution_tasks(task_registry)
    _register_task_execution_side_effects(side_effect_engine)
    _discover_and_register_providers(context_registry)
    _discover_and_register_mcp_modules(module_registry)
    _register_engine_mcp(module_registry)

# composition/lifecycle.py
class AppContainer:
    @classmethod
    def build(cls, chain_dir="", force=False) -> "AppContainer": ...
    @classmethod
    def reset(cls) -> None: ...
    def child(self, chain_dir="", task_id="") -> RequestScope: ...
    def resolve(self, name: str) -> Any: ...

# container.py
class AppContainer:
    """Facade pública del composition root."""
    def __init__(self, workflow, task_registry, ...): ...
```

### 2.6 Regla de factories consistentes

Todas las factories en `_register_libs_services` retornan **instancias** (no clases, no módulos):

```python
# ANTES: inconsistente
registry.register_service("project_service", _build_project_service, lazy=True)  # retorna clase
registry.register_service("delta_spring_service", _build_delta_spring_service, lazy=True)  # retorna módulo
registry.register_service("cognitive_searcher", _build_searcher)  # retorna instancia

# DESPUÉS: consistente
registry.register_service("project_service", lambda: ProjectService(), lazy=True)  # instancia
registry.register_service("delta_spring_service", lambda: SpringService(), lazy=True)  # instancia
registry.register_service("cognitive_searcher", lambda: CognitiveGraphSearcher(...), lazy=True)  # instancia
```

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

**Migrations pendientes:**

| Feature | Archivo actual | Target |
|---------|---------------|--------|
| `code_analysis` | `application/use_cases.py` (único archivo, 5 clases) | `application/use_cases/` (directorio) |
| `cognitive_knowledge` | `application/use_cases.py` (único archivo) | `application/use_cases/` + `application/services/` |
| `project` | `application/use_cases.py` + `application/service.py` | `application/use_cases/` + `application/services/` |
| `event_tracing` | `application/search_service.py` (función suelta) | `application/use_cases/` + `application/services/` |

---

---

## 3. Arquitectura objetivo de `features/main`

```
features/main/
├── composition/                          ← SOLO WIRING (sin lógica)
│   ├── container.py                      ← AppContainer.build() + resolve() + child()
│   ├── registry.py                       ← Constantes de claves DI + register_all()
│   └── lifecycle.py                      ← Singleton, request scope, reset
│
├── api/                                  ← FACADES PÚBLICAS
│   ├── walker_app.py                     ← WalkerApp (API pública de Walker)
│   ├── front_controller.py               ← FrontController (CLI/MCP)
│   └── workflow_facade.py                ← WorkflowFacade (submit/transition/status/prepare)
│
├── cli_interface/                        ← TRANSPORTE
│   ├── mcp.py                            ← MCP server entry point
│   ├── mcp_adapter.py                    ← stdio adapter
│   ├── application/
│   │   ├── orchestrators/                ← Cross-feature SERVICES
│   │   │   ├── prepare_project_service.py
│   │   │   └── check_health_service.py
│   │   ├── handlers.py                   ← Request handlers
│   │   ├── presenter.py                  ← Response formatting
│   │   └── error_mapping.py              ← Error translation
│   ├── contracts/                        ← DTOs de transporte
│   ├── domain/                           ← Entidades de CLI/MCP
│   └── infrastructure/                   ← Output, session store, serializer, reloader
│
├── task_execution/                       ← WORKFLOW LAYER
│   ├── application/
│   │   ├── intelligence_service.py       ← TaskIntelligenceService (cross-feature)
│   │   ├── cpg_port_resolver.py          ← Factory
│   │   └── contracts/                    ← DTOs de inteligencia
│   ├── tasks/                            ← THIN ADAPTERS (sin lógica)
│   │   ├── planning.py                   ← delega en SprintApplicationService
│   │   ├── kg.py                         ← delega en CkgApplicationService
│   │   ├── cpg_analyze.py                ← delega en CpgApplicationService
│   │   └── ...
│   ├── side_effects/                     ← WORKFLOW EVENT HANDLERS
│   │   ├── status.py                     ← delega en SprintApplicationService
│   │   ├── validate.py                   ← delega en SprintApplicationService
│   │   └── ...
│   └── context/
│       └── providers.py                  ← Context providers
│
├── prompts/                              ← SUBSISTEMA AUTÓNOMO
│   ├── manager.py
│   ├── protocol_builder.py
│   ├── templates/
│   └── application/
│       └── ports/
│
└── cognitive_registry.py                 ← ELIMINAR/MOVER a libs/cognitive_graph/
```

---

## 4. Consecuencias

### 4.1 Positivas

- **Composition root único:** `container.py` es el único punto de construcción. No hay `bootstrap.py` paralelo.
- **Main no conoce implementación interna de features:** Tasks y side-effects consumen Application Services, no Use Cases directos.
- **Tasks son thin adapters:** `kg.py` se reduce a < 200 líneas; la lógica vive en `cognitive_knowledge`.
- **Naming honesto:** `PrepareProjectService` no se llama `PrepareProjectUseCase`.
- **Factories predecibles:** Todas retornan instancias; el consumer sabe qué recibe.
- **Librerías reutilizables:** `cognitive_registry.py` puede moverse a `libs/`; `PromptManager` puede usarse fuera de Walker.
- **Testeable:** `AppContainer` se puede construir en tests con mocks; no hay singleton global oculto.

### 4.2 Negativas / Trade-offs

- **Migración incremental:** no se puede hacer big-bang por el riesgo de romper el workflow.
- **Overhead de una capa más:** `orchestrators/` es una capa adicional entre `cli_interface` y los features, pero provee estabilidad contractual.
- **Renombrar `project_use_cases.py`:** rompe imports existentes; se mantiene alias transitorio durante la migración.

---

## 5. Mapa de archivos afectados

### Archivos que se reestructuran

| Archivo actual | Cambio |
|---|---|
| `features/main/container.py` | Dividir en `composition/container.py`, `composition/registry.py`, `composition/lifecycle.py` |
| `features/main/bootstrap.py` | Eliminar después de migrar `app.py` y `front_controller.py` |
| `features/main/app.py` | Migrar de `AppComposition` a `AppContainer` |
| `features/main/front_controller.py` | Migrar de `AppComposition` a `AppContainer` |
| `features/main/current_app.py` | Mantener como puente transitorio |
| `features/main/cli_interface/application/project_use_cases.py` | Renombrar a `orchestrators/prepare_project_service.py` + `orchestrators/check_health_service.py` |
| `features/main/task_execution/tasks/planning.py` | Consumir `SprintApplicationService` en vez de Use Cases directos |
| `features/main/task_execution/tasks/kg.py` | Extraer lógica a `cognitive_knowledge/application/services/ckg_service.py` |
| `features/main/task_execution/side_effects/status.py` | Consumir `SprintApplicationService` |
| `features/main/task_execution/side_effects/validate.py` | Consumir `SprintApplicationService` |
| `features/main/task_execution/application/intelligence_service.py` | Consumir Application Services de features, no Use Cases directos |
| `features/main/cognitive_registry.py` | Mover a `libs/cognitive_graph/` o `shared/` |

### Archivos nuevos

| Archivo | Responsabilidad |
|---|---|
| `features/main/composition/container.py` | `AppContainer.build()`, `resolve()`, `child()` |
| `features/main/composition/registry.py` | `register_all()` + helpers de discovery |
| `features/main/composition/lifecycle.py` | Singleton, reset, request scope |
| `features/main/api/workflow_facade.py` | Facade de workflow (submit/transition/status/prepare) |
| `features/main/cli_interface/application/orchestrators/prepare_project_service.py` | `PrepareProjectService` |
| `features/main/cli_interface/application/orchestrators/check_health_service.py` | `CheckHealthService` |
| `features/cognitive_knowledge/application/services/ckg_service.py` | `CkgApplicationService` (lógica extraída de kg.py) |

---

## 6. failure_scenarios

| # | Fallo | Causa raíz | Mitigación | Detección |
|---|---|---|---|---|
| 1 | `bootstrap.py` no se elimina después de migrar todo a `container.py` | Código legacy olvidado; algún entry point aún usa `compose_app()` | Linter/test: 0 imports de `features.main.bootstrap` desde entry points | `grep -r "from features.main.bootstrap" features/main/` |
| 2 | Tasks siguen importando Use Cases directos después de migrar el feature | Migración incompleta; devs nuevos copian el patrón viejo | Linter rule: prohibir imports de `features.<x>.application.use_cases` desde `features/main/task_execution/` | CI grep en `features/main/task_execution/` |
| 3 | `kg.py` vuelve a crecer porque se agrega lógica en el adapter | Falta de code review; el adapter no es un lugar obvio para lógica | Code review checklist: "si kg.py > 200 líneas, extraer a ckg_service"; test de completitud de responsabilidades | `wc -l features/main/task_execution/tasks/kg.py` |
| 4 | `cognitive_registry.py` se queda en main por error | Movido tarde en el proceso; imports rotos en `container.py` | Migrar `cognitive_registry.py` en el primer paso; actualizar imports | CI: `grep -r "cognitive_registry" features/main/` |
| 5 | Factories de DI retornan tipos inconsistentes | Refactor incompleto; alguna factory retorna clase en vez de instancia | Test de tipado: todas las factories retornan instancias; mypy/pyright estricto | CI typecheck |
| 6 | `current_app.py` se usa como bypass de `AppContainer` | Devs usan el singleton global por conveniencia | Documentar que `current_app.py` es transitorio; deprecated warning en runtime | Test: `get_current_app()` emite DeprecationWarning |
| 7 | `orchestrators/` se convierte en otro `project_use_cases.py` (monolith) | Falta de disciplina; se agrega lógica de negocio en vez de orquestación | Code review: orchestrators solo orquestan, no tienen lógica de negocio | Review checklist |

---

## 7. Checklist de segundo orden

1. **¿Qué módulos importan esto?**
   - `features/main/*` (todos los subdirectorios)
   - `features/cli_interface/*` (entry points)
   - `core/engine/*` (via service locator)
   - Tests E2E y de integración

2. **¿Qué side effects dispara?**
   - Ninguno nuevo. Los side effects existentes se mueven, no se crean nuevos.
   - El `SideEffectEngine` sigue registrando handlers en `task_execution/side_effects/`.

3. **¿Qué eventos de dominio publica?**
   - Ninguno nuevo. Los eventos de workflow y dominio siguen emitiéndose desde los mismos lugares.

4. **¿Qué tests dependen de este comportamiento?**
   - `tests/test_agent_e2e_bdd.py`
   - `tests/test_cli_walker_bdd.py`
   - `tests/test_rag_pipeline_bdd.py`
   - `tests/test_agent_session_bdd.py`
   - `tests/test_container_lifetime_bdd.py`
   - `tests/test_registry_completeness_bdd.py`

5. **¿Qué cambia en MCP adapter, CLI, CKG, DeltaGraph, Trace?**
   - MCP adapter y CLI: ahora consumen `AppContainer` en vez de `AppComposition`.
   - CKG/DeltaGraph/Trace: sin cambios de interfaz.
   - Engine: sin cambios; sigue consumiendo el service locator.

---

## 8. Implementación por fases

### Fase 1: Unificar composition root (sin cambiar arquitectura externa)

1. Migrar `app.py` y `front_controller.py` de `AppComposition` a `AppContainer`.
2. Eliminar `bootstrap.py` y `compose_app()`.
3. Actualizar entry points para usar `AppContainer.build()`.
4. Verificar: tests E2E pasan.

### Fase 2: Reestructurar `container.py`

1. Crear `composition/registry.py` con `register_all()`.
2. Crear `composition/lifecycle.py` con `AppContainer` (build/reset/child/resolve).
3. Crear `composition/container.py` como facade pública.
4. Mover `cognitive_registry.py` a `libs/cognitive_graph/` o `shared/`.
5. Verificar: tests E2E pasan.

### Fase 3: Renombrar `project_use_cases.py` a `orchestrators/`

1. Crear `cli_interface/application/orchestrators/prepare_project_service.py`.
2. Crear `cli_interface/application/orchestrators/check_health_service.py`.
3. Actualizar imports en `app.py`, `front_controller.py`, `workflow_facade.py`.
4. Eliminar `project_use_cases.py`.
5. Verificar: tests E2E pasan.

### Fase 4: Migrar tasks y side-effects a Application Services

1. **delta_tracking:** crear `SprintApplicationService` que envuelva los Use Cases de sprint.
2. **planning.py:** cambiar imports de Use Cases a `SprintApplicationService`.
3. **status.py** y **validate.py:** cambiar imports a `SprintApplicationService`.
4. **cognitive_knowledge:** crear `CkgApplicationService`.
5. **kg.py:** extraer lógica a `ckg_service.py`; `kg.py` consume `CkgApplicationService`.
6. **intelligence_service.py:** cambiar imports a Application Services.
7. Verificar: tests E2E pasan, 0 imports de use_cases desde `features/main/task_execution/`.

### Fase 5: Normalizar factories DI

1. Revisar todas las factories en `registry.py`.
2. Asegurar que todas retornan instancias (no clases, no módulos).
3. Actualizar consumers que asumen tipos inconsistentes.
4. Verificar: typecheck pasa, tests E2E pasan.

### Fase 6: Limpiar `current_app.py` y validar boundaries

1. Marcar `current_app.py` como deprecated.
2. Agregar lint/test que prohíbe imports de `use_cases` desde main.
3. Ejecutar suite completa E2E.
4. Verificar checklist de validación.

---

## 9. Referencias

- ADR-WALKER-045: `features/main` como composition root final.
- ADR-WALKER-046: `AppContainer` lifetime y request scoping.
- ADR-WALKER-047: Registry conflation layer violations — modelo refinado (TaskRegistry = SSOT, ModuleRegistry = DI).
- ADR-WALKER-048: Recuperación determinista de memoria (prompt context) — capas estables.
- ADR-WALKER-048b (`schemas/adrs/ADR-WALKER-048.json`): Patrón canonical de capa de aplicación — Use Cases + Application Services + Domain Services.
- SPEC-011: Unificación de capa de aplicación (Use Cases + Application Services + Domain Services).
- `capa_rout.md`: D1 (composition roots paralelos), D2 (dispatch), D3 (tasks usan servicios de módulo).
- `project.md`: INV-013 (Use Cases internos, Application Services públicos), INV-014 (main no es un feature, boundaries estrictos).

---

## Apéndice A: Fuente — ADR-WALKER-048b (patrón canonical de capa de aplicación)

> Fuente: `schemas/adrs/ADR-WALKER-048.json`
> Título: Patrón canonical de capa de aplicación: Use Cases + Application Services + Domain Services
> Estado: Accepted
> Fecha: 2026-08-14

### A.1 Problema

Los features de Walker usan estructuras inconsistentes para la capa de aplicación: algunos usan directorios `use_cases/`, otros archivos únicos, algunos tienen `services/`, otros no. El composition root (main) importa use cases directos acoplándose a la estructura interna de features. No hay reglas claras sobre qué va en cada capa.

**Evidencia:**
- `delta_tracking/application/use_cases/` tiene 5 archivos (maduro) pero `main/task_execution` los importa directamente
- `code_analysis/application/use_cases.py` es un archivo único con 5 clases mezcladas
- `code_analysis/application/services/` tiene 6 archivos con responsabilidades solapadas
- `cognitive_knowledge/application/use_cases.py` es un archivo único sin `services/`
- `project/application/use_cases.py` + `service.py` duplican responsabilidades
- `event_tracing/application/search_service.py` es una función suelta sin estructura
- `main/task_execution/tasks/planning.py` importa `PlanSprintUseCase` directamente desde `delta_tracking.application.use_cases_sprint`
- `main/task_execution/tasks/kg.py` tiene 775 líneas de lógica de negocio (no es thin adapter)
- `main/task_execution/application/intelligence_service.py` importa `QueryDeltaUseCase` y `GetTaskContextUseCase` directamente desde `delta_tracking.application.use_cases`

### A.2 Decisión

Adoptar el patrón Use Cases + Application Services + Domain Services como estructura canonical de capa de aplicación para todos los features de Walker. Clarificar que `features/main` NO es un feature, sino un composition root + cross-feature orchestrator que SÍ tiene sus propios Application Services pero NUNCA Use Cases individuales.

### A.3 Patrón

**Layers:**

| Layer | Ubicación | Responsabilidad | Visibilidad |
|-------|-----------|-----------------|-------------|
| Use Cases | `features/<feature>/application/use_cases/` | Lógica de negocio atómica. Una clase = una acción. Recibe puertos, devuelve entidades o `Result[T, E]`. | Interna al módulo |
| Application Services | `features/<feature>/application/services/` | Fachada pública del feature. Orquesta use cases, traduce entidades ↔ dicts. Punto de entrada para MCP, CLI, engine. | Pública |
| Domain Services | `features/<feature>/domain/services/` | Lógica pura sin IO. Invariantes, validaciones, transiciones de estado. | Pública |
| Cross-Feature App Services | `features/main/cli_interface/application/` y `features/main/task_execution/application/` | Orquestan lógica que cruza múltiples features. No pertenecen a ningún feature individual. Son Application Services, no Use Cases. | Pública |

**Reglas:**
1. Use Cases son implementación interna del módulo. Nadie fuera del feature debe importarlos directamente (INV-013).
2. Application Services y Domain Services son la API pública del feature.
3. El composition root registra solo Application Services y Domain Services. NUNCA registra Use Cases individuales.
4. Tasks y side-effects consumen Application Services, no Use Cases directos.
5. features/main NO es un feature: no tiene Use Cases, pero sí tiene Application Services cross-feature.
6. container.py registra: (1) Application Services de features, (2) Domain Services de features, (3) Application Services cross-feature de main, (4) Infrastructure, (5) MCP modules.
7. tasks/ son THIN ADAPTERS: no contienen lógica de negocio.

### A.4 Impacto y cambios

| Área | Antes | Después |
|------|-------|---------|
| `code_analysis/application/use_cases.py` | archivo único con 5 clases | directorio `use_cases/` con 5 archivos |
| `code_analysis/application/services/` | 6 archivos con responsabilidades mezcladas | consolidado: `services/` como fachada |
| `cognitive_knowledge/application/use_cases.py` | archivo único | directorio `use_cases/` + `services/` como fachada |
| `project/application/use_cases.py` + `service.py` | dos archivos con duplicación | `use_cases/` + `services/` sin duplicación |
| `event_tracing/application/search_service.py` | función suelta sin estructura | `use_cases/` + `services/` canonical |
| `main/task_execution/tasks/planning.py` | importa `PlanSprintUseCase` directo | consume `SprintApplicationService` |
| `main/task_execution/tasks/kg.py` | 775 líneas de lógica de negocio | thin adapter; lógica extraída a `ckg_service.py` |
| `main/task_execution/application/intelligence_service.py` | importa `QueryDeltaUseCase` y `GetTaskContextUseCase` directos | consume Application Services de features |
| `main/container.py` | registra `SpringService` + `ConflictResolver` | registra Services y Domain Services de todos los features |
| `main/bootstrap.py` | legacy composition root | eliminado o reemplazado por `container.py` |

### A.5 Non-negotiables

1. Use Cases son la implementación interna del feature, no parte de la API pública
2. Application Services son la fachada pública que registra el composition root
3. El composition root NUNCA registra Use Cases individuales
4. Tasks y side-effects consumen Application Services, no Use Cases directos
5. Domain Services contienen solo lógica pura sin IO
6. Application Services no contienen lógica de negocio, solo orquestación y traducción de formatos
7. La migración es incremental feature por feature, nunca big-bang
8. Cada feature migrado debe mantener backward compatibility durante la transición
9. Un Use Case = una clase = una acción atómica de negocio
10. Si un `use_cases.py` es un archivo único con múltiples clases, debe ser un directorio `use_cases/`
11. features/main NO es un feature: no tiene Use Cases, pero sí tiene Application Services cross-feature
12. container.py registra: (1) Application Services de features, (2) Domain Services de features, (3) Application Services cross-feature de main, (4) Infrastructure, (5) MCP modules
13. tasks/ son THIN ADAPTERS: no contienen lógica de negocio

---
