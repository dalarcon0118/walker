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

## 2. Decisión — `app/` como composición de módulos (NestJS/Angular)

`app/` deja de ser "el host monolítico" y pasa a ser **el AppModule raíz que COMPONE módulos**, cada uno con una responsabilidad, auto-registro (ModuleManifest) y boundaries claros. Cada módulo debe poder vivir en su propio paquete sin arrastrar a los demás.

### 2.1 Módulos propuestos

| Módulo | Responsabilidad (UNA) | Contiene | Auto-registro | Empaquetable |
|---|---|---|---|---|
| **AppModule** (`app/` raíz + `composition/`) | Componer: construir el engine (main/register_all), ciclo de vida, container, grafo de imports | `module.py`, `composition/` | registra services de libs + módulos MCP + re-registra puertos | ✅ |
| **WorkflowApiModule** (`app/api/`) | API surface del workflow para adaptadores (CLI/MCP): submit/transition/status/prepare + gates host-side | `workflow_facade.py` | providers exportados (facade) | ✅ |
| **OrchestrationModule** (`app/orchestration/`) | Casos de uso cross-feature (G10 ADR-051): orquestan ENTRE features | los 14 `*_service.py` + `contracts.py` + `module.py` | ya declarado (`app_orchestration`) | ✅ |
| **MemoryModule** (`app/memory/`) | Semántica de las memorias del walker (negocio): schemas inyectados al storage genérico, MemoryQueryService, formatters | `memory_schemas.py`, `memories.py`, `context_format.py` (+ `context_resolver.py`?) | ModuleManifest + exporta query service | ✅ |
| **PathModule** (`app/path_resolver.py` + config) | Única traducción identificadores→rutas (SSOT) | `path_resolver.py` + acceso a config | exporta resolver | ✅ |
| **Bootstrap/main** (NO módulo) | Entry point: `main.ts` análogo — arranca el container, dispara register_all | `app.py`, `bootstrap.py`, `container.py`, `cognitive_registry.py` | — (entry) | — |

### 2.2 Reglas de boundaries

1. **AppModule importa** WorkflowApiModule + OrchestrationModule + MemoryModule + PathModule + features (para construir el engine). Los demás NO importan AppModule.
2. **WorkflowApiModule** depende solo del engine (OrchestrationService vía puertos) + PathModule (chain_dir).
3. **OrchestrationModule** depende de features + PathModule + MemoryModule (cruza repos de memorias). NO importa AppModule ni WorkflowApiModule.
4. **MemoryModule** depende solo de `shared/libs/cognitive_graph` (storage genérico) + PathModule. NO importa features.
5. **PathModule** depende solo de config. Nada lo importa a él para rutas (es el SSOT).
6. Cada módulo se auto-registra vía `ModuleManifest` (ADR-066): `imports`, `providers`, `adapters`, `exports` explícitos. El registro NO se centraliza en `app/module.py`.

### 2.3 Movimientos concretos

- `memory_schemas.py`, `memories.py`, `context_format.py` → `app/memory/` (MemoryModule).
- `context_resolver.py` → decisión pendiente: ¿MemoryModule (si es contexto de memorias) o WorkflowApiModule (si es contexto de prompts)?
- `api/` → módulo propio con su ModuleManifest.
- `orchestration/` → ya tiene `module.py`; se consolida como módulo de primera clase.
- `path_resolver.py` → PathModule (puede quedarse en raíz de app/ como módulo de 1 archivo).
- `app/module.py` → solo AppModule: ya no registra providers de dominios ajenos; cada módulo registra los suyos.

### 2.4 Invariantes y reglas del refinamiento

**Invariantes** (estado que DEBE mantenerse; si se rompen, el módulo deja de ser empaquetable):

