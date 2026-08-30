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
| `workflow_facade.py` | **Aplicación del host** | servicio de la API del workflow (lo consumen los adapters CLI/MCP por DI) |
| `app/context_resolver.py` | **Core del host (primitiva R1)** | resuelve la raíz de memorias (project_id/repo_root/data_dir) — NO es el adapter IContextValidator |
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
| **AppApplication (workflow API)** | Aplicación del host | facade submit/transition/status/prepare + gates | `app/api/` (WorkflowApiModule) |
| **AppCore (paths)** | Infra del host | SSOT de rutas (+ primitiva R1) | `app/path_resolver.py` + `app/context_resolver.py` (Fase 4) |
| **Presenters** | Interface adapters | formatear resultados por memoria | por feature (formatea SU memoria) |
| **Adapters** | Interface adapters | CLI/MCP/REST consumen la facade por DI | `protocols/cli/cli_interface/` |
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
4. **`app/api/`** → WorkflowApiModule del host (aplicación): manifest propio + export `workflow_facade` por contrato; protocols la consume por DI. (Corrección: no es adapter.)
5. **`app/context_resolver.py`** → core (primitiva R1; Fase 4 PathModule). (Corrección: no es adapter IContextValidator — ese es del engine.)
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
| INV-W068-08 | **Las features se AUTO-REGISTRAN su schema de memoria** (`register_memory_index` en su `module.py`); `app/` NO es dueña de ningún schema — solo pasa `register_defaults` al constructor del grafo (composición) | app conduciendo el registro; schemas fuera del dominio |

**Reglas** (convenciones de proceso para mantener las invariantes):

- R1 — Todo módulo nuevo nace con su `ModuleManifest` ANTES de registrar providers (test de completitud por módulo).
- R2 — Un módulo solo importa los `exports` de otros módulos (nunca internos de otro paquete).
- R3 — El grafo de imports se valida en CI: aristas prohibidas (features↔features, `app/memories`→`app/composition`, X→`app/composition`).
- R4 — Los cross-feature orchestrators (G10) viven SOLO en la capa de aplicación (`app/orchestration`); si un servicio cruza 2+ features, va ahí, no a una feature.
- R5 — El `main`/bootstrap construye el container UNA vez (ADR-046); los módulos declaran, no ensamblan.
- R6 — Un feature registra SOLO SU índice en el storage genérico (forFeature): `code_analysis`→CPG, `cognitive_knowledge`→CKG, `delta_tracking`→Delta, `event_tracing`→Trace.
- R7 — **NO hay lista central de paths de módulos**: cada `*/module.py` se auto-registra al importar (`register_manifest(MODULE)`, espejo del bootstrap del engine); el AppModule importa por referencia (NestJS `@Module({imports})`). ✅ implementado (commit `98e3548`).

### 2.6 Resolución del registro centralizado (NestJS/Angular)

**Problema:** `app/composition/registry.py` tenía `module_paths = ["features.code_analysis.module", ...]` — una **lista central de strings** que enumeraba todos los módulos (discovery centralizado), violando la modularidad que `shared` ya respeta (el engine auto-registra sus puertos al importar).

**Cómo lo resuelven NestJS y Angular:** los módulos SE DECLARAN a sí mismos (decoradores/`NgModule`) y el `AppModule` los importa **POR REFERENCIA** (`@Module({imports: [UsersModule, OrdersModule]})` — objetos tipados, no strings). El framework lee el metadata; no hay discovery por strings.

**Resolución implementada (`98e3548`):**
- `shared/libs/module_system/global_registry.py` — registro global de manifests (espejo de `ports_registry`).
- Cada `*/module.py` se auto-registra al final: `register_manifest(MODULE)` (al ser importado — como el bootstrap del engine).
- `_register_module_manifests` importa los módulos **por referencia** (imports estáticos) y aplica los manifests auto-registrados al ModuleRegistry (valida aciclicidad). **Sin lista central de strings.**

### 2.5 Estado actual vs estado deseado

