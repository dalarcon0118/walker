# ADR-WALKER-068 — Módulo host `app/` refinado: una responsabilidad por módulo (NestJS/Angular)

**Estado:** DRAFT — en refinamiento con el usuario (no aprobado).
**Fecha:** 2026-08-30
**Relacionados:** ADR-066 (module manifests DI declarativa), ADR-067 (engine puro + composition root), ADR-062 (puertos/DI), ADR-065 (tasks/actions/protocolos), ADR-051 (cross-feature orchestrators).

---

## 1. Contexto (problema)

`app/` es el módulo HOST del walker, pero **concentra 6 responsabilidades distintas** en un solo paquete:

| # | Responsabilidad | Archivos |
|---|---|---|
| 1 | Composición DI (AppModule) | `module.py`, `composition/` (registry, container, lifecycle, import_graph, errors) |
| 2 | API facade (submit/transition/status/prepare + gates) | `api/workflow_facade.py` |
| 3 | Orquestación cross-feature (14 services) | `orchestration/*` |
| 4 | Semántica de memorias (schemas/query/formatters) | `memory_schemas.py`, `memories.py`, `context_format.py`, `context_resolver.py` |
| 5 | Path resolution (SSOT) | `path_resolver.py` |
| 6 | Bootstrapping / entry points | `app.py`, `bootstrap.py`, `container.py`, `cognitive_registry.py` |

**Problema (antipatrón God-Module):** en NestJS/Angular un módulo tiene **UNA** responsabilidad, declara sus providers, **se auto-registra** (ModuleManifest, ADR-066) y es **empaquetable/pluggable** (se puede mover, versionar y reutilizar sin arrastrar al resto). `app/` hoy:
- Mezcla composición (infra), negocio (memorias), orquestación (use cases cross-feature) y utilidades (paths) en un solo paquete.
- No es empaquetable: `memory_schemas` depende de `shared/libs/cognitive_graph`, `path_resolver` de config, `orchestration` de features — pero todo vive como si fuera "el host" monolítico.
- El registro centralizado (módulo `app/module.py`) contradice el principio decidido: **cada módulo se auto-registra**.

---

## 2. Decisión — mapa Clean Design: qué es cada capa (NestJS/Angular + Clean Architecture)

**Análisis Clean Design (revisado):** las piezas de `app/` NO son "features" (bounded contexts). Clean Architecture mapea cada pieza a su capa:

| Pieza | Capa Clean | Naturaleza |
|---|---|---|
| `features/*` (code_analysis, cognitive_knowledge, delta_tracking, event_tracing, project) | **Bounded contexts** (dominio + aplicación + infra del contexto) | cada uno con `domain/`, `application/`, `infrastructure/`, `interface/` |
| Schemas de memorias (CPG/CKG/Delta/Trace) | **Contrato de persistencia del DOMINIO** de cada feature | la semántica de cada memoria la define SU feature dueña (patrón `forFeature` de NestJS) |
| `MemoryQueryService` + `COMPACT_FIELDS`/`SEVERITY_ORDER` | **Capa de aplicación del HOST** | servicio de aplicación que agrega 4 contextos (no es un bounded context) |
| `context_format.py` (formatters) | **Presentadores** (interface adapters) | formatean resultados por memoria |
| `orchestration/*` (14 G10) | **Capa de aplicación del HOST** | use cases cross-context (ADR-051) |
| `path_resolver.py` | **Utilidad de aplicación/infra del HOST** | SSOT de rutas (CoreModule `@Global`) |
| `workflow_facade.py` | **Interface adapter** | lo consumen los adapters CLI/MCP |
| `context_resolver.py` | **Interface adapter** (implementa `IContextValidator`) | adapter de un puerto del engine |
| `shared/libs/cognitive_graph` | **Gateway / framework** | storage genérico (empaquetable, host-agnostic) |

### 2.1 Conclusión del análisis: NO crear features artificiales

**`features/memories/`, `features/orchestration/`, `features/common/`, `features/cli_interface/` fueron un ERROR** de la propuesta anterior: no son bounded contexts (no tienen dominio propio). La semántica de memorias es un contrato de persistencia **distribuido entre las features dueñas** + un servicio de aplicación cross-cutting. Los manifiestos declarados en Fase 1 se **eliminan** (corrección documentada).

**Estructura objetivo (Clean + NestJS):**