| ID | Invariante | Qué protege |
|---|---|---|
| INV-W068-01 | Cada módulo de `app/` declara **una sola responsabilidad** en su ModuleManifest (`imports`/`providers`/`exports` explícitos) | God-Module; acoplamiento invisible |
| INV-W068-02 | El registro de providers es **por módulo** (auto-registro, ADR-066). `app/module.py` SOLO registra services de libs + módulos MCP + re-registra puertos del engine | Registro centralizado; acoplamiento al AppModule |
| INV-W068-03 | **AppModule no importa a ningún otro módulo de `app/`** (los módulos importan hacia abajo, nunca al AppModule) | Ciclos de dependencia; container irresoluble |
| INV-W068-04 | **MemoryModule no importa `features/`** (solo `shared/libs/cognitive_graph` + PathModule) | Paquete no empaquetable; arrastra negocio |
| INV-W068-05 | **WorkflowApiModule y OrchestrationModule no se importan mutuamente** (WorkflowApi → engine; Orchestration → features) | Ciclo facade↔orchestrator |
| INV-W068-06 | **PathModule es el SSOT de rutas**: 0 construcciones de `.cognitive/` fuera de `app/path_resolver.py` | Paths divergentes; engine contra root distinto |
| INV-W068-07 | Los **moves de archivos no cambian contratos**: los importadores externos (CLI/MCP/features) se actualizan mecánicamente, sin cambio de firma | Regresión silenciosa por re-import |

**Reglas** (convenciones de proceso para mantener las invariantes):

- R1 — Todo módulo nuevo en `app/` nace con su `ModuleManifest` ANTES de registrar providers (test de completitud por módulo).
- R2 — Un módulo solo importa los `exports` de otros módulos (nunca internos de otro paquete).
- R3 — El grafo de imports se valida en CI: aristas prohibidas (app/memory→features, api↔orchestration, X→app/composition).
- R4 — Los cross-feature orchestrators (G10) viven SOLO en OrchestrationModule; si un servicio cruza 2+ features, va ahí, no a una feature.
- R5 — El `main`/bootstrap construye el container UNA vez (ADR-046); los módulos declaran, no ensamblan.

### 2.5 Estado actual vs estado deseado

| Aspecto | Estado actual | Estado deseado |
|---|---|---|
| **Estructura** | `app/` plano: 6 responsabilidades mezcladas | `app/` = AppModule + módulos (`api/`, `orchestration/`, `memory/`, `path_resolver`) |
| **Registro** | `app/module.py` registra providers de todo el host | Cada módulo se auto-registra (ModuleManifest); AppModule solo libs+MCP+puertos |
| **Semántica de memorias** | `memory_schemas.py`/`memories.py`/`context_format.py` en raíz de app | `app/memory/` (MemoryModule) con exports propios |
| **Facade** | `api/workflow_facade.py` suelto en `app/api/` sin manifiesto | WorkflowApiModule declarado, exporta la facade |
| **Orquestación** | 14 services en `app/orchestration/` (declarados como `app_orchestration`) | OrchestrationModule de primera clase con `contracts.py` + manifiesto consolidado |
| **Paths** | `path_resolver.py` utilidad en raíz | PathModule (export por contrato, SSOT verificado) |
| **Composición** | `composition/registry.py` (697 ln) mezcla registro + construcción del engine | `register_all` DELGADO: solo compone; cada módulo registra lo suyo |
| **Acoplamiento** | app/memory (negocio) convive con app/composition (infra) | Boundaries unidireccionales (INV-W068-03..06) verificados en CI |

---

## 3. Plan por fases (con métricas)

### Fase 1 — Declarar los módulos (sin mover código)

- [ ] M-03 Una responsabilidad por módulo declarada en ModuleManifest — anti-patrón: God-Module.
- [ ] M-06 Extensibilidad: cada módulo con `exports` explícitos (Open/Closed) — anti-patrón: Accidental Complexity.
- [ ] M-02 SSOT de la estructura: el grafo de imports refleja los 6 módulos (sin aristas nuevas) — anti-patrón: Shotgun Surgery.

### Fase 2 — MemoryModule (split de memorias)