| Aspecto | Estado actual | Estado deseado |
|---|---|---|
| **Estructura** | `app/` plano: 6 responsabilidades mezcladas | `app/` = AppModule + aplicación (memorias, orquestación) + core (paths) + bootstrap, cada pieza con boundaries |
| **Registro** | `app/module.py` registra providers de todo el host | Cada módulo se auto-registra (ModuleManifest); AppModule solo libs+MCP+puertos |
| **Semántica de memorias** | `memory_schemas.py` central en raíz de app (registra los 4 índices) | Schemas **por feature dueña** (forFeature); facade (`MemoryQueryService`) + proyección en `app/memories/` |
| **Facade** | `api/workflow_facade.py` suelto en `app/api/` sin manifiesto | `app/api/` = WorkflowApiModule del host (manifest + export por contrato) |
| **Context resolver (R1)** | `app/context_resolver.py` (primitiva de rutas) | core (Fase 4 PathModule) |
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

### Fase 2 — Split forFeature: schemas a las features dueñas + `app/memories/` (aplicación)  ✅ HECHA (commit `3c08bfc`)

**Hallazgo (SSOT violado):** hoy hay **DOS fuentes de schemas que se registran simultáneamente en producción**:
- `app/memory_schemas.py::register_default_memory_indexes` (ckg/delta/trace/ref + trace handler + COMPACT_FIELDS/SEVERITY_ORDER) — vía `register_defaults` del CognitiveGraph.
- `app/cognitive_registry.py::register_cognitive_indexes` (ckg/cpg/delta/trace con configs propias) — llamado DESPUÉS por `registry.py:445/475`, `kg_context`, `build_service`.

→ ckg/delta/trace se definen 2 veces con configs distintas ("cuál gana" indefinido); CPG solo en cognitive_registry; refs solo en memory_schemas.

**Solución (domain-first / SSOT — UNA fuente por memoria → feature dueña):**

| Memoria | Dueño (feature) | Piezas |
|---|---|---|
| **CKG** | `features/cognitive_knowledge/` | schema ckg (unificado de ambas fuentes) + `SEVERITY_ORDER` + proyección |
| **Delta** | `features/delta_tracking/` | schema delta (unificado) + proyección |
| **Trace** | `features/event_tracing/` | schema trace (unificado) + search handler + descentralizado + proyección |
| **CPG + refs** | `features/code_analysis/` | schema cpg (con fix FTS) + `cpg.ref.*` + proyección |
| **Composición + facade** | `app/memories/` (aplicación del host) | `apply_memory_indexes(graph)` (aplica lo auto-registrado) + `MemoryQueryService` — NO dueña de schemas (INV-W068-08) |

- [x] M-03 Cada feature registra SOLO SU schema en el storage genérico (forFeature, R6): CPG→`code_analysis`, CKG→`cognitive_knowledge`, Delta→`delta_tracking`, Trace→`event_tracing` — anti-patrón: God-Module de memorias.
- [x] M-04 **Eliminar `register_default_memory_indexes` y `register_cognitive_indexes`** → auto-registro por feature (`register_memory_index` con dedupe, G2) + `apply_memory_indexes(graph)` — anti-patrón: Big Ball of Mud / SSOT duplicado.
- [x] M-05 `MemoryQueryService` + `build_cognitive_graph`/`build_searcher` canónicas en `app/memories/` (G7: elimina la construcción replicada en kg_context/build_service/repos) — anti-patrón: God Class.
- [x] M-06 `COMPACT_FIELDS`/`SEVERITY_ORDER` split por feature (cada una su proyección/ranking, G4/G5); el host mergea para el constructor del grafo — anti-patrón: Duplicate Code.
- [x] M-02 Reconciliar las configs de ckg/delta/trace de las 2 fuentes en la feature dueña (P2: trace→memory_schemas, cpg→cognitive_registry/FTS, ckg/delta→merge) — anti-patrón: Shotgun Surgery.
- [x] B3 Fix `self.self.compact_fields` → `self.compact_fields` (storage engine) — anti-patrón: bug de runtime silencioso.
- [x] B4 `memory_data_dir` → `app/path_resolver` (SSOT de rutas) — anti-patrón: Duplicate Code.
- [x] B5/B6/B7: cache del ref, `except: pass` → log (K), dead code — anti-patrón: efectos ocultos.
- [x] G9 Test de completitud: `{ckg, cpg, delta, trace}` registrados desde sus features + dedupe/idempotencia (5 passed) — anti-patrón: omisión silenciosa.