| Módulo | Capa | Responsabilidad (UNA) | Ubicación |
|---|---|---|---|
| **Bounded contexts** | Domain | cada contexto con dominio/aplicación/infra propios | `features/*` (existentes) |
| **AppModule** | Composición | construir el engine (main/register_all), container, grafo | `app/module.py` + `app/composition/` |
| **AppApplication (memorias)** | Aplicación del host | `MemoryQueryService` + proyección (`COMPACT_FIELDS`/`SEVERITY_ORDER`) | `app/memories/` (módulo con manifest) |
| **AppApplication (orquestación)** | Aplicación del host | use cases cross-context (G10) | `app/orchestration/` (ya tiene manifest) |
| **AppCore (paths)** | Infra del host | SSOT de rutas | `app/path_resolver.py` (módulo de 1 archivo + export) |
| **Presenters** | Interface adapters | formatear resultados por memoria | por feature (formatea SU memoria) |
| **Adapters** | Interface adapters | facade + context_resolver | `protocols/cli/cli_interface/` |
| **Bootstrap/main** | Entry | `main.ts` análogo | `app/app.py`, `bootstrap.py`, `container.py` |

### 2.2 Reglas de boundaries

1. **AppModule (`app/`) importa** los bounded contexts + el engine (para componer). Los demás NO importan AppModule.
2. **Bounded contexts** NO se importan entre sí (cada feature resuelve sus deps por puertos/exports).
3. **AppApplication (memorias)** depende solo de `shared/libs/cognitive_graph` + `features/*` (los schemas de cada feature se registran en SU feature). NO importa app/composition.
4. **AppApplication (orquestación)** depende de features + app/memories + app/path. NO importa AppModule.
5. **PathResolver** es el SSOT: 0 construcciones de `.cognitive/` fuera de `app/path_resolver.py`.
6. Cada módulo se auto-registra vía `ModuleManifest` (ADR-066): `imports`, `providers`, `adapters`, `exports` explícitos. El registro NO se centraliza en `app/module.py`.

### 2.3 Movimientos concretos (refinado — forFeature)

1. **Schemas → a las features dueñas** (patrón `forFeature`):
   - schema **CPG** → `features/code_analysis/`
   - schema **CKG** → `features/cognitive_knowledge/`
   - schema **Delta** (+ref) → `features/delta_tracking/`
   - schema **Trace** + search handler + descentralizado → `features/event_tracing/`
2. **`MemoryQueryService` + `COMPACT_FIELDS`/`SEVERITY_ORDER`** → `app/memories/` (módulo de aplicación del host, manifest propio).
3. **`context_format.py`** → presentadores por feature (cada feature formatea SU memoria) o `app/presenters/` (decisión Q2b).
4. **`workflow_facade.py` + `context_resolver.py`** → `protocols/cli/cli_interface/` (adapters).
5. **`orchestration/`** → se queda en `app/orchestration/` (capa de aplicación del host; ya declarado).
6. **`path_resolver.py`** → se queda en `app/` (módulo de 1 archivo + export por contrato).
7. **`app/module.py`** → solo AppModule: ya no registra providers de dominios ajenos; cada módulo registra los suyos.
8. **ELIMINAR** los manifiestos erróneos de Fase 1: `features/memories/`, `features/orchestration/`, `features/common/`, `features/cli_interface/` (corrección de la propuesta).

### 2.4 Invariantes y reglas del refinamiento

**Invariantes** (estado que DEBE mantenerse; si se rompen, el módulo deja de ser empaquetable):

| ID | Invariante | Qué protege |
|---|---|---|
| INV-W068-01 | Cada módulo de `app/` declara **una sola responsabilidad** en su ModuleManifest (`imports`/`providers`/`exports` explícitos) | God-Module; acoplamiento invisible |
| INV-W068-02 | El registro de providers es **por módulo** (auto-registro, ADR-066). `app/module.py` SOLO registra services de libs + módulos MCP + re-registra puertos del engine | Registro centralizado; acoplamiento al AppModule |
| INV-W068-03 | **AppModule no importa a ningún otro módulo de `app/`** (los módulos importan hacia abajo, nunca al AppModule) | Ciclos de dependencia; container irresoluble |
| INV-W068-04 | **Los bounded contexts (`features/*`) NO se importan entre sí**; el schema de cada memoria vive en SU feature dueña (forFeature) | Acoplamiento entre contextos; forFeature roto |
| INV-W068-05 | **AppApplication (memorias) y AppApplication (orquestación) no se importan mutuamente** (memorias→storage; orquestación→features+memorias) | Ciclo entre servicios de aplicación |
| INV-W068-06 | **PathResolver es el SSOT de rutas**: 0 construcciones de `.cognitive/` fuera de `app/path_resolver.py` | Paths divergentes; engine contra root distinto |
| INV-W068-07 | Los **moves de archivos no cambian contratos**: los importadores externos (CLI/MCP/features) se actualizan mecánicamente, sin cambio de firma | Regresión silenciosa por re-import |

