# Nodo START — Bootstrap Node Multi-Tenant (Start Event BPMN)

> **Versión:** 1.0-draft  
> **Base:** workflow_v3_design.md, workflow_v3_enterprise.md  
> **Estado:** Propuesta de diseño  
> **Objetivo:** Agregar un nodo `START` como "start event" BPMN que sirva de entry point multitenant al walker, permitiendo seleccionar o crear proyectos antes de entrar al ciclo SPRINT.

---

## Tabla de Contenidos

1. [Contexto y problema](#1-contexto-y-problema)
2. [Diseño actual vs deseado](#2-diseño-actual-vs-deseado)
3. [Nodo START — Especificación](#3-nodo-start--especificación)
4. [Sub-estados detallados](#4-sub-estados-detallados)
5. [Gates y transiciones](#5-gates-y-transiciones)
6. [Prompt injection y context providers](#6-prompt-injection-y-context-providers)
7. [Integración con `walker3 status`](#7-integración-con-walker3-status)
8. [Integración con sesiones multi-tenant](#8-integración-con-sesiones-multi-tenant)
9. [Impacto en el workflow existente](#9-impacto-en-el-workflow-existente)
10. [Plan de implementación](#10-plan-de-implementación)
11. [Diagrama de flujo completo](#11-diagrama-de-flujo-completo)

---

## 1. Contexto y problema

### 1.1 Situación actual

El walker3 implementa un workflow que comienza directamente en el nodo `SPRINT`:

```
initial_state: "SPRINT"  ← en workflow.json
```

Cuando el agente ejecuta `walker3 status` sin argumentos, el CLI muestra un dashboard de proyectos (implementado recientemente en `cmd_status`), pero este dashboard **no es parte del workflow formal**. No hay:
- Inyección de contexto estructurado al LLM vía `prompt_id`
- Sub-estados con `required_fields` validables
- Transiciones formales hacia PROJECT_INIT o SPRINT
- Un "start event" BPMN que represente la entrada al sistema

### 1.2 Problemas detectados

| # | Problema | Impacto |
|---|----------|---------|
| 1 | El entry point del workflow es `SPRINT`, no hay un nodo `START` de "bienvenida" | El agente no recibe contexto de qué proyectos existen al arrancar |
| 2 | `PROJECT_INIT` existe pero solo es alcanzable desde `SPRINT.ACTIVE` mediante gate `init_project` | No hay forma de crear un proyecto desde el entry point |
| 3 | El dashboard de `walker3 status` es solo texto CLI, no inyecta contexto al LLM | El LLM no tiene acceso estructurado a la lista de proyectos |
| 4 | No hay un sub-estado `SELECT` para elegir proyecto existente | El agente no tiene un flujo guiado para seleccionar proyecto |
| 5 | La sesión activa se auto-crea con el primer proyecto disponible (WorkflowAPI.ensure_session paso 4) | El agente no elige explícitamente en qué proyecto trabajar |

### 1.3 Visión deseada

```
walker3 status / walker3 start
        │
        ▼
┌─────────────────────────────┐
│    NODO PROJECT (entry)     │  ← Start event BPMN
│                             │
│  INIT → auto: list projects │
│  SELECT → LLM: elige/crea   │
│  CONTINUE → auto: go SPRINT │
└─────────────────────────────┘
        │
        ▼
┌─────────────────────────────┐
│    NODO SPRINT (existente)  │  ← Sprint lifecycle
│    PLANNING → ACTIVE → ...  │
└─────────────────────────────┘
```

---

## 2. Diseño actual vs deseado

### 2.1 Principios arquitectónicos

| # | Principio | Implicancia |
|---|-----------|-------------|
| 1 | **El workflow ES la orquestación** | El engine ejecuta nodos y sub-estados secuencialmente. El agente no "llama al walker", el walker le presenta pasos via prompts. |
| 2 | **START es un hermano, no un padre** | START, PROJECT_INIT y SPRINT son nodos al mismo nivel en el grafo. START solo posiciona al agente, no absorbe ni duplica. |
| 3 | **Extensibilidad por conectividad** | Si mañana se necesita un nodo nuevo, se conecta via gate desde el sub-estado correspondiente. Los nodos existentes no se modifican. |
| 4 | **START es delgado** | No implementa creación de proyectos ni sprints. Solo crea el contexto inicial para que el agente sepa dónde está parado. |

### 2.2 Workflow actual (simplificado)

```
                  ┌──────────────────┐
                  │   SPRINT.INIT    │ ← initial_state
                  │   (auto)         │
                  └────────┬─────────┘
                           │
                  ┌────────▼─────────┐
                  │  SPRINT.CHECK    │
                  │  (auto)          │
                  └────────┬─────────┘
                           │
                  ┌────────▼─────────┐
          ┌───────┤  SPRINT.CREATE   │
          │       │  (llm_task)      │
          │       └────────┬─────────┘
          │                │
          │       ┌────────▼─────────┐
          │       │  SPRINT.ACTIVE   │──────→ (ciclo ANALYZE/IMPLEMENT/VERIFY)
          │       │  (auto/manual)   │
          │       └──────────────────┘
          │
          │       ┌──────────────────┐
          └──────→│  PROJECT_INIT    │ ← solo alcanzable desde SPRINT.ACTIVE
                  │  (para crear)    │
                  └──────────────────┘
```

### 2.3 Workflow deseado — Grafo de nodos hermanos

```
   initial_state: "START"


   ┌─────────────────────────────────────────────────────┐
   │                  NODO START (delgado)                │
   │                                                      │
   │  LIST (auto)                                         │
   │  → Escanea proyectos, prepara contexto               │
   │  → Avanza a SELECT                                   │
   │       │                                               │
   │       ▼                                               │
   │  SELECT (llm_task)                                   │
   │  → "Estos son tus proyectos. ¿Cuál eliges?"          │
   │  → Inyecta contexto: lista de proyectos, traces      │
   │  → El agente decide:                                 │
   │       │                                               │
   │       ├──→ submit project_selected + project_id X    │
   │       │    → gate project_selected → SPRINT          │
   │       │                                               │
   │       └──→ submit create_project + project_name Y    │
   │            → gate create_project → PROJECT_INIT      │
   │                                                      │
   │  (SELECT no pregunta specs ni nombre técnico —       │
   │   PROJECT_INIT lo hará cuando le toque)              │
   └──────────────────────────────────────────────────────┘
        │                      │
        │ project_selected     │ create_project
        ▼                      ▼
   ┌──────────┐        ┌──────────────────┐
   │  SPRINT  │        │  PROJECT_INIT    │ ← nodo hermano existente
   │  (existe) │        │  DETECT → ...    │    (no modificado)
   │          │        │  → FIRST_SPRINT  │
   └──────────┘        └────────┬─────────┘
                                │
                                │ project_ready
                                ▼
                           ┌──────────┐
                           │  SPRINT  │
                           └──────────┘
```

### 2.4 ¿Por qué START no absorbe PROJECT_INIT?

- **PROJECT_INIT es un nodo pesado** (9 sub-estados) con lógica especializada: detectar código legacy, escanear repos, construir CPG/CKG.
- **START es delgado**: solo posiciona al agente. Si mañana se necesita saltar START e ir directo a SPRINT (por ejemplo, reanudando una sesión), es posible.
- **Extensibilidad**: si en el futuro hay un nodo `AUTH` o `ONBOARDING`, se conecta desde `START.SELECT` sin modificar `PROJECT_INIT` ni `SPRINT`.
- **No duplicación**: `START.SELECT` solo pregunta "¿cuál proyecto?" — no pide specs. `PROJECT_INIT.SCENARIO` y `GATHER_SPECS` hacen eso cuando les toca.

---

## 3. Nodo START — Especificación

### 3.1 Principios de diseño

1. **START es delgado**: solo 2 sub-estados (LIST + SELECT). No tiene lógica de creación ni gestión de proyectos.
2. **START es hermano**: PROJECT_INIT y SPRINT están al mismo nivel. START transiciona a ellos via gates.
3. **El workflow orquesta**: el engine ejecuta START.LIST → START.SELECT → (gate) → siguiente nodo. El agente solo recibe prompts y responde con submit/transition.
4. **Sin duplicación**: SELECT no pregunta specs técnicas. Solo "¿qué proyecto?" o "¿crear nuevo?". PROJECT_INIT hace su trabajo completo cuando le toca.

### 3.2 Declaración del nodo

Archivo: `workflow.v3/nodes/start.node.json`

```json
{
  "label": "Start Bootstrap",
  "description": "Multi-tenant entry point (start event BPMN). Lists projects, lets agent select or create one. Thin node — delegates to PROJECT_INIT or SPRINT via gates.",
  "sub_states": [
    {
      "key": "LIST",
      "label": "List projects",
      "execution": "auto",
      "action": "list_projects",
      "input_schema": {},
      "output_schema": {
        "type": "object",
        "properties": {
          "projects": {
            "type": "array",
            "description": "All available projects with sprint counts and active sprint info"
          }
        }
      },
      "on_success": "SELECT",
      "side_effects": [],
      "timeout_ms": 10000,
      "prompt_id": "start/list"
    },
    {
      "key": "SELECT",
      "label": "Select or create project",
      "execution": "llm_task",
      "input_schema": {
        "type": "object",
        "properties": {
          "project_id": {
            "type": "string",
            "description": "Existing project ID to continue working on"
          },
          "create_new": {
            "type": "boolean",
            "description": "Set to true to create a new project (goes to PROJECT_INIT)"
          },
          "project_name": {
            "type": "string",
            "description": "Name for new project (optional hint for PROJECT_INIT)"
          }
        }
      },
      "output_schema": {
        "type": "object",
        "properties": {
          "decision": {
            "type": "string",
            "enum": ["select", "create"],
            "description": "select: work on existing project. create: initialize new project."
          },
          "project_id": {
            "type": "string",
            "description": "Selected project ID (required if decision=select)"
          }
        },
        "required": ["decision"]
      },
      "on_success": "",
      "on_failure": "",
      "side_effects": [],
      "timeout_ms": 600000,
      "prompt_id": "start/select",
      "context": {
        "sources": [
          {
            "provider": "projects",
            "config": {}
          },
          {
            "provider": "trace",
            "config": {
              "max_events": 5
            }
          }
        ],
        "focus": {
          "role": "bootstrapping — choose where to go",
          "constraints": [
            "Show all available projects from PROJECTS layer with sprint status",
            "Show recent activity from TRACE layer for context",
            "If there is a project with an active sprint, recommend it",
            "START is thin — it only decides WHERE to go, not HOW to create"
          ],
          "context_priority": "projects → trace"
        }
      },
      "audit": {
        "capture_decision": true,
        "level": "full"
      }
    }
  ]
}
```

**Nota:** START solo tiene LIST y SELECT. No hay CONTINUE porque SELECT transiciona directamente via gate al nodo hermano correspondiente (PROJECT_INIT o SPRINT). La transición la maneja el engine, no un sub-estado intermedio.

### 3.3 ¿Qué NO hace START?

| No hace | Por qué |
|---------|---------|
| ❌ Preguntar escenario (from_code/greenfield/spec) | Eso es responsabilidad de `PROJECT_INIT.SCENARIO` |
| ❌ Recabar specs técnicas del proyecto | Eso es `PROJECT_INIT.GATHER_SPECS` |
| ❌ Escanear código o construir CPG/CKG | Eso es `PROJECT_INIT.SCAN_CODE` y `BUILD_CKG` |
| ❌ Crear sprints | Eso es `PROJECT_INIT.FIRST_SPRINT` o `SPRINT.PLANNING` |
| ❌ Gestionar sesiones | El engine lo hace via side-effects en las gates |
| ❌ Decidir si va a PLANNING o ACTIVE | El engine lo determina según si el proyecto tiene sprint activo |

### 3.4 Declaración de edges (nuevas gates)

```json
[
  {
    "from": "START",
    "to": "SPRINT",
    "gate": "project_selected",
    "sub_state_gate": "PLANNING",
    "description": "Existing project selected. Engine decides: PLANNING (no sprint) or ACTIVE (has active sprint)."
  },
  {
    "from": "START",
    "to": "PROJECT_INIT",
    "gate": "create_project",
    "sub_state_gate": "DETECT",
    "description": "Create new project. Delegates to PROJECT_INIT (sibling node). START does NOT duplicate PROJECT_INIT logic."
  },
  {
    "from": "START",
    "to": "START",
    "gate": "refresh",
    "sub_state_gate": "LIST",
    "description": "Refresh project list."
  },
  {
    "from": "SPRINT",
    "to": "START",
    "gate": "back_to_start",
    "sub_state_gate": "LIST",
    "description": "Return to start to switch projects."
  },
  {
    "from": "PROJECT_INIT",
    "to": "START",
    "gate": "back_to_start",
    "sub_state_gate": "LIST",
    "description": "Return to start from PROJECT_INIT (cancel creation)."
  }
]
```

---

## 4. Sub-estados detallados

### 4.1 `START.LIST` (auto)

**Propósito**: Escanear `.cognitive/projects/` y preparar la lista de proyectos para el LLM. Es un sub-estado puramente informativo — el resultado se inyecta como contexto en `START.SELECT`.

| Campo | Valor |
|-------|-------|
| `execution` | `auto` |
| `action` | `list_projects` |
| `prompt_id` | `start/list` |

**Flujo**: LIST se ejecuta automáticamente al entrar al nodo START. Escanea el directorio de proyectos, identifica sprints activos y fecha de última actividad, y avanza inmediatamente a SELECT.

**Salida esperada** (se inyecta en el prompt del siguiente sub-estado):
```json
{
  "projects": [
    {
      "project_id": "delta-skill",
      "sprints": 8,
      "active_sprint": "taskregistry-migration-20260724",
      "active_sprint_status": "in_progress",
      "last_activity": "2026-07-24T23:56:00Z"
    },
    {
      "project_id": "react-native-frontend",
      "sprints": 2,
      "active_sprint": null,
      "last_activity": "2026-07-22T19:12:00Z"
    }
  ]
}
```

### 4.2 `START.SELECT` (llm_task)

**Propósito**: El LLM recibe la lista de proyectos y decide: seleccionar uno existente o crear uno nuevo. Este es el único punto de decisión del nodo START.

| Campo | Valor |
|-------|-------|
| `execution` | `llm_task` |
| `prompt_id` | `start/select` |
| `required_fields` | `["decision"]` |

**Context providers**:
- `projects` → lista completa de proyectos con sprints activos
- `trace` → últimos 5 eventos del sistema (última actividad)

**Prompt template** (`prompts/start/select.prompt.json`):
```
START.SELECT — Punto de entrada

Proyectos disponibles:

{projects_list}

¿Qué deseas hacer?

  Opciones:
    1. Elegir un proyecto existente → submit --decision select --project_id <id>
    2. Crear un nuevo proyecto      → submit --decision create

Nota: Si eliges "crear", irás al nodo PROJECT_INIT que te guiará
en la creación completa del proyecto (escenario, specs, código, etc.).
```

**Importante**: SELECT no pregunta escenario, specs, ni ningún detalle técnico. Si el agente elige "crear", simplemente transiciona al nodo hermano `PROJECT_INIT` que se encarga del resto. START es solo el "front door".

---

## 5. Gates y transiciones

### 5.1 Gates del nodo START

| Gate | Desde | Hacia | Quién gatilla | Descripción |
|------|-------|-------|---------------|-------------|
| `project_selected` | `START.SELECT` | `SPRINT` | Engine (auto) | El engine lee `project_id` de `sub_state_data`, busca sprint activo, y transiciona a `SPRINT.PLANNING` o `SPRINT.ACTIVE` según corresponda |
| `create_project` | `START.SELECT` | `PROJECT_INIT.DETECT` | Engine (auto) | Transiciona al nodo hermano `PROJECT_INIT`. `START` no pasa datos técnicos — `PROJECT_INIT` los descubre por sí mismo |
| `refresh` | `START.SELECT` | `START.LIST` | Agent | Refrescar lista de proyectos (vuelve a LIST) |
| `back_to_start` | `SPRINT.*` | `START.LIST` | Agent | Volver a START para cambiar de proyecto |
| `back_to_start` | `PROJECT_INIT.*` | `START.LIST` | Agent | Cancelar creación y volver a START |

### 5.2 Flujo de decisión en SELECT

```
START.SELECT: LLM recibe prompt con lista de proyectos
  │
  ├── submit --decision select --project_id "delta-skill"
  │   → gate: project_selected
  │   → Engine busca sprint activo en delta-skill
  │       ├── hay sprint activo → SPRINT.ACTIVE
  │       └── no hay sprint    → SPRINT.PLANNING
  │
  └── submit --decision create
      → gate: create_project
      → PROJECT_INIT.DETECT  (nodo hermano, no hijo)
      → PROJECT_INIT ejecuta su flujo completo:
          DETECT → LEGACY_MAP → SCENARIO → GATHER_SPECS
          → BUILD_PROJECT → SCAN_CODE → BUILD_CKG → REVIEW
          → FIRST_SPRINT
      → gate: project_ready → SPRINT.ACTIVE
```

### 5.3 Diferencia clave: START no tiene CONTINUE

En la versión anterior de este diseño, START tenía un sub-estado `CONTINUE` que ejecutaba `switch_to_project`. Eso ya no es necesario porque:

- **La transición la maneja el engine**: cuando `SELECT` submit con `decision=select`, la gate `project_selected` se activa y el engine aplica `TransitionUseCase` que cambia `current_node` a `SPRINT` directamente.
- **Side-effects en la gate**: si se necesita cambiar la sesión activa, se hace via `on:{}` event o transform en la gate, no en un sub-estado intermedio.
- **START se mantiene delgado**: 2 sub-estados, nada más.

---

## 6. Prompt injection y context providers

### 6.1 Nuevo context provider: `projects`

Se necesita un nuevo provider que inyecte la lista de proyectos al prompt del LLM.

```python
class ProjectsContextProvider(ContextProvider):
    """Inyecta la lista de proyectos disponibles con sus sprints activos."""

    provider_id = "projects"

    def get_context(self, ctx, config) -> str:
        projects = list_projects()  # del walker_v3
        lines = []
        for p in projects:
            active = [s for s in p["sprints"] if s["status"] == "active"]
            if active:
                lines.append(f"  🟢 {p['project_id']} — sprint activo: {active[0]['id']}")
            else:
                n = len(p["sprints"])
                lines.append(f"  ⚪ {p['project_id']} — {n} sprints, ninguno activo")
        return "\n".join(lines) if lines else "  (no hay proyectos)"
```

### 6.2 Prompt templates

#### `prompts/project/list.prompt.json`
```json
{
  "id": "project/list",
  "type": "auto",
  "version": 1,
  "description": "List all projects — auto action output",
  "template": "PROJECT.LIST - Scanning available projects...\n\nFound {count} project(s). Ready for selection."
}
```

#### `prompts/start/select.prompt.json`
```json
{
  "id": "start/select",
  "type": "llm_task",
  "version": 1,
  "description": "Select or create a project — main entry decision point",
  "template": "START.SELECT — Punto de entrada\n\n{Projects list injected by context provider}\n\n¿Qué deseas hacer?\n\n  . submit --decision select --project_id '<id>'\n    → ir a ese proyecto (SPRINT)\n\n  . submit --decision create\n    → crear un nuevo proyecto (PROJECT_INIT)\n\n  . submit --decision refresh\n    → recargar lista de proyectos"
}
```

Nota: No hay prompt para CONTINUE porque START ya no tiene ese sub-estado. La transición la maneja el engine via gates.

---

## 7. Integración con `walker3 status`

### 7.1 Comportamiento actual vs deseado

| Comando | Actual | Deseado |
|---------|--------|---------|
| `walker3 status` | Dashboard CLI de proyectos (texto plano) | Entra al workflow en `START.LIST` → engine ejecuta step() → inyecta prompt al LLM |
| `walker3 <proyecto>/status` | Estado detallado del proyecto | Se mantiene igual (proyecto específico, bypass de START) |
| `walker3` (sin args) | Muestra help | Entra a `START.LIST` automáticamente |

### 7.2 Flujo de integración

Cuando el agente ejecuta `walker3 status` sin proyecto activo, el workflow orquesta:

```
1. walker3 status
2. cmd_status() sin args
3. WorkflowAPI.ensure_session()
   → No hay sesión activa
   → Crea SessionRecord bootstrap (sin project_id)
   → SESSION_DIR se usa como chain_dir virtual:
       ~/.walker/sessions/{session_id}/
   → Workflow carga workflow.json (canónico de delta-skill)
   → SprintState inicializado con initial_state = "START"
   → SprintState se persiste en session_dir (no en proyecto)
   → Retorna session_id
4. WorkflowAPI.step()
   → Engine carga SprintState: current_node = "START", current_sub_state = "LIST"
   → Ejecuta LIST (auto): action "list_projects"
   → Avanza a SELECT
5. step() devuelve prompt:
   "START.SELECT — Punto de entrada
    Proyectos disponibles: ...
    ¿Qué deseas hacer?"
6. El agente responde via submit
```

### 7.3 Modificación a `cmd_status`

```python
def cmd_status(args: list[str]) -> int:
    if not args:
        api = WorkflowAPI()
        sid = api.ensure_session()
        if not api.project_id:
            # No hay proyecto activo → START node (bootstrap)
            result = api.step()
            print(result.get("message", ""))
            return 0
        _output(api.status())
        return 0
    # ... resto igual (project/status, sprint/status)
```

**Nota:** No se necesita crear manualmente el SprintState. El engine lo inicializa automáticamente desde `workflow.initial_state`.

---

## 8. Integración con sesiones multi-tenant

### 8.1 Solución al gap de persistencia: session_dir como chain_dir virtual

**Problema:** El engine necesita `chain_dir` para cargar workflow y persistir SprintState. Durante START no hay proyecto, por lo tanto no hay chain_dir.

**Solución:** Usar `~/.walker/sessions/{session_id}/` como chain_dir virtual durante el nodo START.

```
~/.walker/sessions/{session_id}/
├── session.json           ← SessionRecord (ya existe)
├── sprint-state.json      ← SprintState temporal (NUEVO)
└── workflow.json          ← symlink al workflow canónico (NUEVO)
```

**Ventajas:**
- Reutiliza la infraestructura existente de sesiones
- No requiere crear nuevos directorios ni esquemas de persistencia
- SprintStateRepository.save/load funciona sin modificaciones (solo cambia el chain_dir)
- Al seleccionar proyecto, se migra SessionRecord.chain_dir al real y SprintState se copia

### 8.2 Ciclo de vida de sesión con START node

```
SIN PROYECTO ACTIVO (entrada inicial)
─────────────────────────────────────
SessionRecord {
  session_id: "sess-xxx",
  project_id: "",                    ← vacío
  chain_dir: "~/.walker/sessions/sess-xxx/",  ← virtual (session_dir)
  status: "bootstrap"
}
  → WorkflowRepository.load(session_dir)
      → No hay workflow.v3/ en session_dir
      → Fallback al workflow canónico de delta-skill
      → Carga initial_state = "START"
  → SprintStateRepository.load(session_dir)
      → No existe sprint-state.json aún
      → Crea SprintState()
      → Auto-inicializa: current_node = workflow.initial_state ("START")
      → current_sub_state = "LIST"
      → Persiste en session_dir/sprint-state.json
  → step() ejecuta LIST (auto) → avanza a SELECT
  → SELECT inyecta prompt con lista de proyectos

PROYECTO SELECCIONADO (via gate project_selected)
─────────────────────────────────────────────────
SessionRecord {
  session_id: "sess-xxx",
  project_id: "delta-skill",
  chain_dir: ".../delta/delta-skill/taskregistry-migration-20260724",  ← real
  status: "active"
}
  → Gate project_selected activa transform:
      1. Lee project_id de sub_state_data.SELECT.fields.project_id
      2. Busca sprint activo del proyecto
      3. Resuelve target_sub_state: ACTIVE o PLANNING
      4. Cambia chain_dir al sprint real
      5. Migra SprintState del session_dir al chain_dir real
      6. Actualiza SessionRecord
  → SPRINT.ACTIVE o SPRINT.PLANNING

CREAR NUEVO PROYECTO (via gate create_project)
──────────────────────────────────────────────
SessionRecord {
  session_id: "sess-xxx",
  project_id: "",                     ← aún vacío
  chain_dir: "~/.walker/sessions/sess-xxx/",  ← sigue virtual
  status: "bootstrap"
}
  → Gate create_project requiere que PROJECT_INIT.DETECT
    pueda operar sin chain_dir (detecta desde filesystem)
  → PROJECT_INIT ejecuta su flujo completo
  → Al completar (gate project_ready):
      chain_dir se actualiza al nuevo sprint
      SprintState se migra
```

### 8.2 WorkflowAPI.ensure_session() modificado

```python
def ensure_session(self) -> str:
    # 1. Si hay session_id explícito, usarlo
    if self._session_id:
        record = self._session_store.load(self._session_id)
        if record:
            ...
            return self._session_id

    # 2. Buscar sesión activa
    active = self._session_store.get_active()
    if active:
        self._session_id = active.session_id
        self._chain_dir = active.chain_dir
        self._project_id = active.project_id
        return self._session_id

    # 3. NO crear sesión automática con primer proyecto
    #    En vez de eso, crear sesión bootstrap sin proyecto
    #    para que el workflow entre a START.LIST (initial_state)
    record = SessionRecord.create()  # project_id vacío, chain_dir vacío
    self._session_id = record.session_id
    self._session_store.save(record)
    self._session_store.set_active(self._session_id)
    self.status_message = "Walk3r iniciado. Entrando a nodo START."
    return self._session_id
```

---

## 9. Impacto en el workflow existente

### 9.1 Archivos a crear

| Archivo | Descripción |
|---------|-------------|
| `workflow.v3/nodes/start.node.json` | Definición del nodo START con 2 sub-estados (LIST, SELECT) |
| `workflow.v3/prompts/start/list.prompt.json` | Prompt para LIST (auto) |
| `workflow.v3/prompts/start/select.prompt.json` | Prompt para SELECT (llm_task) |
| `_walker/workflow_engine/infrastructure/context_providers/projects_provider.py` | Context provider "projects" |

### 9.2 Archivos a modificar

| Archivo | Cambio |
|---------|--------|
| `workflow.v3/workflow.json` | Cambiar `initial_state` de `"SPRINT"` a `"START"` |
| `workflow.v3/workflow.json` | Agregar `import.nodes: "nodes/start.node.json"` |
| `workflow.v3/edges.json` | Agregar 5 gates: `project_selected`, `create_project`, `refresh`, `back_to_start` (×2) |
| `_walker/walker_v3.py` | Modificar `cmd_status` para entrar a nodo START si no hay proyecto activo |
| `_walker/workflow_engine/domain/models.py` | Agregar `NodeType.START = "start"` |
| `_walker/workflow_engine/application/workflow_api.py` | Modificar `ensure_session` paso 4: crear sesión bootstrap sin proyecto |
| `_walker/workflow_engine/infrastructure/context_providers/` | Registrar `projects_provider` en el registry |

### 9.3 Sin impacto (backwards compatible)

| Componente | Razón |
|------------|-------|
| `SPRINT` node | No se modifica. Solo cambia cómo se llega a él (antes era initial_state, ahora se llega via gate) |
| `PROJECT_INIT` node | No se modifica. Ahora es alcanzable desde START además de desde SPRINT. Sigue siendo un nodo hermano independiente |
| `ANALYZE/IMPLEMENT/VERIFY/REFINE` | No se modifican |
| `cmd_list` | No se modifica |
| `cmd_submit` / `cmd_transition` | No se modifican |
| Sesiones existentes | Siguen funcionando. El flujo START solo se activa cuando NO hay sesión activa |
| `walker3 <proyecto>/status` | Sigue funcionando igual — bypass de START y va directo al proyecto |

---

## 10. Plan de implementación

### Fase 1: Context Provider y modelo (día 1)

- [ ] Agregar `NodeType.START = "start"` a `domain/models.py`
- [ ] Crear `projects_provider.py` con `ProjectsContextProvider`
- [ ] Registrar provider en `context_providers/__init__.py`
- [ ] Implementar action `list_projects` en TaskRegistry

### Fase 2: Nodo START (día 1-2)

- [ ] Crear `workflow.v3/nodes/start.node.json` (2 sub-estados: LIST, SELECT)
- [ ] Crear prompts: `start/list.prompt.json`, `start/select.prompt.json`
- [ ] Agregar edges a `edges.json` (project_selected, create_project, refresh, back_to_start ×2)
- [ ] Actualizar `workflow.json` (initial_state = "START" + import node)

### Fase 3: Integración del engine (día 2)

- [ ] Modificar `WorkflowAPI.ensure_session()` — paso 4: crear sesión bootstrap sin proyecto
- [ ] Verificar que el engine cargue `initial_state = "START"` y ejecute LIST → SELECT correctamente
- [ ] Verificar que gate `project_selected` resuelva el sprint activo y transicione a SPRINT.ACTIVE o PLANNING

### Fase 4: Integración CLI (día 2-3)

- [ ] Modificar `cmd_status()` — si no hay proyecto, llamar a `api.step()` que ejecuta START
- [ ] Verificar que `walker3 status` sin args ejecuta el nodo START y devuelve prompt
- [ ] Verificar que `walker3 <proyecto>/status` sigue funcionando (bypass de START)

### Fase 5: Tests y refinamiento (día 3)

- [ ] Test: `walker3 status` sin sesión → muestra prompt "START.SELECT"
- [ ] Test: `submit --decision select --project_id X` → gate project_selected → SPRINT
- [ ] Test: `submit --decision create` → gate create_project → PROJECT_INIT.DETECT
- [ ] Test: PROJECT_INIT completa → gate project_ready → SPRINT.ACTIVE
- [ ] Test: `transition back_to_start` desde SPRINT → START.LIST
- [ ] Test: `transition back_to_start` desde PROJECT_INIT → START.LIST

---

## 11. Diagrama de flujo completo — Grafo de nodos hermanos

```
                    initial_state = "START"
                           │
                           ▼
              ┌───────────────────────┐
              │    NODO START         │  ← Nodo delgado, solo posiciona al agente
              │                       │
              │  LIST (auto)          │
              │  action: list_projects│
              │  → escanea proyectos  │
              │  → avanza a SELECT    │
              └──────────┬────────────┘
                         │
                         ▼
              ┌───────────────────────┐
              │  SELECT (llm_task)    │
              │  → inyecta contexto:  │
              │    projects + trace   │
              │  → prompt: "¿Qué      │
              │    proyecto?"         │
              │                       │
              │  ┌─────┬──────┬────┐  │
              │  │sel. │crea  │ref.│  │
              │  └──┬──┴──┬───┴───┘  │
              └─────┼─────┼──────────┘
                    │     │
          project_selected  create_project
                    │     │
                    ▼     ▼
    ┌──────────────────┐  ┌──────────────────────────┐
    │  NODO SPRINT     │  │  NODO PROJECT_INIT        │ ← HERMANOS
    │  (existente)     │  │  (existente, no modificado)│
    │                  │  │                            │
    │  PLANNING (si no │  │  DETECT → LEGACY_MAP       │
    │  hay sprint)     │  │  → SCENARIO → GATHER_SPECS │
    │  ACTIVE (si hay  │  │  → BUILD_PROJECT → SCAN    │
    │  sprint activo)  │  │  → BUILD_CKG → REVIEW      │
    │                  │  │  → FIRST_SPRINT             │
    │  → ANALYZE/IMPL  │  │                            │
    │  → VERIFY/REFINE │  │  gate: project_ready       │
    │  → CLOSE         │  └──────────┬─────────────────┘
    └──────┬───────────┘             │
           │                        │
           │  gate: back_to_start   │
           └────────┬───────────────┘
                    │
                    ▼
          ┌──────────────────┐
          │  START.LIST      │  ← Volver a inicio
          │  (refresh)       │
          └──────────────────┘
```

### Relaciones clave

| Relación | Tipo |
|----------|------|
| `START` → `SPRINT` | Vía gate `project_selected` (proyecto existente) |
| `START` → `PROJECT_INIT` | Vía gate `create_project` (nuevo proyecto) |
| `SPRINT` → `START` | Vía gate `back_to_start` (cambiar de proyecto) |
| `PROJECT_INIT` → `START` | Vía gate `back_to_start` (cancelar creación) |
| `PROJECT_INIT` → `SPRINT` | Vía gate `project_ready` (creación completada) |

Todos los nodos son **hermanos** — están al mismo nivel en el grafo. Ninguno es hijo de otro.

---

## 12. Cambios en el engine para mitigar los gaps

### 12.1 `WorkflowGraph.initial_state` — nuevo campo

**Archivo:** `domain/models.py`

Se agrega `initial_state` al `WorkflowGraph` para que el engine pueda leer desde `workflow.json` cuál es el nodo inicial y auto-inicializar `SprintState` cuando está vacío.

```python
@dataclass
class WorkflowGraph:
    nodes: dict[str, WorkflowNode] = field(default_factory=dict)
    initial_state: str = ""  # NUEVO: leído de workflow.json
    on_events: dict[str, dict] = field(default_factory=dict)
```

**Archivo:** `infrastructure/repositories.py` — en `_build_graph()`:

```python
graph = WorkflowGraph(
    nodes=nodes_dict,
    initial_state=raw.get("initial_state", ""),  # NUEVO
    on_events=dict(raw.get("on", {})),
)
return graph
```

### 12.2 `SessionRecord.session_dir` — property para directorio virtual

**Archivo:** `domain/session.py`

```python
@dataclass
class SessionRecord:
    session_id: str
    project_id: str = ""
    chain_dir: str = ""

    @property
    def session_dir(self) -> str:
        """Directorio de sesión usado como chain_dir virtual durante START."""
        base = os.path.expanduser("~/.walker/sessions")
        return os.path.join(base, self.session_id)

    def ensure_session_dir(self) -> str:
        os.makedirs(self.session_dir, exist_ok=True)
        return self.session_dir
```

### 12.3 `WorkflowAPI.ensure_session()` — session_dir como chain_dir virtual

**Archivo:** `application/workflow_api.py`

Cuando no hay proyecto activo, se usa `session_dir` como `chain_dir` virtual.
El SprintState se persiste ahí temporalmente.

```python
def ensure_session(self) -> str:
    # ... pasos 1 (session_id explícito) y 2 (sesión activa) sin cambios ...

    # 3. No hay sesión activa ni proyecto
    #    → Crear sesión bootstrap con session_dir como chain_dir virtual
    record = SessionRecord.create()
    record.ensure_session_dir()
    self._session_id = record.session_id
    self._chain_dir = record.session_dir  # ← virtual
    self._project_id = ""

    # Inicializar SprintState desde workflow.initial_state
    from ..infrastructure.repositories import WorkflowRepository, SprintStateRepository
    wf_repo = WorkflowRepository()
    st_repo = SprintStateRepository()

    workflow = wf_repo.load(self._chain_dir)
    state = st_repo.load(self._chain_dir)

    if workflow.initial_state and not state.current_node:
        state.current_node = workflow.initial_state
        # Resolver primer sub-estado del nodo initial_state
        node = workflow.get_node(workflow.initial_state)
        state.current_sub_state = node.first_sub_state.key if node and node.first_sub_state else "INIT"
        state.sub_state_index = 0
        state.add_history(f"{state.current_node}.{state.current_sub_state}")
        state.context["session_id"] = self._session_id
        st_repo.save(self._chain_dir, state)

    self._session_store.save(record)
    self._session_store.set_active(self._session_id)
    self.status_message = f"Sesión bootstrap. Nodo inicial: {workflow.initial_state}"
    return self._session_id
```

### 12.4 `WorkflowRepository._resolve_path()` — fallback a workflow canónico

**Archivo:** `infrastructure/repositories.py`

Cuando el chain_dir es un session_dir (contiene `.walker/sessions`), no hay
`workflow.v3/` ahí. El resolver debe hacer fallback al workflow canónico
de delta-skill que es el SSOT de la definición de nodos.

```python
def _resolve_path(self, chain_dir: str) -> str | None:
    if not chain_dir:
        return None

    parent = os.path.dirname(os.path.dirname(chain_dir))

    # 1. workflow.v3/ modular
    modular = os.path.join(parent, "workflow.v3", "workflow.json")
    if os.path.exists(modular):
        return modular

    # 2. Legacy inline
    candidate = os.path.join(parent, "workflow.json")
    if os.path.exists(candidate):
        return candidate

    # 3. NUEVO: session_dir → workflow canónico de delta-skill
    if ".walker/sessions" in chain_dir:
        canonical = os.path.normpath(
            os.path.join(
                os.path.dirname(__file__),
                "../../../../../../.cognitive/projects/delta-skill/workflow.v3/workflow.json"
            )
        )
        if os.path.exists(canonical):
            return canonical

    # 4-5. Fallbacks legacy...
    # ... (código existente) ...
```

### 12.5 Gate `project_selected` con transform para routing dinámico

**Archivo:** `workflow.v3/edges.json`

La edge usa un `transform` que ejecuta `resolve_project_sprint` para decidir
si el destino es SPRINT.ACTIVE o SPRINT.PLANNING según el proyecto.

```json
{
  "from": "START",
  "to": "SPRINT",
  "gate": "project_selected",
  "sub_state_gate": "ACTIVE",
  "description": "Project selected. Transform resolves ACTIVE or PLANNING.",
  "transform": {
    "action": "resolve_project_sprint",
    "input": ["SELECT.fields"],
    "output": "SPRINT.context"
  }
}
```

### 12.6 `resolve_project_sprint` — tarea de routing dinámico (NUEVA)

**Archivo:** `tasks/resolve_project_sprint.py`

Esta tarea se ejecuta como transform de la gate `project_selected`.
Inspecciona el proyecto seleccionado y resuelve:
- Si tiene sprint activo → target = `SPRINT.ACTIVE`, chain_dir = el del sprint
- Si no tiene sprint → target = `SPRINT.PLANNING`, chain_dir = a definir

```python
def register(registry):
    registry.register("resolve_project_sprint", handler)

def handler(ctx, data):
    # Leer project_id del sub_state_data de SELECT
    fields = data.get("SELECT.fields", {})
    project_id = fields.get("project_id", "")

    if not project_id:
        return TaskResult.failure("project_id no especificado en SELECT")

    # Buscar sprint activo del proyecto
    chain_dir = _find_sprint(project_id, "")
    if chain_dir:
        # Tiene sprint activo → ir a SPRINT.ACTIVE
        return TaskResult.success({
            "target_node": "SPRINT",
            "target_sub_state": "ACTIVE",
            "chain_dir": chain_dir,
            "project_id": project_id,
        })
    else:
        # No tiene sprint → ir a SPRINT.PLANNING
        return TaskResult.success({
            "target_node": "SPRINT",
            "target_sub_state": "PLANNING",
            "chain_dir": "",
            "project_id": project_id,
        })
```

### 12.7 `TransitionUseCase.execute()` — usar target del transform

**Archivo:** `application/use_cases.py`

El `TransitionUseCase` debe ejecutar el transform ANTES de aplicar la transición
y usar el `target_node` / `target_sub_state` resueltos dinámicamente.

```python
def execute(self, chain_dir, gate_name, ...):
    # ... load workflow + state ...

    # 1. Buscar gate
    gate = self._find_gate(workflow, state.current_node, gate_name)

    # 2. Resolver target (con transform dinámico)
    target_node = gate.target_node
    target_sub_state = gate.target_sub_state

    if gate.transform:
        # Ejecutar transform antes de la transición
        exec_ctx = ctx or ExecutionContext.create(...)
        task_result = self._task_registry.execute(
            gate.transform["action"], exec_ctx,
            {"SELECT.fields": state.sub_state_data.get("SELECT", {}).fields}
        )
        if task_result.ok and task_result.data:
            target_node = task_result.data.get("target_node", target_node)
            target_sub_state = task_result.data.get("target_sub_state", target_sub_state)
            # Si el transform devuelve un nuevo chain_dir, actualizarlo
            new_chain = task_result.data.get("chain_dir", "")
            if new_chain:
                chain_dir = new_chain

    # 3. Aplicar transición con target resuelto
    TransitionService.apply(state, workflow, target_node, target_sub_state)

    # 4. Si chain_dir cambió, migrar SprintState y actualizar sesión
    if new_chain and new_chain != chain_dir:
        st_repo.save(new_chain, state)  # persistir en nuevo chain_dir
        if hasattr(ctx, 'session_id'):
            record = SessionStore.load(ctx.session_id)
            record.chain_dir = new_chain
            record.project_id = target_sub_state or ""
            SessionStore.save(record)

    # ... persistir y retornar ...
```

### 12.8 `list_projects` — nueva tarea en TaskRegistry

**Archivo:** `tasks/list_projects.py` (NUEVO)

```python
def register(registry):
    registry.register("list_projects", handler)

def handler(ctx, data):
    from _walker.walker_v3 import _list_projects
    projects = _list_projects()
    return TaskResult.success({"projects": projects})
```

### 12.9 `projects` — nuevo context provider

**Archivo:** `context/providers/projects_provider.py` (NUEVO)

```python
class ProjectsContextProvider(ContextProvider):
    provider_id = "projects"

    def get_context(self, ctx, config) -> str:
        from _walker.walker_v3 import _list_projects
        projects = _list_projects()
        if not projects:
            return "  (no hay proyectos todavía)"
        lines = []
        for p in projects:
            active = [s for s in p["sprints"] if s["status"] == "active"]
            if active:
                lines.append(f"  🟢 {p['project_id']} — {active[0]['id'][:50]}")
            else:
                lines.append(f"  ⚪ {p['project_id']} — {len(p['sprints'])} sprints")
        return "\n".join(lines)
```

### 12.10 PROJECT_INIT autónomo sin chain_dir

**Archivo:** `tasks/detect_project_context.py` (modificación)

Cuando `chain_dir` está vacío (viene desde START), la tarea debe operar en
modo autónomo: detectar el contexto desde el filesystem o asumir greenfield.

```python
def handler(ctx, data):
    chain_dir = ctx.chain_dir or ""
    project_path = data.get("project_path", "")

    if not chain_dir and not project_path:
        # Sin chain_dir ni path → modo greenfield automático
        return TaskResult.success({
            "has_code": False,
            "has_git": False,
            "scenario": "greenfield",
            "message": "No chain_dir disponible. Modo greenfield."
        })

    # ... detección normal desde chain_dir o project_path ...
```

---

## Anexo D: Análisis de gaps técnicos (resueltos en §12)

### C.1 `initial_state` no es usado por el engine

| Aspecto | Detalle |
|---------|---------|
| **Problema** | `workflow.json` tiene campo `initial_state: "START"` pero el engine (`OrchestrationService`, `WorkflowRepository`, `SprintStateRepository`) nunca lo lee para inicializar `SprintState`. |
| **Código** | `WorkflowRepository._build_graph()` (repositories.py:124-216) no extrae `initial_state`. `WorkflowGraph` (models.py:241) no tiene campo `initial_state`. |
| **Impacto** | Cuando `SprintStateRepository.load("")` devuelve `SprintState()` vacío (current_node=""), `OrchestrationService.step()` recibe `node=None` y retorna error. El engine no puede auto-inicializarse. |
| **Solución** | Agregar `initial_state` a `WorkflowGraph` + lógica en `WorkflowAPI.step()` o `ensure_session()` que, si SprintState está vacío, lo inicialice desde `workflow.initial_state`. |

### C.2 `chain_dir` vacío rompe load/save de repositorios

| Aspecto | Detalle |
|---------|---------|
| **Problema** | Durante START no hay proyecto → no hay `chain_dir`. `WorkflowRepository._resolve_path("")` retorna `None` (repositories.py:88-89). `SprintStateRepository.save("", state)` → `os.path.join("", "sprint-state.json")` = `"/sprint-state.json"` → error de escritura. |
| **Código** | `repositories.py:80-89`: `if not chain_dir: return None` ; `repositories.py:240-246`: `save()` usa `os.path.join(chain_dir, ...)` sin validar chain_dir vacío. |
| **Impacto** | `OrchestrationService.step()` llama a `workflow_repo.load(chain_dir)` que retorna `WorkflowGraph()` vacío. Luego `workflow.get_node("")` retorna `None` → error. El flujo START no puede ejecutarse. |
| **Solución** | Una de: (a) usar session_dir como chain_dir virtual durante bootstrap, (b) agregar `bootstrap` mode al engine que use un workflow canónico sin chain_dir, (c) pre-inicializar SprintState antes de llamar al engine. |

### C.3 SprintState no tiene dónde persistir durante START

| Aspecto | Detalle |
|---------|---------|
| **Problema** | `SprintStateRepository.save()` escribe en `{chain_dir}/sprint-state.json`. Sin chain_dir, no hay directorio. |
| **Contexto** | El estado de START debe persistirse para mantener la sesión entre llamadas del agente (submit/transition). Sin persistencia, cada llamada empezaría desde cero. |
| **Solución** | Usar `~/.walker/sessions/{session_id}/state.json` como ubicación temporal mientras no hay proyecto. El `SessionRecord` ya tiene `session_id` que puede usarse como clave. |

### C.4 `list_projects` action no existe en TaskRegistry

| Aspecto | Detalle |
|---------|---------|
| **Problema** | `START.LIST` define `action: "list_projects"` pero no hay tarea registrada con ese nombre en el TaskRegistry. |
| **Código** | `bootstrap.py` descubre tasks/ automáticamente pero no hay `tasks/list_projects.py`. |
| **Solución** | Crear `tasks/list_projects.py` con función `register(registry)` que registra `list_projects`. La acción escanea `.cognitive/projects/` y retorna lista JSON. |

### C.5 `projects` context provider no existe

| Aspecto | Detalle |
|---------|---------|
| **Problema** | `START.SELECT` define `sources: [{provider: "projects"}]` pero `ContextProviderRegistry` no tiene un provider con ese nombre. |
| **Código** | `context/__init__.py` tiene `create_default_registry()` con providers existentes (project, state, ckg, delta, trace, cpg) pero no "projects". |
| **Solución** | Crear `context/providers/projects_provider.py` con `ProjectsContextProvider` y registrarlo en `create_default_registry()`. |

### C.6 Gate `project_selected` no soporta lógica condicional ACTIVE/PLANNING

| Aspecto | Detalle |
|---------|---------|
| **Problema** | La edge define `target_sub_state: "PLANNING"` fijo. Pero la decisión de ir a ACTIVE vs PLANNING depende de si el proyecto destino tiene sprint activo. |
| **Código** | `TransitionService.apply()` (services.py:182-213) usa `target_sub_state` literal. No hay mecanismo de routing condicional en gates. |
| **Impacto** | Si el proyecto tiene sprint activo (ej. delta-skill con taskregistry-migration), el agente iría a PLANNING en vez de ACTIVE, rompiendo el flujo. |
| **Solución** | Una de: (a) agregar `target_resolver` opcional en Gate que sea una función que decida dinámicamente, (b) usar `transform` en la gate que modifique `target_sub_state` antes de aplicar, (c) que el engine resuelva en `TransitionUseCase.execute()` inspeccionando el proyecto destino. |

### C.7 PROJECT_INIT no puede funcionar sin `chain_dir`

| Aspecto | Detalle |
|---------|---------|
| **Problema** | `PROJECT_INIT.DETECT` ejecuta `detect_project_context` que espera un `chain_dir` o `project_path`. Desde START, no hay ninguno. |
| **Código** | No se pudo inspeccionar `detect_project_context` porque es una tarea registrada en el TaskRegistry. |
| **Impacto** | Al transicionar `START.SELECT → create_project → PROJECT_INIT.DETECT`, el engine pasa `chain_dir` vacío y PROJECT_INIT falla al intentar leer archivos. |
| **Solución** | Una de: (a) la gate `create_project` usa un `transform` que pasa el `project_name` (dato liviano de SELECT) a PROJECT_INIT, (b) la transición primero ejecuta una acción que establece un chain_dir temporal, (c) PROJECT_INIT.DETECT es modificado para ser autónomo (detectar desde CWD o desde un parámetro). |

### C.8 `NodeType.START` no existe (bajo impacto)

| Aspecto | Detalle |
|---------|---------|
| **Problema** | `domain/models.py:39-46` define `NodeType` enum sin valor "start". |
| **Impacto** | Bajo — `NodeType` es solo para categorización. El routing usa strings literales (`current_node`, `gate.target_node`), no el enum. El workflow funciona sin modificar el enum. |
| **Solución** | Agregar `START = "start"` al enum por completitud y para futuras validaciones. |

### Resumen de gaps por severidad

| Severidad | Gap | ¿Bloqueante? |
|-----------|-----|:------------:|
| 🔴 CRÍTICO | C.1: `initial_state` no es usado por el engine | Sí. Sin esto, START no arranca. |
| 🔴 CRÍTICO | C.2: `chain_dir` vacío rompe load/save | Sí. El engine no puede operar sin chain_dir. |
| 🔴 CRÍTICO | C.3: SprintState sin persistencia durante START | Sí. Sin estado persistido, el agente no puede hacer submit/transition. |
| 🟡 ALTO | C.4: `list_projects` action no existe | Sí. LIST fallaría al ejecutar. |
| 🟡 ALTO | C.5: `projects` context provider no existe | Sí. SELECT no podría inyectar contexto al LLM. |
| 🟡 ALTO | C.6: Gate `project_selected` sin lógica condicional | Sí. Iría a PLANNING aunque haya sprint activo. |
| 🟡 ALTO | C.7: PROJECT_INIT no funciona sin chain_dir | Sí. La creación de proyectos nuevos fallaría. |
| 🟢 BAJO | C.8: NodeType.START no existe | No. Solo es etiqueta. |

---

## Anexo A: Referencias

- `workflow_v3_design.md` — Diseño base del motor de nodos
- `workflow_v3_enterprise.md` — Diseño enterprise con eventos y resiliencia
- `workflow.v3/workflow.json` — Definición actual del workflow (delta-skill)
- `workflow.v3/nodes/project_init.node.json` — Nodo PROJECT_INIT existente
- `_walker/walker_v3.py` — CLI handler actual
- `_walker/workflow_engine/application/workflow_api.py` — WorkflowAPI con ensure_session
- `_walker/workflow_engine/domain/models.py` — Modelos de dominio (NodeType, etc.)
- `.agents/skills/walker/SKILL.md` — Documentación de la skill walker

## Anexo B: Decisiones de diseño (ADRs)

| ID | Decisión | Fundamentos |
|----|----------|-------------|
| ADR-START-001 | **START es un nodo delgado** (solo LIST + SELECT) | No duplica lógica de PROJECT_INIT. Su único trabajo es posicionar al agente. |
| ADR-START-002 | **START, PROJECT_INIT y SPRINT son hermanos** | Todos están al mismo nivel en el grafo. Ninguno es padre/hijo. El workflow se navega via gates, no por jerarquía. |
| ADR-START-003 | **LIST es auto, no llm_task** | Escanear el sistema de archivos no requiere decisión del LLM. Es puramente informativo. |
| ADR-START-004 | **SELECT es llm_task, no user_input** | El LLM debe poder elegir basado en contexto (proyectos activos, traces). Si fuera user_input, el usuario tendría que elegir manualmente cada vez. |
| ADR-START-005 | **SELECT no pregunta escenario ni specs** | Eso es responsabilidad de PROJECT_INIT. START no duplica. |
| ADR-START-006 | **No hay sub-estado CONTINUE** | La transición la maneja el engine via gates, no un sub-estado intermedio. |
| ADR-START-007 | **ensure_session() ya no auto-asigna proyecto** | El entry point ahora es START, no SPRINT. La sesión bootstrap sin proyecto permite que el workflow decida. |
| ADR-START-008 | **`project_selected` resuelve ACTIVE vs PLANNING automáticamente** | El engine inspecciona el proyecto destino: si tiene sprint activo va a ACTIVE, si no a PLANNING. El agente no necesita decidir. |
| ADR-START-009 | **Extensibilidad por conectividad** | Si mañana se necesita un nodo AUTH o ONBOARDING, se conecta desde START.SELECT via una gate nueva. Los nodos existentes no se tocan. |