### Fase 3 — Adapters + orquestación consolidada

**CORRECCIÓN (hallazgo):** la decisión previa (facade + context_resolver → protocols) estaba basada en premisas incorrectas:
- `app/context_resolver.py` NO es el adapter `IContextValidator` (ese es `shared/core/engine/application/context_resolver.py`, del engine). El de `app/` es una **primitiva de paths** (R1: `resolve_memory_context`/`resolve_data_dir`) → pertenece al core (Fase 4 PathModule), NO a protocols.
- `workflow_facade` es un **servicio de APLICACIÓN del host** (usa el engine + gates) — en NestJS los controllers (protocols) inyectan el service; el service vive en la capa de aplicación. NO se mueve a protocols (mezclaría capas).

**Fase 3 corregida (HECHA, commits `98eed05` + `42a27b0`):**
- [x] M-01 Gates host-side atómicos en la facade (todo-o-nada antes de submit) — anti-patrón: efectos ocultos.
- [x] M-05 OrchestrationModule (capa aplicación) sin reconstruir dominio del engine (consume puertos; spec_transform vía IWorkflowStore) — anti-patrón: God Class.
- [x] M-02 `app/api/` consolidado como WorkflowApiModule del host (`app/api/module.py` auto-registrado + export `workflow_facade` por contrato; protocols consume por DI).
- [x] M-06 Cada orchestrator exportado por id+contrato (ADR-062): los 14 registrados en registry.py + test de completitud (`test_orchestration_registry_bdd`) — anti-patrón: Not Invented Here.
- [ ] M-03 `app/context_resolver.py` → core (PathModule, Fase 4) como primitiva R1 — ✅ HECHO en Fase 4.

### Fase 4 — AppModule delgado + PathResolver por contrato  ✅ HECHA (commit `5095f22`)

- [x] M-02 path_resolver SSOT: la primitiva R1 (`context_resolver.py`) se consolidó en `app/path_resolver.py`; construcciones restantes de `.cognitive/` en `features/project` (dueño del layout) documentadas como P1 — anti-patrón: Duplicate Code.
- [x] M-06 AppModule solo compone (engine ingredients + libs + MCP + re-registro de puertos; sin providers de dominios ajenos) — anti-patrón: Accidental Complexity.
- [x] M-01 Composición idempotente (`register_port` reemplaza; manifests por dict overwrite; `reset_*` para hot reload/tests) — anti-patrón: Transaction Script.
- [x] M-03 `app/context_resolver.py` → `app/path_resolver.py` (primitiva R1 en el SSOT) — anti-patrón: Data Clumps.

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
2. **Q2 — `context_resolver.py` (app/) es primitiva R1 de rutas, NO adapter `IContextValidator`** (ese es del engine, `shared/core/engine/application/`). Se queda en app/ (core, Fase 4 PathModule). Y la `workflow_facade` es **aplicación del host** (no adapter) → `app/api/` = WorkflowApiModule.
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
| `app/api/workflow_facade.py` | `app/api/` (WorkflowApiModule del host — aplicación) | AppApplication | 🔧 consolidar manifest + export por contrato |
| `app/context_resolver.py` (78 ln) | core (primitiva R1, Fase 4 PathModule) | AppCore | 🔧 queda en app/ hasta Fase 4 |
| `app/orchestration/*` (14 + contracts) | **sin move** (capa de aplicación del host) | AppApplication | 🔧 consolidar manifest |
| `app/path_resolver.py` (592 ln) | **sin move** (módulo de 1 archivo + export por contrato) | AppCore | ➕ export |
| `app/module.py` | `app/module.py` (AppModule) | AppModule | 🔧 solo libs+MCP+puertos |
| `app/composition/registry.py` (697 ln) | sin move | AppModule | 🔧 `register_all` delgado |
| `features/memories/`, `features/orchestration/`, `features/common/`, `features/cli_interface/` (Fase 1) | **ELIMINAR** (corrección Clean Design) | — | 🗑️ revert manifiestos |