**Reglas** (convenciones de proceso para mantener las invariantes):

- R1 — Todo módulo nuevo nace con su `ModuleManifest` ANTES de registrar providers (test de completitud por módulo).
- R2 — Un módulo solo importa los `exports` de otros módulos (nunca internos de otro paquete).
- R3 — El grafo de imports se valida en CI: aristas prohibidas (features↔features, `app/memories`→`app/composition`, X→`app/composition`).
- R4 — Los cross-feature orchestrators (G10) viven SOLO en la capa de aplicación (`app/orchestration`); si un servicio cruza 2+ features, va ahí, no a una feature.
- R5 — El `main`/bootstrap construye el container UNA vez (ADR-046); los módulos declaran, no ensamblan.
- R6 — Un feature registra SOLO SU índice en el storage genérico (forFeature): `code_analysis`→CPG, `cognitive_knowledge`→CKG, `delta_tracking`→Delta, `event_tracing`→Trace.

### 2.5 Estado actual vs estado deseado

| Aspecto | Estado actual | Estado deseado |
|---|---|---|
| **Estructura** | `app/` plano: 6 responsabilidades mezcladas | `app/` = AppModule + aplicación (memorias, orquestación) + core (paths) + bootstrap, cada pieza con boundaries |
| **Registro** | `app/module.py` registra providers de todo el host | Cada módulo se auto-registra (ModuleManifest); AppModule solo libs+MCP+puertos |
| **Semántica de memorias** | `memory_schemas.py` central en raíz de app (registra los 4 índices) | Schemas **por feature dueña** (forFeature); facade (`MemoryQueryService`) + proyección en `app/memories/` |
| **Facade** | `api/workflow_facade.py` suelto en `app/api/` sin manifiesto | `protocols/cli/cli_interface/` (adapters CLI/MCP) |
| **Orquestación** | 14 services en `app/orchestration/` (capa aplicación, declarado `app_orchestration`) | se queda en `app/orchestration/` — es la capa de aplicación del host (sin moves) |
| **Paths** | `path_resolver.py` utilidad en raíz | export por contrato (CoreModule `@Global`), SSOT verificado |
| **Composición** | `composition/registry.py` (697 ln) mezcla registro + construcción del engine | `register_all` DELGADO: solo compone; cada módulo registra lo suyo |
| **Acoplamiento** | negocio de memorias convive con composición (infra) en `app/` | Boundaries unidireccionales (INV-W068-03..06) verificados en CI; features no se importan entre sí |

---

## 3. Plan por fases (con métricas)

### Fase 1 — Declarar los módulos (sin mover código)  ⚠️ SUPERSEDIDA (corrección Clean Design)

- [x] M-03/M-06/M-02 se declararon como `features/memories/`, `features/orchestration/`, `features/common/`, `features/cli_interface/` (commit `c878d48`).
- ⚠️ **CORRECCIÓN (Clean Design):** esas piezas NO son bounded contexts → los 4 manifiestos se **eliminan** (se revierte su registro en `_register_module_manifests`). El fix de `trace_log` (bug real) SE MANTIENE.
- [x] Bonus: fix del tracing real (`trace_log` usaba `cg.append()` inexistente → `cg.store_many()`; todo el trace de producción se perdía silenciosamente).

### Fase 2 — Split forFeature: schemas a las features dueñas + `app/memories/` (aplicación)

- [ ] M-03 Cada feature registra SOLO SU schema en el storage genérico (forFeature, R6): CPG→`code_analysis`, CKG→`cognitive_knowledge`, Delta→`delta_tracking`, Trace→`event_tracing` — anti-patrón: God-Module de memorias.
- [ ] M-04 `MemoryQueryService` + `COMPACT_FIELDS`/`SEVERITY_ORDER` → `app/memories/` (módulo de aplicación del host con manifest) sin importar features internos — anti-patrón: Big Ball of Mud.
- [ ] M-05 `MemoryQueryService` inyectado por constructor (sin service locator) — anti-patrón: God Class.
- [ ] M-06 `context_format.py` → presentadores por feature (cada feature formatea SU memoria) — anti-patrón: Duplicate Code.

### Fase 3 — Adapters + orquestación consolidada

