# State Management — Workflow Context en 3 Capas

> **Versión:** 1.0-draft  
> **Base:** workflow_v3_enterprise.md, START_NODE.md  
> **Estado:** Propuesta de diseño  
> **Objetivo:** Definir un mecanismo formal para que los datos fluyan entre nodos del workflow, distinguiendo estado global (persistente entre nodos) de estado local (por nodo), y eliminando la necesidad de que el LLM recuerde cómo llamar al walker.

---

## Tabla de Contenidos

1. [Problema](#1-problema)
2. [Arquitectura: 3 Capas de Estado](#2-arquitectura-3-capas-de-estado)
3. [Mecanismo: `context_updates` en edges](#3-mecanismo-context_updates-en-edges)
4. [Pipeline de transición completo](#4-pipeline-de-transición-completo)
5. [Ejemplo: flujo START → SPRINT](#5-ejemplo-flujo-start--sprint)
6. [Ejemplo: flujo SPRINT → ANALYZE](#6-ejemplo-flujo-sprint--analyze)
7. [Reglas de diseño](#7-reglas-de-diseño)
8. [Impacto en el código existente](#8-impacto-en-el-código-existente)
9. [Preguntas abiertas](#9-preguntas-abiertas)

---

## 1. Problema

### 1.1 Síntoma

Hoy, cuando el agente selecciona un proyecto en `START.SELECT`:

```
submit --decision select --project_id delta-skill
```

Ese `project_id` queda enterrado en:

```
SprintState.sub_state_data["SELECT.fields"]["project_id"] = "delta-skill"
```

Pero cuando el workflow transiciona a `SPRINT.ACTIVE`, ese dato **desaparece** del alcance del LLM. El siguiente prompt no sabe qué proyecto está activo porque:

- `sub_state_data["SELECT"]` ya no está en el nodo actual
- `state.context` no tiene `project_id` porque nunca se promovió
- El LLM tendría que "acordarse" de lo que hizo en el paso anterior

### 1.2 Causa raíz

No hay un mecanismo explícito para que los datos viajen de un nodo a otro. El `SprintState.context` existe pero nunca se escribe automáticamente desde `sub_state_data`. Dependemos de que el LLM recuerde todo o de transforms ad-hoc que no siempre persisten en contexto.

### 1.3 Principio rector

> **El LLM no debe recordar cómo llamar al walker. El grafo de estado ya lo sabe. El engine debe inyectar automáticamente el contexto global en cada paso.**

---

## 2. Arquitectura: Contexto como React Context (Provider + Consumer)

### 2.1 La metáfora

> **Un nodo declara `provides` = React `<Provider value={...}>`**
> **Un sub-estado recibe contexto = React `useContext()`**
> **Engine context = React global (siempre disponible)**
> **Transiciones propagan contexto = Provider anidados**

Así como en React un `Provider` envuelve componentes hijos y estos acceden al contexto sin props explícitas, en el workflow un nodo declara qué contexto produce y todos sus sub-estados lo reciben automáticamente. Al transicionar al siguiente nodo, el contexto promovido viaja con el workflow.

### 2.2 Los 3 orígenes de contexto

```
┌──────────────────────────────────────────────────────────────────┐
│  ORIGEN 1: ENGINE CONTEXT (hardcodeado, no se declara)           │
│                                                                  │
│  Son datos que el engine conoce intrínsecamente. No los declara  │
│  ningún nodo. Están disponibles para TODO el workflow.           │
│                                                                  │
│  ├─ session_id     → SessionRecord.session_id                    │
│  ├─ chain_dir      → SessionRecord.chain_dir                     │
│  ├─ current_node   → SprintState.current_node                    │
│  ├─ current_sub    → SprintState.current_sub_state               │
│  ├─ history        → SprintState.history                         │
│  └─ config_*       → cfg("repo_root"), cfg("projects_dir")...   │
│                                                                  │
│  ¿Quién lo provee?  El engine runtime                             │
│  ¿Quién lo consume?  Engine + ContextComposer + LLM               │
│  ¿Cambia?           session_id y chain_dir pueden cambiar         │
│                     (multitenant: cada sesión tiene su session_id)│
│                     Pero NO se declaran — el engine los gestiona  │
└──────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│  ORIGEN 2: PROVIDES (declarado por nodos, estilo React Context)  │
│                                                                  │
│  Cada nodo puede declarar qué contexto PRODUCE.                  │
│  Todos los sub-estados de ese nodo lo RECIBEN automáticamente.   │
│  Al transicionar al siguiente nodo, el contexto promovido viaja. │
│                                                                  │
│  Ejemplo en node.json:                                           │
│  {                                                               │
│    "label": "Start Bootstrap",                                    │
│    "provides": ["session_id"],   ← START produce session_id      │
│    "sub_states": [                                                │
│      {                                                            │
│        "key": "SELECT",                                           │
│        "needs": ["project_id"],  ← SELECT necesita project_id    │
│        "provides": ["project_id"] ← SELECT produce project_id    │
│      }                                                            │
│    ]                                                              │
│  }                                                                │
│                                                                  │
│  Herencia automática:                                             │
│  START.provides = ["session_id"]                                  │
│    └── SELECT (sub-estado de START)                              │
│        ├── hereda: session_id (de START)                         │
│        └── provides: project_id, chain_dir                       │
│                                                                  │
│  Al transicionar a SPRINT:                                       │
│    SPRINT recibe: session_id (de START) + project_id (de SELECT) │
│    SPRINT.provides = ["sprint_id"]                                │
│      └── ACTIVE (sub-estado de SPRINT)                            │
│          ├── hereda: session_id, project_id (del grafo)          │
│          └── hereda: sprint_id (de SPRINT)                       │
└──────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│  ORIGEN 3: PROVIDERS (context providers registrados)              │
│                                                                  │
│  Datos derivados que no están en el estado del workflow           │
│  pero se pueden calcular desde fuentes externas:                  │
│                                                                  │
│  ├─ project provider   → project.json (convenciones, patrones)   │
│  ├─ ckg provider       → decisiones de diseño previas            │
│  ├─ cpg provider       → estructura de código actual             │
│  ├─ trace provider     → historial de sesión                     │
│  ├─ delta provider     → tareas de sprints anteriores            │
│  └─ sprint provider    → backlog completo del sprint             │
│                                                                  │
│  Se resuelven vía ContextProviderRegistry + QueryManager          │
│  disponibles para prompts del LLM cuando el nodo los declara     │
│  en context.sources                                              │
└──────────────────────────────────────────────────────────────────┘
```

### 2.3 Herencia: cómo viaja el contexto entre nodos

```
NODO: START
  provides: ["session_id"]
  │
  ├── SUB: LIST
  │   └── contexto disponible: session_id (hereda de START)
  │
  ├── SUB: SELECT
  │   ├── contexto disponible: session_id (hereda de START)
  │   ├── needs: ["session_id"]   ← del engine
  │   └── provides: ["project_id"] ← captura project_id
  │
  └── → Transición a SPRINT
      └── contexto que viaja: session_id + project_id
               │
               ▼
NODO: SPRINT
  provides: ["sprint_id"]    ← produce sprint_id
  │
  ├── SUB: PLANNING
  │   └── contexto disponible: session_id, project_id (heredados)
  │       └── sprint_id (heredado de SPRINT)
  │
  ├── SUB: ACTIVE
  │   ├── contexto disponible: session_id, project_id, sprint_id
  │   ├── needs: ["project_id", "sprint_id"]
  │   └── provides: ["task_id"]  ← captura task_id cuando se inicia una tarea
  │
  └── → Transición a ANALYZE
      └── contexto que viaja: session_id + project_id + sprint_id + task_id
               │
               ▼
NODO: ANALYZE
  needs: ["task_id", "project_id"]
  └── contexto disponible: session_id, project_id, sprint_id, task_id
      ¡todo resuelto automáticamente!
```

### 2.4 Reglas de herencia

| Regla | Descripción |
|-------|-------------|
| **H1** | Engine context siempre disponible (no se declara) |
| **H2** | Un nodo declara `provides` → todos sus sub-estados heredan |
| **H3** | Al transicionar, el `provides` del nodo anterior se promueve automáticamente al global state |
| **H4** | Un sub-estado declara `needs` → el ContextResolver lo busca en: global → herencia → infraestructura |
| **H5** | Si `needs` no se cumple, el engine puede fallar en validación (error temprano) o resolver con default |
| **H6** | `provides` anidado: un sub-estado puede `provide` datos que su nodo padre no tenía |

### 2.5 Comparación con React Context

| React | Workflow |
|-------|----------|
| `<Provider value={session}>` | Nodo con `provides: ["session_id"]` |
| `useContext(SessionContext)` | Sub-estado con `needs: ["session_id"]` |
| Providers anidados | Transiciones entre nodos que acumulan contexto |
| Context value cambia | `project_id` cambia según el proyecto seleccionado |
| Default context value | Engine context (siempre disponible) |
| Error si no hay Provider | Error de validación si `needs` no se cumple |

```
START.SELECT                   SPRINT.ACTIVE
     │                              ▲
     │  submit({project_id})         │  step() inyecta:
     │      │                        │    context.project_id ✓
     │      ▼                        │    context.chain_dir ✓
     │  sub_state_data["SELECT"] ────┤
     │      │                        │
     │      │ context_updates        │
     │      ▼                        │
     │  context.project_id ──────────┘
     │  context.chain_dir ───────────┘
     ▼
Gate: project_selected
```

---

## 3. Mecanismo: `context_updates` en edges

### 3.1 Definición

Cada edge en `edges.json` puede declarar `context_updates`: una lista de mapeos que copian datos de `sub_state_data` a `context`.

```json
{
  "from": "START",
  "to": "SPRINT",
  "gate": "project_selected",
  "context_updates": [
    {"from": "SELECT.fields.project_id", "to": "context.project_id"},
    {"from": "SELECT.fields.project_id", "to": "context.chain_dir", "via": "resolve_chain_dir"}
  ],
  "transform": {
    "action": "resolve_project_sprint",
    "input": ["SELECT.fields"],
    "output": "context.resolve_result"
  }
}
```

### 3.2 Formato de cada update

| Campo | Tipo | Obligatorio | Descripción |
|-------|------|:-----------:|-------------|
| `from` | `string` | ✅ | Selector en formato `NODO.SUB.campo` (sub_state_data) |
| `to` | `string` | ✅ | Destino en formato `context.campo` |
| `via` | `string` | ❌ | Acción opcional del TaskRegistry para transformar el valor antes de copiarlo |

### 3.3 Semántica

```
context_updates se ejecutan DESPUÉS de la transición de nodo
pero ANTES de que el nuevo nodo ejecute step().

Pipeline:
  1. TransitionUseCase.apply() cambia current_node
  2. Se leen context_updates de la edge
  3. Por cada update:
     a. Se lee el valor de sub_state_data["NODO.SUB"]["campo"]
     b. Si tiene via: se ejecuta la acción del TaskRegistry para transformarlo
     c. Se escribe en state.context["campo"]
  4. Se persiste sprint-state.json
  5. El nuevo nodo ejecuta step() con context actualizado
```

### 3.4 Ejemplos de uso

```json
// Promover project_id directamente
{"from": "SELECT.fields.project_id", "to": "context.project_id"}

// Promover chain_dir via transform (resuelve la ruta del sprint)
{"from": "SELECT.fields.project_id", "to": "context.chain_dir",
 "via": "resolve_chain_dir"}

// Promover un array de archivos
{"from": "ANALYZE.GATHER.files", "to": "context.impacted_files"}

// Promover un valor con nombre diferente
{"from": "IMPLEMENT.REPORT_GATE.decision", "to": "context.last_decision"}
```

---

## 4. Pipeline de transición completo

```
LLM: submit({decision: "select", project_id: "delta-skill"})
  │
  ▼
┌──────────────────────────────────────────────────────────────────┐
│  OrchestrationService.submit()                                   │
│  1. SubmitDataUseCase.execute()                                  │
│     a. Valida required_fields                                    │
│     b. Persiste en sub_state_data["SELECT.fields"]               │
│  2. Como execution es llm_task → NO auto-advance                 │
│  3. Retorna al CLI                                               │
└──────────────────────────────────────────────────────────────────┘
  │
  ▼
LLM recibe respuesta: "Datos guardados. Usa transition project_selected"
  │
  ▼
LLM: transition project_selected  ← o el engine lo hace automáticamente
  │
  ▼
┌──────────────────────────────────────────────────────────────────┐
│  OrchestrationService.transition("project_selected")             │
│                                                                  │
│  1. TransitionUseCase.execute()                                  │
│     a. Encuentra gate "project_selected"                         │
│     b. Valida precondiciones                                     │
│     c. TransitionService.apply(to: "SPRINT", sub_state: "ACTIVE")│
│     d. APLICA context_updates:                                   │
│        - Lee SELECT.fields.project_id                            │
│        - Escribe context.project_id = "delta-skill"              │
│        - Ejecuta via "resolve_chain_dir" si existe               │
│        - Escribe context.chain_dir = ".../sprint-xxx/"           │
│     e. Ejecuta transform "resolve_project_sprint"                │
│        - Determina ACTIVE vs PLANNING                            │
│     f. Persiste sprint-state.json (context actualizado)          │
│                                                                  │
│  2. Traza la transición                                          │
│  3. Retorna resultado                                            │
└──────────────────────────────────────────────────────────────────┘
  │
  ▼
┌──────────────────────────────────────────────────────────────────┐
│  OrchestrationService.step()                                     │
│  1. Carga workflow + state                                       │
│  2. Lee context.project_id = "delta-skill"  ← ¡ya disponible!    │
│  3. Ejecuta SPRINT.ACTIVE (auto dashboard)                       │
│  4. Inyecta contexto:                                            │
│     ProjectProvider lee context.project_id                       │
│     → Muestra convenciones de delta-skill                        │
│  5. Retorna prompt con dashboard del sprint                      │
└──────────────────────────────────────────────────────────────────┘
```

---

## 5. Ejemplo: flujo START → SPRINT

### 5.1 Declaración de la edge

```json
{
  "from": "START",
  "to": "SPRINT",
  "gate": "project_selected",
  "sub_state_gate": "ACTIVE",
  "description": "Project selected. Promueve project_id al contexto global.",
  "context_updates": [
    {"from": "SELECT.fields.project_id", "to": "context.project_id"}
  ],
  "transform": {
    "action": "resolve_project_sprint",
    "input": ["SELECT.fields"],
    "output": "context.sprint_resolution"
  }
}
```

### 5.2 Qué ve el LLM en cada paso

**Paso 1: START.SELECT**
```
Global context: {}  ← vacío, primera vez

START.SELECT — Entry Point

Available projects:
  🟢 delta-skill — active sprint: taskregistry-migration-20260724
  ⚪ backend-microservices — 5 sprints

What would you like to do?
```

**Paso 2: Agente responde**
```
submit --decision select --project_id delta-skill
```

**Paso 3: Transición (engine, transparente para el LLM)**
```
context_updates: project_id → context.project_id
transform: resolve_project_sprint → SPRINT.ACTIVE
```

**Paso 4: SPRINT.ACTIVE**
```
Global context: {
  project_id: "delta-skill",          ← ¡automático!
  chain_dir: ".../taskregistry-.../",  ← ¡automático!
}

SPRINT.ACTIVE — Sprint Dashboard

Goal: Completar walker_v3 enterprise
Tasks: 12 tasks • 1 in_progress • 11 pending
...
```

El LLM **no necesitó recordar** `project_id`. Llegó solo.

---

## 6. Ejemplo: flujo SPRINT → ANALYZE

### 6.1 Declaración de la edge

```json
{
  "from": "SPRINT",
  "to": "ANALYZE",
  "gate": "start_task",
  "sub_state_gate": "GATHER",
  "description": "Start task analysis. Promueve task_id al contexto.",
  "context_updates": [
    {"from": "ACTIVE.fields.task_id", "to": "context.task_id"}
  ],
  "transform": {
    "action": "prepare_analysis_context",
    "input": ["ACTIVE.state.task_id"],
    "output": "context.analysis_ready"
  }
}
```

### 6.2 Qué ve el LLM

**SPRINT.ACTIVE → submit --task_id TASK-012 → transition start_task**

```
ANALYZE.GATHER — Gather context

Global context: {
  project_id: "delta-skill",          ← promovido en START
  chain_dir: ".../taskregistry-.../",  ← promovido en START
  task_id: "TASK-012",                ← promovido ahora
}

Analizando task TASK-012 del proyecto delta-skill...
```

---

## 7. Reglas de diseño

| ID | Regla | Descripción |
|----|-------|-------------|
| R1 | **Cada capa tiene un SSOT** | Global = `context`, Local = `sub_state_data`, Pipeline = `transform` |
| R2 | **Los datos locales no persisten entre nodos** | Solo se promueven via `context_updates` |
| R3 | **El LLM nunca recibe datos que no necesite** | Cada nodo declara qué contexto necesita, no se inyecta todo |
| R4 | **Las transiciones son determinísticas** | El engine decide el target basado en gates + transforms, no el LLM |
| R5 | **Cada edge declara explícitamente qué promover** | `context_updates` es obligatorio cuando un nodo produce datos que otros necesitan |
| R6 | **Las promociones son idempotentes** | Si el campo ya existe en context, se sobreescribe (último valor gana) |
| R7 | **`via` es para transformaciones, no para efectos secundarios** | Si necesita IO (ej: resolver chain_dir), use `via`. Si solo copia, use `from→to` directo |

---

## 8. Impacto en el código existente

### 8.1 Modelo de nodo: `provides` + `needs`

**Archivo:** `domain/models.py` — `WorkflowNode` y `SubState`

```python
@dataclass
class WorkflowNode:
    node_id: str
    sub_states: list[SubState] = field(default_factory=list)
    gates: list[Gate] = field(default_factory=list)
    label: str = ""
    description: str = ""
    entry_action_script: str = ""
    on_entry_message: str = ""
    provides: list[str] = field(default_factory=list)  # NUEVO
    """Contexto que este nodo produce. Se promueve automáticamente a global
    state al transicionar. Todos los sub-estados lo heredan."""

@dataclass
class SubState:
    key: str
    label: str = ""
    execution: ExecutionType = ExecutionType.MANUAL
    # ... campos existentes ...
    provides: list[str] = field(default_factory=list)  # NUEVO
    """Contexto que este sub-estado produce. Se promueve al node.provides
    al completar el sub-estado."""
    needs: list[str] = field(default_factory=list)  # NUEVO
    """Contexto que este sub-estado necesita. El ContextResolver lo resuelve
    automáticamente desde: engine context → node provides → global state.
    Si no se cumple, error de validación."""
```

### 8.2 Parseo desde node.json

**Archivo:** `infrastructure/repositories.py` — en `_build_graph()`

```python
# Al construir WorkflowNode:
nodes_dict[node_id] = WorkflowNode(
    node_id=node_id,
    sub_states=sub_states,
    gates=gates,
    label=node_raw.get("label", ""),
    description=node_raw.get("description", ""),
    provides=node_raw.get("provides", []),  # NUEVO
)

# Al construir SubState:
sub_states.append(SubState(
    key=ss_raw.get("key", f"STEP_{i}"),
    # ... campos existentes ...
    provides=ss_raw.get("provides", []),  # NUEVO
    needs=ss_raw.get("needs", []),  # NUEVO
))
```

### 8.3 ContextResolver (NUEVO)

**Archivo:** `application/context_resolver.py` (NUEVO)

```python
class ContextResolver:
    """Resuelve contexto para sub-estados basado en provides/needs.
    
    Flujo:
    1. Colecta engine context (siempre disponible)
    2. Colecta node.provides + sub_state.provides
    3. Para cada need del sub-estado actual:
       a. Busca en engine context
       b. Busca en node.provides (hereda el nodo)
       c. Busca en global state (SprintState.context)
       d. Si no encuentra, error de validación
    4. Inyecta contexto resuelto en ExecutionContext.baggage
    """
    
    def resolve(self, state: SprintState, node: WorkflowNode, 
                sub_state: SubState, session: SessionRecord) -> dict:
        context = {}
        
        # 1. Engine context (siempre)
        context["session_id"] = session.session_id
        context["chain_dir"] = session.chain_dir
        context["current_node"] = state.current_node
        context["current_sub"] = state.current_sub_state
        
        # 2. Node.provides (hereda)
        for key in node.provides:
            if key in state.context:
                context[key] = state.context[key]
        
        # 3. Sub-state.provides (propio)
        for key in sub_state.provides:
            if key in state.context:
                context[key] = state.context[key]
        
        # 4. Resolver needs
        for key in sub_state.needs:
            if key in context:
                continue  # ya resuelto
            if key in state.context:
                context[key] = state.context[key]
                continue
            # No encontrado → error
            raise ContextNotFoundError(key, node.node_id, sub_state.key)
        
        return context
```

### 8.4 Transición automática de provides

Cuando un nodo completa su ejecución y transiciona al siguiente, el engine promueve automáticamente al global state todos los datos declarados en `provides` que estén en `sub_state_data`:

```python
# En TransitionUseCase.execute(), después de apply():
if node.provides:
    for key in node.provides:
        # Buscar en sub_state_data de todos los sub-estados del nodo
        for ss in node.sub_states:
            data_key = f"{node.node_id}.{ss.key}"
            ss_data = state.sub_state_data.get(data_key)
            if ss_data and key in ss_data.fields:
                state.context[key] = ss_data.fields[key]
                break
        # Si no está en sub_state_data pero ya está en context, mantenerlo
        if key not in state.context:
            pass  # opcional: default o warning
```

### 8.5 ¿Qué pasa con `context_updates`?

**Se simplifica.** El mecanismo `context_updates` en edges ya no es necesario como tal. Su función la cumple `provides` en los nodos:

| Mecanismo viejo | Mecanismo nuevo |
|----------------|-----------------|
| `context_updates` en edges | `provides` en nodos + promoción automática |
| `from/to` explícito | El engine infiere desde `sub_state_data` |
| `via` para transformaciones | Transform en edges (se mantiene) |

El `transform` en edges se mantiene para lógica de routing (decidir ACTIVE vs PLANNING). La promoción de datos al contexto global se vuelve automática basada en `provides`.

---

## 9. Decisiones de diseño (refinadas)

### 9.1 `needs` es opcional, pero si se declara se valida

```
REGLAS:
  - needs NO es un campo obligatorio en todos los sub-estados
  - Solo los nodos/sub-estados que REQUIEREN contexto externo
    lo declaran
  - Si un sub-estado declara needs, el engine debe VALIDAR que
    exista un provider para cada necesidad (engine, nodo anterior,
    o contexto global)
  - Si no se declara needs, el sub-estado funciona sin validación
    (puede acceder al contexto global implícitamente si existe)

EJEMPLO:
  // SPRINT.PLANNING necesita project_id y sprint_id para funcionar
  {
    "key": "PLANNING",
    "needs": ["project_id", "sprint_id"],  ← validación en carga
    "execution": "llm_task"
  }

  // SPRINT.CLOSE no necesita contexto externo
  {
    "key": "CLOSE",
    // sin needs ← sin validación
    "execution": "auto"
  }
```

### 9.2 Validación en carga del workflow

Cuando el engine carga `workflow.json` (en `WorkflowRepository._build_graph()`), debe validar que todos los `needs` declarados tienen un provider:

```
ALGORITMO DE VALIDACIÓN:

1. Construir grafo de nodos (dirigido: START → SPRINT → ANALYZE → ...)
2. Colectar engine context: [session_id, chain_dir, current_node, current_sub]
3. Para cada nodo en orden topológico:
   a. Recibir contexto acumulado del nodo anterior (engine + provides)
   b. Para cada sub-estado con needs:
      - Verificar que cada need exista en el contexto acumulado
      - Si falta → ERROR: "Sub-estado X necesita Y pero ningún provider lo produce"
   c. Agregar node.provides + sub_state.provides al contexto acumulado
   d. Pasar contexto acumulado al siguiente nodo

Esto detecta errores de diseño ANTES de ejecutar el workflow.
```

### 9.3 Transiciones automáticas

Si un sub-estado tiene todos sus `required_fields` satisfechos (por submit del agente O por contexto resuelto), el engine puede auto-transicionar sin esperar instrucción explícita del LLM.

```
CONDICIONES PARA AUTO-TRANSITION:
  1. El sub-estado actual tiene execution = llm_task o user_input
  2. Todos los required_fields están presentes (en sub_state_data o context)
  3. El sub-estado NO tiene side_effects que requieran confirmación
  4. La edge tiene un target definido (sub_state_gate)

FLUJO:
  submit({decision: "select", project_id: "delta-skill"})
    → Engine detecta que required_fields = ["decision"] están completos
    → Auto-ejecuta transition project_selected
    → Engine promueve provides al global state
    → Engine ejecuta step() en el nuevo nodo
    → LLM recibe prompt del nuevo nodo sin haber hecho "transition"

El LLM nunca necesita decir "transition". El engine lo hace.
```

### 9.4 ContextResolver vs ContextComposer: mismo módulo, roles diferentes

| Aspecto | ContextResolver | ContextComposer |
|---------|----------------|-----------------|
| **Propósito** | Resolver contexto para el ENGINE | Componer contexto para el LLM |
| **Consumidor** | TransitionUseCase, SubmitDataUseCase, gates | Prompts (llm_task, user_input) |
| **Input** | Engine context + SprintState + provides/needs | Resolved context + ContextProviders registrados |
| **Output** | `ExecutionContext.baggage` (datos para el engine) | `ComposedContext.system + context_layers + user` |
| **Idioma** | "Engine language": session_id, chain_dir, project_id | "LLM language": "project: delta-skill, lang: python" |
| **Validación** | Error si need no tiene provider | No valida — provee lo que tenga |
| **Cuándo se ejecuta** | En cada transición + submit | En cada step() para nodos llm_task/user_input |

**Pueden compartir el mismo módulo** (ej: `context/`) pero tienen pipelines separados:

```
┌────────────────────────────────────────────────────┐
│  context/                                           │
│                                                     │
│  ├── resolver.py ← ContextResolver                  │
│  │   (engine-facing: valida needs, resuelve datos)   │
│  │                                                   │
│  ├── composer.py ← ContextComposer                   │
│  │   (LLM-facing: formatea prompts, capas, contexto) │
│  │                                                   │
│  ├── providers.py ← ContextProviders                 │
│  │   (fuentes de datos: project, ckg, cpg, trace...) │
│  │                                                   │
│  └── registry.py ← ContextProviderRegistry           │
│      (registro central de providers)                 │
└──────────────────────────────────────────────────────┘
```

**Flujo integrado:**

```
1. Transición entre nodos
   → ContextResolver.valida_needs(next_node)
   → ContextResolver.promueve_provides(current_node)
   → ContextResolver.inyecta_en_baggage(exec_ctx)

2. step() en nodo llm_task
   → ContextResolver.resuelve_para_engine(sub_state.needs)
   → ContextComposer.compone_para_llm(
        context_config,
        resolved_context,
        prompt_template
      )
   → Retorna prompt con contexto inyectado
```

### 9.5 Resumen de decisiones

| Decisión | Valor | Fundamento |
|----------|-------|------------|
| `needs` obligatorio | Solo si el sub-estado necesita contexto | No forzar declaración donde no se necesita |
| Validación | En carga del workflow | Error temprano > error en ejecución |
| Transiciones | Automáticas si required_fields están completos | El LLM no debe recordar llamar "transition" |
| Resolver vs Composer | Mismo módulo, roles diferentes | Resolver = engine-facing, Composer = LLM-facing |
| Providers | Compartidos entre ambos | Registry es SSOT, Resolver y Composer lo consultan |

---

## Anexo A: Referencias

- `START_NODE.md` — Diseño del nodo START como entry point
- `workflow_v3_enterprise.md` — Patrones enterprise del workflow engine
- `domain/models.py` — Modelo `Gate`, `SprintState`, `WorkflowGraph`
- `application/use_cases.py` — `TransitionUseCase.execute()`
- `application/orchestrator.py` — `OrchestrationService.transition()`
- `context/providers.py` — Context providers existentes (`ProjectContextProvider`, etc.)
- `infrastructure/repositories.py` — `_build_graph()` donde se parsean las edges

## Anexo B: ADRs

| ID | Decisión | Fundamentos |
|----|----------|-------------|
| ADR-STATE-001 | **Tres capas: Global, Local, Pipeline** | Separación clara de concerns. Cada capa tiene un SSOT y un propósito específico. |
| ADR-STATE-002 | **`context_updates` se declara en edges** | La promoción de datos es parte del contrato entre nodos, no responsabilidad del LLM ni del engine implícitamente. |
| ADR-STATE-003 | **`context_updates` se ejecuta post-transición** | Para que los datos estén disponibles inmediatamente cuando el nuevo nodo ejecute `step()`. |
| ADR-STATE-004 | **`via` es opcional para transformaciones** | No todos los campos necesitan transformación. El caso simple (copy) no debe requerir boilerplate. |
| ADR-STATE-005 | **El LLM no debe recordar datos entre nodos** | Todo dato necesario debe estar en `context` (global) o ser provisto por context providers. Si falta, es un bug de diseño. |