**Importadores a actualizar mecánicamente** (Fase 2): `app/memories.py` (memory_schemas), `features/delta_tracking/application/index_service.py`, `features/code_analysis/application/build_service.py`, `features/cognitive_knowledge/application/import_ckg_v1_cli.py`, `tasks/infrastructure/kg_context.py`, `protocols/cli/cli_interface/application/handlers.py`, `app/composition/registry.py`, `tests/*` — solo paths de import, sin cambios de firma (INV-W068-07).

---

## 8. Fase 2 — Documentación de diseño (análisis, propuesta, impacto, aceptación)

### 8.1 Análisis (domain-first)

**Las 4 memorias son el conocimiento base del walker**, cada una poseída por UN bounded context:

| Memoria | Qué es | Feature dueña (dominio) |
|---|---|---|
| **CPG** | Code Property Graph (símbolos del código) | `features/code_analysis/` |
| **CKG** | Knowledge Graph (decisiones/convenciones/patrones) | `features/cognitive_knowledge/` |
| **Delta** | Spring + specs del proyecto | `features/delta_tracking/` |
| **Trace** | Ejecución (workflow + agente + tools) | `features/event_tracing/` |

**Hallazgo crítico (SSOT violado):** el schema de las memorias se define en **DOS fuentes que se registran simultáneamente en producción**:

- `app/memory_schemas.py::register_default_memory_indexes(graph)` — registra ckg/delta/trace/ref + trace search handler + trace descentralizado + `COMPACT_FIELDS`/`SEVERITY_ORDER`. Se inyecta vía `CognitiveGraph(register_defaults=...)` (registry.py:442/474).
- `app/cognitive_registry.py::register_cognitive_indexes(cg)` — registra ckg/cpg/delta/trace con **configs propias** (`ckg_index_config`/`cpg_index_config`/`delta_index_config`/`trace_index_config`). Se llama DESPUÉS en registry.py:445/475, `kg_context.py:88`, `build_service.py:303`, `cognitive_cpg_repo.py:133`.

**Consecuencias:**
- ckg/delta/trace se registran **2 veces con configs que difieren** → "cuál gana" es indefinido (depende del orden).
- CPG solo se registra en `cognitive_registry` (memory_schemas NO tiene schema cpg).
- `cpg.ref.*` (CPGs de referencia) solo en `memory_schemas`.
- `COMPACT_FIELDS`/`SEVERITY_ORDER` centralizados en `app/`, cuando son semántica de cada memoria.

### 8.2 Propuesta de solución (domain-first / SRP / SSOT) — diseño COMPLETO

**Principio SSOT:** cada memoria se define en UNA sola fuente → su feature dueña. El host SOLO compone.

**Cada feature dueña expone:**
- `register_<memoria>_index(graph)` — función pura que registra SU schema (reconciliando las 2 fuentes actuales).
- `<MEMORIA>_COMPACT_FIELDS` — su proyección (consistente con su schema, G4).
- `SEVERITY_ORDER` — solo CKG (ranking obligatoria/recomendada/informativa).

| Archivo nuevo (feature) | Contiene |
|---|---|
| `features/cognitive_knowledge/schema.py` | `register_ckg_index(graph)` + `CKG_COMPACT_FIELDS` + `SEVERITY_ORDER` |
| `features/delta_tracking/schema.py` | `register_delta_index(graph)` + `DELTA_COMPACT_FIELDS` |
| `features/event_tracing/schema.py` | `register_trace_index(graph)` (schema + search handler + descentralizado) + `TRACE_COMPACT_FIELDS` |
| `features/code_analysis/schema.py` | `register_cpg_indexes(graph)` (cpg + `cpg.ref.*`) + `CPG_COMPACT_FIELDS` |

**Auto-registro (espejo de los manifests — §2.6):**

| | Manifests (implementado) | Schemas de memoria (Fase 2) |
|---|---|---|
| Definición | `module.py` declara `MODULE` | `features/*/schema.py` define `register_<memoria>_index(graph)` |
| Auto-registro | `register_manifest(MODULE)` al final del `module.py` | `register_memory_index(register_<memoria>_index)` en `features/*/schema.py` (con dedupe, G2) |
| Gatillo | el `module.py` se importa | el `module.py` de la feature importa SU `schema.py` |
| Aplicación | `registry.register_manifest(m)` por cada auto-registrado | `apply_memory_indexes(graph)` drena el collector (register_defaults del grafo) |