- [ ] M-01 Gates host-side atómicos en la facade (todo-o-nada antes de submit) — anti-patrón: efectos ocultos.
- [ ] M-05 OrchestrationModule (capa aplicación) sin reconstruir dominio del engine (consume puertos) — anti-patrón: God Class.
- [ ] M-06 Cada orchestrator exportado por id+contrato (ADR-062) — anti-patrón: Not Invented Here.
- [ ] M-02 `workflow_facade.py` + `context_resolver.py` → `protocols/cli/cli_interface/` (adapters) — anti-patrón: Shotgun Surgery.

### Fase 4 — AppModule delgado + PathResolver por contrato

- [ ] M-02 path_resolver SSOT: 0 construcciones de `.cognitive/` fuera de `app/path_resolver.py` — anti-patrón: Duplicate Code.
- [ ] M-06 AppModule solo compone (sin lógica de negocio) — anti-patrón: Accidental Complexity.
- [ ] M-01 Composición idempotente (register_all re-ejecutable) — anti-patrón: Transaction Script.

---

## 4. `failure_scenarios`

1. **Si un feature registra el schema de OTRA memoria** (p.ej. code_analysis registra trace), el forFeature se rompe y la feature dueña pierde el control de su contrato de persistencia. *Causa:* `register_default_memory_indexes` central sigue vivo. *Mitigación:* R6 (cada feature registra SOLO su índice) verificada por grep/test de registros. *Detección:* test que valida qué índices registra cada feature.
2. **Si AppModule centraliza registros de otros módulos** (regresión a `app/module.py` con providers ajenos), el auto-registro muere y el grafo de imports se acopla. *Causa:* prisas por "que funcione" sin declarar el módulo dueño. *Mitigación:* cada módulo declara su ModuleManifest ANTES de registrar providers; test de completitud por módulo. *Detección:* test que valida que `app/module.py` solo registra services de libs + módulos MCP.
3. **Si la facade y la orquestación se importan mutuamente** (facade→orchestrator y orchestrator→facade), hay ciclo y el container no resuelve. *Causa:* boundaries de §2.2 ignorados. *Mitigación:* regla de imports unidireccional verificada en el grafo (ADT-065 divergencia #4). *Detección:* `import_graph` detecta ciclo y falla el boot.
4. **Si PathResolver deja de ser SSOT** (alguien construye rutas a mano), los paths divergen entre módulos y el engine resuelve contra un project_root distinto al del host. *Causa:* atajo sin pasar por el resolver. *Mitigación:* convención + revisión de PRs; PathResolver exportado por contrato. *Detección:* grep de `\.cognitive/` hardcodeado fuera de `app/path_resolver.py`.

---

## 5. Checklist de segundo orden (AGENTS.md)

1. **¿Qué módulos importan este?** AppModule (raíz) compone; los bounded contexts no se importan entre sí; `app/memories` + `app/orchestration` son consumidos por los adapters (protocols) y tasks.
2. **¿Qué side effects dispara?** La re-estructuración NO cambia runtime (solo organiza paquetes + manifiestos). Los gates host-side (ya existentes) no cambian.
3. **¿Qué eventos de dominio publica?** Ninguno nuevo. Los eventos del engine (WorkflowEvent) no cambian.
4. **¿Qué tests dependen de este comportamiento?** Tests de composición (test_composition_root_d1, test_dynamic_discovery_bdd, test_workflow_production_di_bdd) validan el grafo de módulos; los tests de memorias (test_cpg_*, test_rag_pipeline_bdd) validan schemas forFeature.
5. **¿Qué cambia en MCP adapter, CLI, CKG, DeltaGraph, Trace?** Nada funcional: los paths de import (app.memory_schemas → features dueñas, app.memories → app/memories, workflow_facade → protocols) se actualizan mecánicamente; MCP/CLI consumen el facade (sin cambios de contrato).

---

## 5.5 Escenarios de validación

Escenarios de uso/aceptación que DEBEN cumplirse tras el refinamiento (además de los `failure_scenarios`):

1. **forFeature correcto:** `CognitiveGraph(register_defaults=...)` registra los 4 índices A TRAVÉS de las 4 features (cada una registra el suyo); un test valida el conjunto {cpg, ckg, delta, trace, ref}.
2. **Agregar un orchestrator cross-feature:** se crea `app/orchestration/xyz_service.py`, se exporta en el ModuleManifest de la capa de aplicación y queda disponible por id+contrato (ADR-062) sin tocar `app/module.py`.
3. **Boot de la app:** `register_all` construye el engine; el grafo de imports valida aciclicidad (INV-W068-03/05); `container.resolve("workflow")` devuelve el facade.
4. **Gate host-side en submit:** `WorkflowFacade.submit` ejecuta el gate de uncaptured_work ANTES de llamar al engine; si bloquea, el engine no se invoca (assert en test de composición).
5. **Re-registro de puertos:** el engine auto-registra `IWorkflowStore` (default host-agnóstico); el AppModule lo re-registra con el store cableado; `resolve_port(IWorkflowStore)` en host devuelve el store real (no el default).
6. **Hot reload / tests:** `reset_ports()` + re-`register_all` (idempotente) dejan el sistema en el mismo estado; los tests de composición (`test_composition_root_d1`, `test_dynamic_discovery_bdd`) siguen verdes.

---

## 6. Decisiones (resueltas por Clean Design + modelo NestJS)

Resueltas con Clean Architecture (cada pieza → su capa) + modelo NestJS (`src/order/` ↔ `features/order/`):

1. **Q1 — NO existe ni se crea un "feature memorias".** La semántica de memorias es un contrato de persistencia **distribuido**: cada feature dueña registra SU schema (forFeature, R6); el facade cross-cutting (`MemoryQueryService`) + proyección es **capa de aplicación del host** (`app/memories/`).
2. **Q2 — `context_resolver.py` → `protocols/cli/cli_interface/`.** Es un interface adapter (implementa `IContextValidator`) — vive con los adapters CLI/MCP, no en app/ ni en un feature.
3. **Q3 — Orquestación → queda en `app/orchestration/`.** Es la capa de aplicación del host (use cases cross-context G10). NO se mueve a features (no es un bounded context).
4. **Q4 — PathResolver → se queda en `app/`** (módulo de 1 archivo + export por contrato, equivalente `@Global()` de NestJS). NO features/common.
5. **Q5 — Declarar ahora, mover por fases con tests verdes entre fases.** Fase 1 declaró los módulos; el análisis Clean Design la SUPERSEDE (se eliminan los 4 manifests de features/* y se ejecuta la Fase 2 forFeature).

**Nota de arquitectura (Clean):** `features/` = bounded contexts (con dominio propio); `app/` = AppModule + capa de aplicación del host + core utils; `protocols/` = interface adapters; `shared/` = domain core (engine) + gateways (libs). NO crear features artificiales para piezas que no son bounded contexts.

---

## 7. Archivos impactados (moves propuestos — Fase 2 forFeature)

| Actual | Destino | Módulo/capa | Tipo de cambio |
|---|---|---|---|
| `app/memory_schemas.py` (335 ln) | **split**: schema CPG → `features/code_analysis/`; CKG → `features/cognitive_knowledge/`; Delta → `features/delta_tracking/`; Trace → `features/event_tracing/` | Bounded contexts (forFeature, R6) | 🔀 split por feature dueña |
| `app/memories.py` (68 ln) | `app/memories/` (MemoryQueryService) | Capa de aplicación del host | 📦 move + ➕ manifest |
| `app/context_format.py` (120 ln) | formatters por feature (cada feature formatea SU memoria) | Presenters (interface adapters) | 🔀 split |
| `app/api/workflow_facade.py` | `protocols/cli/cli_interface/` | Adapters | 📦 move |
| `app/context_resolver.py` (78 ln) | `protocols/cli/cli_interface/` | Adapters (IContextValidator) | 📦 move |
| `app/orchestration/*` (14 + contracts) | **sin move** (capa de aplicación del host) | AppApplication | 🔧 consolidar manifest |
| `app/path_resolver.py` (592 ln) | **sin move** (módulo de 1 archivo + export por contrato) | AppCore | ➕ export |
| `app/module.py` | `app/module.py` (AppModule) | AppModule | 🔧 solo libs+MCP+puertos |
| `app/composition/registry.py` (697 ln) | sin move | AppModule | 🔧 `register_all` delgado |
| `features/memories/`, `features/orchestration/`, `features/common/`, `features/cli_interface/` (Fase 1) | **ELIMINAR** (corrección Clean Design) | — | 🗑️ revert manifiestos |

**Importadores a actualizar mecánicamente** (Fase 2): `app/memories.py` (memory_schemas), `features/delta_tracking/application/index_service.py`, `features/code_analysis/application/build_service.py`, `features/cognitive_knowledge/application/import_ckg_v1_cli.py`, `tasks/infrastructure/kg_context.py`, `protocols/cli/cli_interface/application/handlers.py`, `app/composition/registry.py`, `tests/*` — solo paths de import, sin cambios de firma (INV-W068-07).