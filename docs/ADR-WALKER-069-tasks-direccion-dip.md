# ADR-WALKER-069 — Dirección y refactor de `shared/tasks` (ServicesModule + DIP)

**Estado:** DRAFT (en revisión — el usuario manifiesta dudas; pendiente de aprobación)
**Fecha:** 2026-08-31
**Relacionados:** ADR-068 (app-module responsabilidades nestjs), ADR-067 (engine puro + composition root Ártico), ADR-062 (puertos/DI), ADR-066 (module manifests DI), ADR-065 (tasks/actions/protocolos), ADR-051 (cross-feature orchestrators).
**Contexto activo:** `features/workflow` consume puertos (M-04), `tasks/` movido a `shared/tasks/` (ADR-068 §9.7.2).

---

## 1. Pregunta a resolver (duda del usuario)

> *"Si el feature workflow es quien crea la instancia del workflow, y este recibe un registro de las tareas como constructor, ¿por qué entonces el feature workflow no importa el registro de tareas del módulo task?"*

Sub-pregunta arquitectónica (del hilo previo):
> *"Si task importa funcionalidades de los features, ¿es arquitectónicamente correcto que esté dentro de `shared/`? ¿o dentro de `features/` como servicio compartido?"*

---

## 2. Diagnóstico real (verificado sobre el código)

### 2.1 El feature workflow NO crea la instancia del workflow ni le inyecta las tasks

| Afirmación | Realidad (código) |
|---|---|
| "workflow crea la instancia de Workflow" | **Falso.** `WorkflowAPI.__init__(bundle: EngineBundle, ...)` recibe el `EngineBundle` construido por el **composition root** (`app/composition/registry.py:117`) — no por `features/workflow`. El motor es anémico (ADR-067): solo consume el bundle inyectado, nunca lo construye. |
| "workflow recibe el registro de tasks como constructor" | El `EngineBundle.actions: ActionCatalog` **es** el registro de tasks, pero lo arma app/: `app/module.py` hace `from shared.tasks.actions import register_task_execution_tasks` → puebla el `ActionCatalog` → `module_registry.get_service("action_catalog")` → `EngineBundle(actions=...)`. |
| "¿workflow importa tasks?" | **No** (verificado: `grep shared.tasks features/workflow` = vacío). |

**Flujo real (DIP):**
```
app/ (composition root)
  1. import shared.tasks.module / shared.tasks.actions → puebla ActionCatalog
  2. ActionCatalog → module_registry["action_catalog"]
  3. EngineBundle(actions=ActionCatalog) → WorkflowAPI(bundle)
        ▲
features/workflow
  - crea/usa WorkflowAPI (o el engine) pasando SOLO task_id
  - el engine despacha task_id → ActionCatalog → handler en shared/tasks
```

**Conclusión 2.1:** el acoplamiento `workflow ↔ tasks` NO es un import; es **resuelto por el composition root** (DIP, ADR-045/062). Por eso `features/workflow` no importa el registro de tasks: **lo recibe inyectado**. Hacer que workflow importara `shared/tasks` DUPLICARÍA el cableado que ya hace app/ y acoplaría un bounded context a una implementación.

### 2.2 Dirección real de dependencias de `shared/tasks`

| Importa | Realidad |
|---|---|
| `shared/tasks → shared/core/engine` | ✅ correcto (implementa `ActionCatalog`) |
| `shared/tasks → features/*` | ⚠️ **SÍ importa hoy** (`features.code_analysis`, `features.cognitive_knowledge`, `features.delta_tracking`, `features.project`) |
| `shared/tasks → app/*` | ⚠️ importa `app.memories`, `app.orchestration`, `app.path_resolver` |
| `shared/core/engine → shared/tasks` | ❌ NO (INV-W068-09 respetado) |
| `features/workflow → shared/tasks` | ❌ NO (DIP respetado) |

**El problema de diseño:** `shared/` es la **capa base** (solo deps internas de shared). Un módulo en `shared/` que importa `features/*` y `app/*` **mira hacia arriba** (hacia la capa de features y app), lo que invierte la jerarquía: `shared → features → app`. Esto es un **acoplamiento en la dirección equivocada** (nivel 1 del anillo de inversión).

---

## 3. Decisión sobre la UBICACIÓN (resuelve la sub-pregunta)

**`shared/tasks` debe vivir en `shared/`, NO en `features/`** — lo cual es correcto SI y solo si refactorizamos sus deps para que NO importe features.

| Ubicación | Veredicto |
|---|---|
| `features/tasks/` como bounded context | ❌ **Descartado**: tasks consume TODOS los features (CKG, Sprint, Project, CPG). Sería el único bounded context que importa a todos los demás = violación directa de INV-W068-04/R7 (los bounded contexts NO se importan entre sí). Su naturaleza transversal lo DESCARTA de la capa de bounded contexts. |
| `shared/tasks/` | ✅ **Correcto** como "servicio compartido" transversal SI se elimina el import a features (→ puertos). |
| `walker/tasks/` (raíz) | Era donde estaba; `shared/` es más estándar para un módulo compartido (resolución previa ADR-068 §9.7.2). |