**Mecanismo:** el collector `register_memory_index(fn)` vive en `shared/libs/module_system/global_registry.py` (infra genérica de auto-registro — features→shared permitido; el CONTENIDO es walker). Dedupe por función (G2) + `reset_memory_indexes()` (hot reload, P5).

**Construcción canónica del grafo (G7):** `app/memories/` expone:
- `apply_memory_indexes(graph)` — drena el collector (NO es dueña de schemas, INV-W068-08).
- `build_cognitive_graph(chain_dir, backend)` / `build_searcher(chain_dir)` — UNA función canónica que construye el grafo con `register_defaults=apply_memory_indexes`. Reemplaza la construcción replicada (registry, kg_context, build_service, repos).
- `MemoryQueryService` — facade de query (constructor-injected).

**Consumidores:** los features/tasks que hoy construyen graphs directamente (kg_context, build_service, cognitive_*_repo) reciben la fábrica canónica INYECTADA (port `ICognitiveGraphFactory`) — elimina la replicación (G7), el doble registro (B1/B2) y los edges features→app de construcción.

**Merge de proyecciones:** el host mergea `<MEMORIA>_COMPACT_FIELDS` → `compact_fields` del grafo; `SEVERITY_ORDER` se inyecta solo para ckg (G5).

### 8.6 Resoluciones de bugs y gaps integradas en la Fase 2

| ID | Resolución |
|---|---|
| B1 | ForFeature: event_tracing/schema.py registra trace CON su handler; `register_cognitive_indexes` se ELIMINA → no hay segundo registro que lo pise |
| B2 | Reconciliación por memoria en la feature dueña (una fuente por índice); el orden deja de importar |
| B3 | Fix `self.self.compact_fields` → `self.compact_fields` en `shared/libs/cognitive_graph/__init__.py:212` (bug del storage, independiente de Fase 2) |
| B4 | `memory_data_dir` → usa `app/path_resolver` (una sola fuente de rutas, INV-W068-06) |
| B5 | Cache del payload del ref en el search handler (gzip 1 vez) |
| B6 | `_collect_project_native_trace`: `except: pass` → `logger.error` (K) |
| B7 | Eliminar dead code (`_ = es_query.get("match_all")`) |
| G1 | Una fuente por memoria + collector con dedupe → sin "último gana" |
| G2 | `register_memory_index` dedupe por función (idempotente ante hot reload) |
| G3 | `apply_memory_indexes` se invoca con el graph YA con `chain_dir` (constructor lo setea antes de `register_defaults`); refs leen `graph.chain_dir` |
| G4 | `CPG_COMPACT_FIELDS` definido en `code_analysis/schema.py` JUNTO al schema cpg (consistente) |
| G5 | `SEVERITY_ORDER` inyectado solo para el índice ckg |
| G6 | Full scan del trace: documentado como perf gap (no bloqueante; mejora futura con índices) |
| G7 | `build_cognitive_graph` canónica + port `ICognitiveGraphFactory` inyectado a los consumidores |
| G8 | Tests actualizados a `apply_memory_indexes`/build canónica |
| G9 | Test de completitud: conjunto `{ckg, cpg, delta, trace, ref}` + origen por feature |

### 8.7 Pendientes detectados en el refinamiento completo

- **P1 — Edges features→app preexistentes:** features/tasks importan `app.path_resolver` (+ hoy `app.cognitive_registry`) en ~15 lugares. La construcción canónica + port (G7) resuelve la construcción, pero `path_resolver` en `app/` sigue generando edges features→app. *Decisión:* resolver en Fase 4 (PathModule) o mover path_resolver a `shared/` (utilidad pura) — pendiente de decisión.
- **P2 — Reconciliación por memoria (decisión por índice):** trace → versión de `memory_schemas` (tiene el handler); cpg → versión de `cognitive_registry` (tiene el fix FTS); ckg/delta → merge de ambas (comparar fields/search). Requiere revisar cada config lado a lado.
- **P3 — Consumo de `SEVERITY_ORDER`:** verificar dónde lo usa el searcher (¿ranking de resultados ckg?) antes de inyectarlo solo para ckg.
- **P4 — Productor de `cpg.ref.*`:** documentar quién crea los CPG de referencia (¿task/index_repo?) — el schema los registra pero el productor no está documentado.
- **P5 — Hot reload/tests:** el collector necesita `reset_memory_indexes()` (como `reset_manifests`/`reset_ports`) para idempotencia entre tests.
- **P6 — Dependencia del trace descentralizado:** `event_tracing/schema.py` (forFeature) depende de `app.path_resolver.get_project_dir` → edge features→app (mismo P1).