- [ ] M-03 MemoryModule contiene SOLO semántica de memorias (schemas/query/formatters) — anti-patrón: Data Clumps.
- [ ] M-04 Sin imports de features en memory/* (desacoplamiento por contrato) — anti-patrón: Big Ball of Mud.
- [ ] M-05 `MemoryQueryService` inyectado por constructor (sin service locator) — anti-patrón: God Class.

### Fase 3 — API + Orchestration como módulos de primera clase

- [ ] M-01 WorkflowApiModule con gates host-side atómicos (todo-o-nada antes de submit) — anti-patrón: efectos ocultos.
- [ ] M-05 OrchestrationModule sin reconstruir dominio del engine (consume puertos) — anti-patrón: God Class.
- [ ] M-06 Cada orchestrator exportado por id+contrato (ADR-062) — anti-patrón: Not Invented Here.

### Fase 4 — PathModule + AppModule delgado

- [ ] M-02 path_resolver SSOT: 0 construcciones de `.cognitive/` fuera de PathModule — anti-patrón: Duplicate Code.
- [ ] M-06 AppModule solo compone (sin lógica de negocio) — anti-patrón: Accidental Complexity.
- [ ] M-01 Composición idempotente (register_all re-ejecutable) — anti-patrón: Transaction Script.

---

## 4. `failure_scenarios`

1. **Si MemoryModule queda importando features** (p.ej. schemas que referencian task_intelligence), el paquete deja de ser empaquetable → todo consumidor de memorias arrastra features. *Causa:* boundaries no verificados en Fase 2. *Mitigación:* grep CI que prohíbe `features` en `app/memory/*`. *Detección:* `grep -r "from features" app/memory` falla en CI.
2. **Si AppModule centraliza registros de otros módulos** (regresión a `app/module.py` con providers ajenos), el auto-registro muere y el grafo de imports se acopla. *Causa:* prisas por "que funcione" sin declarar el módulo dueño. *Mitigación:* cada módulo declara su ModuleManifest ANTES de registrar providers; test de completitud por módulo. *Detección:* test que valida que `app/module.py` solo registra services de libs + módulos MCP.
3. **Si WorkflowApiModule y OrchestrationModule se importan mutuamente** (facade→orchestrator y orchestrator→facade), hay ciclo y el container no resuelve. *Causa:* boundaries de §2.2 ignorados. *Mitigación:* regla de imports unidireccional verificada en el grafo (ADT-065 divergencia #4). *Detección:* `import_graph` detecta ciclo y falla el boot.
4. **Si PathModule deja de ser SSOT** (alguien construye rutas a mano), los paths divergen entre módulos y el engine resuelve contra un project_root distinto al del host. *Causa:* atajo sin pasar por el resolver. *Mitigación:* convención + revisión de PRs; PathModule exportado por contrato. *Detección:* grep de `\.cognitive/` hardcodeado fuera de `app/path_resolver.py`.

---

## 5. Checklist de segundo orden (AGENTS.md)

1. **¿Qué módulos importan este?** AppModule (raíz) importa los 4 módulos; features importan PathModule/MemoryModule (vía exports). Ningún módulo importa AppModule.
2. **¿Qué side effects dispara?** La re-estructuración NO cambia runtime (solo organiza paquetes + manifiestos). Los gates host-side (ya existentes) no cambian.
3. **¿Qué eventos de dominio publica?** Ninguno nuevo. Los eventos del engine (WorkflowEvent) no cambian.
4. **¿Qué tests dependen de este comportamiento?** Tests de composición (test_composition_root_d1, test_dynamic_discovery_bdd, test_workflow_production_di_bdd) validan el grafo de módulos; deben seguir verdes con los nuevos manifiestos.
5. **¿Qué cambia en MCP adapter, CLI, CKG, DeltaGraph, Trace?** Nada funcional: los paths de import (app.memory_schemas, app.memories, app.orchestration.*) se actualizan si hay moves; MCP/CLI consumen el facade (sin cambios de contrato).

---

## 5.5 Escenarios de validación

Escenarios de uso/aceptación que DEBEN cumplirse tras el refinamiento (además de los `failure_scenarios`):

1. **Consumir memorias sin arrastrar el host:** un nuevo feature importa `MemoryModule` (`from app.memory import MemoryQueryService`) y funciona SIN importar `app.composition` ni `features` — validado por grep de aristas prohibidas (R3).
2. **Agregar un orchestrator cross-feature:** se crea `app/orchestration/xyz_service.py`, se exporta en el ModuleManifest de OrchestrationModule y queda disponible por id+contrato (ADR-062) sin tocar `app/module.py`.
3. **Boot de la app:** `register_all` construye el engine; el grafo de imports valida aciclicidad (INV-W068-03/05); los 4 módulos se auto-registran; `container.resolve("workflow")` devuelve el facade.
4. **Gate host-side en submit:** `WorkflowFacade.submit` (WorkflowApiModule) ejecuta el gate de uncaptured_work ANTES de llamar al engine; si bloquea, el engine no se invoca (assert en test de composición).
5. **Re-registro de puertos:** el engine auto-registra `IWorkflowStore` (default host-agnóstico); el AppModule lo re-registra con el store cableado; `resolve_port(IWorkflowStore)` en host devuelve el store real (no el default).
6. **Hot reload / tests:** `reset_ports()` + re-`register_all` (idempotente) dejan el sistema en el mismo estado; los tests de composición (`test_composition_root_d1`, `test_dynamic_discovery_bdd`) siguen verdes.

---

## 6. Preguntas abiertas (para refinar)

1. **¿MemoryModule vive dentro de `app/` (`app/memory/`) o en `features/cognitive_knowledge/`?** (La semántica de memorias es negocio del walker — ¿dominio o host?)
2. **¿`context_resolver.py` va a MemoryModule o a WorkflowApiModule?** (¿contexto de memorias o contexto de prompts?)
3. **¿OrchestrationModule queda en `app/` o se mueve a un paquete propio?** (Es la capa G10 — ¿podría ser `features/orchestration/`?)
4. **¿PathModule necesita ser módulo o basta con un export de utilidad?** (Angular lo pondría en SharedModule; NestJS en CoreModule.)
5. **¿Se mueve código en este ADR o solo se declaran los módulos?** (Fases 2-4 implican moves — ¿los hacemos ahora o los dejamos como plan?)

---

## 7. Archivos impactados (moves propuestos)

| Actual | Destino | Módulo | Tipo de cambio |
|---|---|---|---|
| `app/module.py` | `app/module.py` (AppModule) | AppModule | 🔧 solo registra libs+MCP+puertos; providers ajenos se mueven a sus módulos |
| `app/composition/registry.py` (697 ln) | `app/composition/registry.py` | AppModule | 🔧 `register_all` delgado; delega registro por módulo |
| `app/composition/container.py`, `lifecycle.py`, `import_graph.py`, `errors.py` | sin cambio | AppModule | — |
| `app/api/workflow_facade.py` | `app/api/workflow_facade.py` + nuevo `app/api/module.py` | WorkflowApiModule | ➕ manifiesto propio (providers: facade) |
| `app/orchestration/*_service.py` (14) | sin cambio de path | OrchestrationModule | 🔧 consolidar `module.py` (exports por id+contrato) |
| `app/orchestration/contracts.py` (601 ln) | sin cambio | OrchestrationModule | — |
| `app/memory_schemas.py` (335 ln) | `app/memory/memory_schemas.py` | MemoryModule | 📦 move |
| `app/memories.py` (68 ln) | `app/memory/memories.py` | MemoryModule | 📦 move |
| `app/context_format.py` (120 ln) | `app/memory/context_format.py` | MemoryModule | 📦 move |
| `app/context_resolver.py` (78 ln) | `app/memory/` **o** `app/api/` (decisión Q2) | MemoryModule o WorkflowApiModule | 📦 move (pendiente) |
| `app/path_resolver.py` (592 ln) | sin cambio (módulo de 1 archivo) | PathModule | ➕ manifiesto/export por contrato |
| `app/app.py`, `bootstrap.py`, `container.py`, `cognitive_registry.py` | sin cambio | Bootstrap/main | — |

**Importadores a actualizar mecánicamente** (si hay moves): `app/orchestration/*` (memory_schemas), `app/api/*`, `features/*` (memories, context_format, memory_schemas, context_resolver), `tasks/*`, `protocols/cli_interface/*`, `tests/*` — solo paths de import, sin cambios de firma (INV-W068-07).