---

## 4. Diseño objetivo: tasks por PUERTOS (no por features)

El refactor que hace viable `shared/tasks` es que **tasks implemente `ActionCatalog` + consuma puertos de servicios** (patterns ya aplicados en `features/workflow` M-04: `resolve_port(IProjectContext/ISprintService)`).

### 4.1 Estado actual de cada dep de tasks → features

| Dep actual de `shared/tasks` | Acción de diseño |
|---|---|
| `features.project` (16) | → resolver vía service-locator DI `module_registry.get_service("project_service")` o puerto `IProjectContext` |
| `features.cognitive_knowledge` CkgApplicationService (10) | → DI `get_service("ckg_service")` |
| `features.delta_tracking` (Sprint/Spring) | → puerto `ISprintService` (`resolve_port(ISprintService)`) |
| `features.code_analysis` (CPG) | → puerto `ICpgAnalysisPort` (YA existe `cpg_port_resolver`) |
| `app.orchestration.*` (side_effects, 15) | → mover a `app/` como adapters; tasks los invoca por contrato (SPEC-014) |
| `app.memories` / `app.path_resolver` (4) | → subir la utilidad a `shared/` (el SSOT de paths es del SISTEMA, no de app) |

### 4.2 Dirección post-refactor

```
shared/tasks ──► shared/core/engine/domain      (implementa ActionCatalog + consume puertos)
              ──► shared/ (memories, path_resolver — sistema)
              ──► module_registry.get_service("ckg_service"/"project_service")   (DI)
              ✗ NO features/*  ✗ NO app/*  (excepto adapters que viven en app/ y se inyectan)

features/*      ──► puertos en shared/core/engine/domain   (NUNCA importa shared/tasks)
features/workflow ──► engine (task_id) → dispatch → shared/tasks handler
app/ (composition root) cablea puertos ← adapters de features
```

### 4.3 Invariantes reforzados

- **INV-W068-09 (reformulado):** `shared/core/engine/` NUNCA importa `shared/tasks/`.
- **INV-W068-10 (nuevo):** `shared/tasks/` NUNCA importa `features/*` ni `app/*` directamente. Siempre por puertos (`ActionCatalog`, `IProjectContext`, `ISprintService`, `ICpgAnalysisPort`) o por DI (`module_registry.get_service(...)`). Antí-patrón: "shared que mira hacia features".

---

## 5. failure_scenarios

```json
[
  {
    "fallo": "shared/tasks intenta resolver el CKG por import y el MCP está caído",
    "causa_raiz": "acoplamiento directo a features.cognitive_knowledge",
    "mitigacion": "resolución por DI (get_service) o puerto; el fallo se degrada con log + fallback",
    "deteccion": "grep 'from features' en shared/tasks debe dar vacío (gate CI)"
  },
  {
    "fallo": "features/workflow importa shared/tasks y crea ciclo",
    "causa_raiz": "workflow 'necesita' handlers directamente",
    "mitigacion": "workflow NO importa tasks; inyecta por el engine (bundle recibe action_catalog del root)",
    "deteccion": "grep 'shared.tasks' en features/workflow debe dar vacío (gate CI)"
  },
  {
    "fallo": "shared/tasks se convierte en cajón de sastre de side_effects vs features",
    "causa_raiz": "side_effects apuntan a app.orchestration (orquestadores del HOST)",
    "mitigacion": "los orquestadores viven en app/ y tasks los invoca por contrato (SPEC-014); tasks no es dueño de la lógica del host",
    "deteccion": "revisión: side_effects solo invocan puertos/adapters, nunca acoplan la implementación del host"
  }
]
```

---

## 6. Pasos de refactor (fases, para el ritmo ADR)

1. **T2 (servicios por DI/puertos en actions)**: `features.project/cognitive_knowledge/delta_tracking` → `resolve_port` / `get_service`. (Análogo M-04 de workflow.)
2. **T3 (side_effects)**: mover `app.orchestration.*` a `app/` como adapters + tasks los invoca por contrato.
3. **T4 (utils a shared)**: `app.path_resolver`/`app.memories` (SSOT sistema) → `shared/`.
4. **Verificación**: `grep "from features|from app" shared/tasks` = vacío; compile; boot; tests.

---

## 7. Estado / aprobación

Este ADR está en **DRAFT**. Resuelve la duda del usuario (2.1: workflow NO importa tasks porque el composition root cablea; — 4: shared/tasks viable por puertos) y define el diseño a refinar. **Pendiente de revisión y aprobación antes de continuar con T2-T4.**