### 8.3 Impacto — antes vs después

| Aspecto | Antes | Después |
|---|---|---|
| Schema CKG | 2 fuentes (memory_schemas + cognitive_registry) | 1: `features/cognitive_knowledge/schema.py` |
| Schema Delta | 2 fuentes | 1: `features/delta_tracking/schema.py` |
| Schema Trace | 2 fuentes + handler en memory_schemas | 1: `features/event_tracing/schema.py` (schema+handler+descentralizado) |
| Schema CPG | solo `app/cognitive_registry.py` | `features/code_analysis/schema.py` |
| Refs (`cpg.ref.*`) | `app/memory_schemas.py` | `features/code_analysis/schema.py` |
| Proyección | `COMPACT_FIELDS` central en app | por feature (`<MEMORIA>_COMPACT_FIELDS`) |
| Ranking | `SEVERITY_ORDER` central | `features/cognitive_knowledge/schema.py` |
| Registro | 2 funciones solapadas (winner indefinido) | auto-registro por feature (`register_memory_index` en cada `module.py`) + `apply_memory_indexes` |
| Facade de query | `app/memories.py` (raíz) | `app/memories/` (módulo aplicación) |
| Consumidores | importan 2 fuentes distintas | importan las features dueñas o la agregación host |

**Archivos que se eliminan:** `app/memory_schemas.py` (split), `app/cognitive_registry.py::register_cognitive_indexes` + configs (movidas a features).

**Consumidores a actualizar (solo paths/firma, INV-W068-07):** `app/composition/registry.py` (442-475), `app/memories.py`, `tasks/infrastructure/kg_context.py`, `tasks/actions/trace_push.py`, `features/code_analysis/application/build_service.py`, `features/code_analysis/infrastructure/cognitive_cpg_repo.py`, `features/cognitive_knowledge/infrastructure/cognitive_ckg_repo.py`, `features/cognitive_knowledge/application/import_ckg_v1_cli.py`, `features/delta_tracking/infrastructure/cognitive_delta_repo.py`, `features/delta_tracking/application/index_service.py`, `protocols/cli/cli_interface/application/handlers.py`, `tests/*`.

### 8.4 Criterios de aceptación (verificables)

1. **forFeature (R6):** `CognitiveGraph(register_defaults=apply_memory_indexes)` registra el conjunto completo `{ckg, cpg, delta, trace, ref}` y cada schema proviene de SU feature. *Test:* test de completitud que valida los 5 índices registrados y su origen.
2. **SSOT + auto-registro:** `grep -r "register_default_memory_indexes\|register_cognitive_indexes"` → 0 resultados (funciones eliminadas); `grep -r "register_memory_index" features/*/module.py` → 1 por feature (cada una registra SOLO la suya); cada config de índice existe en UNA sola feature.
3. **Sin regresión de comportamiento:** los search de trace (handler + descentralizado) y los CPG de referencia (`cpg.ref.*`) funcionan igual tras el move (mismas firmas). *Test:* `test_cpg_*`, `test_rag_pipeline_bdd`, `test_migrated_gaps_bdd` verdes.
4. **Boot:** MCP self-test registra las 31 tools y el container construye (composición acíclica).
5. **Proyección:** `compact_fields`/`severity_order` del grafo contienen las 4 proyecciones merged por el host (nada se pierde del `COMPACT_FIELDS` actual).
6. **Nuevos módulos de feature:** `schema.py` de cada feature con `register_*_index` puro (sin side effects en import) — verificado por import sin construcción.
7. **Sin service locator:** `MemoryQueryService` recibe el grafo por constructor (grep: sin `resolve_port`/`get_service` dentro de `app/memories/`).

### 8.5 Bugs y gaps de diseño detectados en la Fase 2 (auditoría)

**Bugs (runtime, en producción HOY):**

