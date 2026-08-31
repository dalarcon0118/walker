# ADR-WALKER-069 — `shared/tasks`: separar wiring (NestJS) de DIP-engine (ADR-067)

**Estado:** DRAFT v2 (rehecho — corrige mezcla de dos ejes ortogonales; pendiente de aprobación)
**Fecha:** 2026-08-31
**Relacionados:** ADR-068 (app-module responsabilidades nestjs), ADR-067 (engine puro + composition root), ADR-062 (puertos/DI), ADR-066 (module manifests DI), ADR-065 (tasks/actions/protocolos).
**Contexto activo:** `features/workflow` consume puertos (M-04), `tasks/` movido a `shared/tasks/` (ADR-068 §9.7.2).

---

## 0. Advertencia de modelado (por qué se rehace)

La v1 de este ADR mezcló **dos ejes ortogonales** y aplicó a uno una regla del otro:

| Eje | Qué es | En NestJS/Angular |
|---|---|---|
| **A. Wiring/DI (módulos)** | Cómo se organizan providers y se resuelven inyecciones (`SharedModule`, `CoreModule`, `forFeature`) | Un módulo **puede importar libremente a otros módulos** (incluidos los de "features"). No hay regla "el módulo compartido no importa features". |
| **B. DIP de capas** | Quién puede importar a quién por razón de arquitectura (engine anémico, ADR-067) | No es un concepto de NestJS; es hexagonal/arquitectura. |

**Error de la v1:** inventó la regla *"shared/tasks no debe importar features — porque es un SharedModule"*. Eso NO existe en NestJS. En NestJS, `SharedModule` (o cualquier módulo) puede importar `ConfigModule`, `HttpModule`, etc. sin problema. La restricción real viene de OTRO lado (el DIP del engine, ADR-067), y solo aplica a `shared/core/engine`, no a `shared/tasks`.

Este ADR separa ambos y deja cada decisión bajo su mecanismo correcto.

---

## 1. Pregunta original del usuario

> *"Si el feature workflow es quien crea la instancia del workflow, y este recibe un registro de las tareas como constructor, ¿por qué entonces el feature workflow no importa el registro de tareas del módulo task?"*

Sub-pregunta (hilo previo): *"Si task importa funcionalidades de los features, ¿es correcto que esté en `shared/`? ¿o en `features/` como servicio compartido?"*

---

## 2. Diagnóstico (verificado)

### 2.1 El feature workflow NO crea la instancia ni inyecta las tasks

| Afirmación | Realidad |
|---|---|
| "workflow crea la instancia de Workflow" | **Falso.** `WorkflowAPI.__init__(bundle: EngineBundle, ...)` recibe el bundle construido por el **composition root** (`app/composition/registry.py:117`). El motor es anémico (ADR-067): solo consume el bundle inyectado. |
| "workflow recibe el registro de tasks como constructor" | El `EngineBundle.actions: ActionCatalog` **es** el registro de tasks, pero lo arma app/ (`app/module.py`: `from shared.tasks.actions import register_task_execution_tasks` → `ActionCatalog` → `get_service("action_catalog")` → `EngineBundle(actions=...)`). |
| "¿workflow importa tasks?" | **No** (verificado: `grep shared.tasks features/workflow` = vacío). |

**Flujo real:**
```
app/ (composition root)
  1. importa shared/tasks → puebla ActionCatalog
  2. ActionCatalog → EngineBundle(actions=...) → WorkflowAPI(bundle)
features/workflow ──usa──► WorkflowAPI (pasa SOLO task_id; no conoce tasks)
```

**Conclusión 2.1 (eje B — DIP del engine):** `features/workflow` no importa tasks por el **DIP del engine (ADR-067)**: el motor anémico no conoce implementaciones; despacha por `task_id` → `ActionCatalog` → handler. workflow no tiene por qué conocer el registro de tasks — lo recibe **inyectado** en el bundle. Importarlo duplicaría el cableado del composition root y acoplaría workflow a una implementación.

### 2.2 Dirección real de dependencias de `shared/tasks`

| Dep | Realidad |
|---|---|
| `shared/tasks → shared/core/engine` | ✅ implementa `ActionCatalog` (correcto, dirección de implementación) |
| `shared/tasks → features/*` | ✅ **legítimo en el eje A (wiring)**: tasks ejecuta operaciones LLM que usan Application Services (CKG, Sprint, Project, CPG) — un servicio puede usar servicios de otros módulos. NO es violación. |
| `shared/tasks → app/*` (orchestration, memories, path_resolver) | ⚠️ a revisar (decisión de T3/T4), pero no es una violación de "shared no puede importar" |
| `shared/core/engine → shared/tasks` | ❌ prohibido por ADR-067 (engine no conoce negocio) — respetado |
| `features/workflow → shared/tasks` | ❌ no por be shared, sino por DIP engine (ADR-067) — respetado |

---

## 3. UBICACIÓN (eje A — wiring/decisión de módulo)

**`shared/tasks` en `shared/` ✓** — porque es un **módulo compartido** por workflow, CLI y MCP (transversal). Es la decisión de wiring de ADR-068 §9.7.2.

**Descartado `features/tasks/`:** no por "violar capas", sino porque tasks es **transversal** (lo usan workflow, CLI, MCP), no un bounded context con una sola responsabilidad de feature. En el modelo de módulos, algo transversal vive donde vive el shared feature module.

**Consecuencia del eje A:** por ser compartido, `shared/tasks` **puede** importar `features/*` (como cualquier módulo NestJS importa otros). La regla que impone la v1 —"shared no importa features"— es **INCORRECTA** y se elimina.

---

## 4. Reglas de dependencia que SÍ valen (eje B — arquitectura/DIP)

La única restricción dura de "no importar" en Walker es la del **engine anémico (ADR-067/INV-W068-09)**:

- **INV-W068-09 (renumerado y único):** `shared/core/engine/` NUNCA importa `shared/tasks/` ni ninguna implementación de negocio. El engine declara puertos (`ActionCatalog`, `IProjectContext`, `ISprintService`, `ICpgAnalysisPort`); tasks/features los implementan; el composition root (app/) cablea. Si el engine necesita comportamiento, es vía DI/del bundle, nunca por import.

**Se ELIMINA INV-W068-10** (el "shared/tasks no importa features" de la v1) — es un invento que no corresponde al modelo.

---

## 5. Lo que sí conviene refinar (decisiones de diseño, no reglas)

Sin la falsa regla de "shared no importa features", quedan decisiones de **calidad/desacoplamiento** (opcionales, nivel de implementación):

| Tema | Decisión propuesta |
|---|---|
| **T2** `shared/tasks/actions` llaman a features por import directo | Invocar por **service-locator DI** (`module_registry.get_service("ckg_service"/"project_service")`) para que tasks no dependa de nombres de clase de features en import-time — mismo beneficio que M-04 de workflow (testabilidad, desacoplamiento). Es una MEJORA, no una regla. |
| **T3** `side_effects → app.orchestration.*` | Los orquestadores son del HOST. Decidir si tasks los invoca por contrato (SPEC-014) o se suben a shared. Es diseño, no axioma. |
| **T4** `app.path_resolver`/`app.memories` | Si son utilidades del SISTEMA (rutas, memoria) y no del host, subirlas a `shared/`. Es decisión de pertenencia, no de capas. |

Ninguna de estas rompe un invariante; mejoran el desacoplamiento y la testabilidad.

---

## 6. failure_scenarios

```json
[
  {
    "fallo": "features/workflow importa shared/tasks y crea un ciclo/duplica cableado",
    "causa_raiz": "workflow 'necesita' los handlers directamente (perdió el DIP)",
    "mitigacion": "workflow NO importa tasks; recibe el engine con el bundle (action_catalog) del composition root",
    "deteccion": "grep 'shared.tasks' en features/workflow = vacío (gate CI)"
  },
  {
    "fallo": "shared/core/engine importa shared/tasks (o features) en un utility y rompe el motor anémico",
    "causa_raiz": "regresión al acoplar el engine a negocio",
    "mitigacion": "engine declara puertos; composition root cablea; gate CI prohíbe engine→tasks/features",
    "deteccion": "grep 'from shared.tasks|from features.' en shared/core/engine = vacío (gate CI)"
  },
  {
    "fallo": "tasks/actions importan clase de features en import-time y el contenedor no está listo",
    "causa_raiz": "coupling estático + orden de bootstrap",
    "mitigacion": "resolución lazy/DI (get_service); degrada con log si el service no está",
    "deteccion": "test de bootstrap / import aislado de shared.tasks"
  }
]
```

---

## 7. Estado / aprobación

**DRAFT v2.** Corrige la mezcla de conceptos de la v1: separa **wiring (eje A, NestJS)** de **DIP-engine (eje B, ADR-067)**, elimina INV-W068-10 y reduce las "restricciones" a la única que es real (engine anémico). Las decisiones T2-T4 quedan como mejoras opcionales de diseño, no como reglas. Pendiente de revisión del usuario para aprobar y refinar el diseño de T2-T4.