| ID | Bug | Evidencia | Gravedad |
|---|---|---|---|
| B1 | **Search handler del trace PERDIDO**: `register_cognitive_indexes` corre DESPUÉS de `register_default_memory_indexes` en registry `_build_graph`/`_build_searcher`, `kg_context`, `build_service`; el backend REEMPLAZA el `Index` (`self._indexes[name] = Index(..., search_handler=None)`) → el handler custom del trace (filtros + descentralizado) se pierde | `file_backend.py:50`, `sqlite_backend.py:408` | 🔴 crítica |
| B2 | **Configs de ckg/delta sobrescritas**: el segundo registro pisa las configs de memory_schemas (fulltext delta, fields ckg) — "cuál gana" = orden de ejecución (indefinido) | doble registro en 3+ callers | 🔴 |
| B3 | **`self.self.compact_fields` (typo)** en `shared/libs/cognitive_graph/__init__.py:212` → AttributeError en runtime; la proyección COMPACT_FIELDS NUNCA funcionó (cada búsqueda compactada peta o cae al fallback) | línea 212 (llamada en línea 177) | 🔴 (del split de libs) |
| B4 | **`memory_data_dir` (cognitive_registry) duplica a `path_resolver`**: resuelve rutas vía `ProjectService.resolve_paths` fuera de `app/path_resolver` → SSOT de paths violado (INV-W068-06); importado por 6+ | `cognitive_registry.py:20` | 🟡 |
| B5 | **`_make_ref_search` descomprime gzip por query**: lee todo el archivo en cada búsqueda de ref, sin cache | `memory_schemas.py:190` | 🟡 perf |
| B6 | **`_collect_project_native_trace` con `except: pass` silencioso** (viola K): errores del trace descentralizado invisibles | `memory_schemas.py:127` | 🟡 |
| B7 | **Dead code**: `_ = es_query.get("match_all")` (no-op) en `_prepare_trace_filters` | `memory_schemas.py:57-58` | 🟢 |

**Gaps de diseño:**

| ID | Gap | Detalle |
|---|---|---|
| G1 | **Orden de registro indefinido** | el "último gana" hace que el schema dependa del orden de llamada — el forFeature debe definir UNA fuente con dedupe/conflicto explícito |
| G2 | **Collector `register_memory_index` sin idempotencia** | si un `module.py` se importa 2 veces (hot reload/tests), el registrar se duplica → aplicar el schema 2 veces; debe deduplicar (como `_MANIFESTS` por nombre) |
| G3 | **`cpg-ref-*` dinámicos por proyecto** | se registran leyendo `data/` en construcción → dependen de que el grafo se construya CON `chain_dir` ANTES de `apply_memory_indexes`; orden a garantizar |
| G4 | **`COMPACT_FIELDS["cpg"]` sin schema cpg en la misma fuente** | la proyección referencia un índice que memory_schemas no registra (solo cognitive_registry) — tras el split, cada feature expone proyección CONSISTENTE con su schema |
| G5 | **`SEVERITY_ORDER` global pero solo aplica a CKG** | el grafo recibe severity_order globalmente; el split debe inyectarlo solo donde aplica |
| G6 | **Trace handler hace FULL SCAN por query** | lee todos los eventos (perf gap conocido, no bloqueante) |
| G7 | **Construcción de grafo replicada en 4+ puntos** | registry (2 factories), kg_context, build_service, cognitive_*_repo construyen CognitiveGraph con doble registro — el wiring debe ser UNA función canónica (DRY) |
| G8 | **Tests construyen graphs con `register_default_memory_indexes`** | el move rompe test_cpg_*, test_rag_pipeline_bdd, test_migrated_gaps_bdd, test_gw6 — deben pasar a `apply_memory_indexes` |
| G9 | **Sin test de completitud de índices** | hoy no se valida el conjunto `{ckg, cpg, delta, trace, ref}` — por eso el doble registro pasó desapercibido (patrón de completitud de AGENTS.md) |

**Nota:** B3 es del STORAGE ENGINE genérico (`shared/libs/cognitive_graph`) — regresión del split de libs; se corrige con la Fase 2 (o antes, es independiente). B1/B2 se resuelven con el forFeature + eliminación de `register_cognitive_indexes` (una sola fuente por memoria).