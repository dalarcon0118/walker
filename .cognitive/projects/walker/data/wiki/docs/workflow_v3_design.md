# Workflow v3 — Diseño del Motor de Nodos

> Estado: Diseño forward-compatible con la definición actual de `workflow.json`.
> Objetivo: separar definitivamente la **API del workflow** (lo que el LLM llama) del **motor de ejecución** (lo que el workflow hace internamente).

---

## 1. Visión general

El workflow describe un proceso como un grafo de **nodos** y **sub-nodos**.  
El motor recorre ese grafo y, en cada paso, ejecuta exactamente una de estas 5 acciones:

| Tipo | ¿Quién ejecuta? | ¿Quién decide cuándo avanzar? | Ejemplo |
|------|----------------|------------------------------|---------|
| `session_bootstrap` | motor | motor / usuario | Asignar `session_id`, pedir proyecto/sprint |
| `auto` | motor | motor | tests, análisis estático, actualizar CPG |
| `llm_task` | LLM externo | motor, basado en output estructurado | análisis 6→3 capas, resumen PO |
| `user_input` | usuario | motor, basado en opciones | aprobar/rechazar/refinar |
| `manual` | agente / LLM | agente | editar plan, completar campos |

**Regla 1:** el LLM solo ve: `step()`, `submit()`, `status()`, `transition()`.  
**Regla 2:** el workflow no expone scripts, CPG, CKG ni DeltaGraph al LLM.  
**Regla 3:** las tareas son puras: `transform(state, context) -> result`.

---

## 2. API Contract

### 2.1 `step(session_id)`

Entrada: `session_id`  
Salida:

```json
{
  "session_id": "sess-...",
  "current": "ANALYZE.GATHER",
  "execution": "auto",
  "prompt": null,
  "required_fields": [],
  "options": null,
  "state_snapshot": {
    "task_id": "TASK-002",
    "node": "ANALYZE",
    "sub_state": "GATHER"
  }
}
```

- Si `execution = auto` → el motor ejecuta automáticamente y devuelve el nuevo estado.
- Si `execution = llm_task` → `prompt` contiene la instrucción para el LLM.
- Si `execution = user_input` → `options` contiene las opciones válidas.

### 2.2 `submit(session_id, data)`

Entrada: `session_id`, `data`  
Salida:

```json
{
  "ok": true,
  "result": {
    "passed": true,
    "findings": [],
    "summary": "..."
  },
  "side_effects": [
    {"type": "update_cpg", "files": 3}
  ],
  "next": "KNOWLEDGE_CAPTURE",
  "prompt": null
}
```

El motor:
1. Valida `data` contra `input_schema` / `required_fields`.
2. Ejecuta la tarea del sub-estado actual.
3. Ejecuta `side_effects`.
4. Decide `on_success` o `on_failure`.
5. Persiste y devuelve el nuevo estado.

### 2.3 `status(session_id)`

Solo lectura. Devuelve `current`, `history`, `state_snapshot`.

### 2.4 `transition(session_id, gate)`

Transición inter-nodo. Antes de aplicar la gate:
1. Ejecuta el sub-estado actual automáticamente si corresponde.
2. Valida precondiciones.
3. Cambia de nodo y persiste.

---

## 3. Definición de nodo / sub-nodo

Cada sub-estado en `workflow.json` se modela así:

```json
{
  "key": "CODE_REVIEW_SCAN",
  "label": "Revisión automática de código",
  "execution": "auto",
  "action": "review_code",
  "input_schema": {},
  "output_schema": {
    "passed": "boolean",
    "findings": "array",
    "summary": "string"
  },
  "on_success": "KNOWLEDGE_UPDATE",
  "on_failure": "REPORT_GATE",
  "side_effects": [
    {"type": "update_cpg", "paths": "artefactos_impactados"}
  ],
  "retry": 0,
  "on_entry_message": "Ejecutando análisis estático..."
}
```

Campos:

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `key` | string | Identificador único dentro del nodo |
| `label` | string | Texto legible |
| `execution` | enum | `session_bootstrap / auto / llm_task / user_input / manual` |
| `action` | string | Nombre de la tarea pura registrada en `TaskRegistry` |
| `input_schema` | object / array | Estructura esperada de entrada |
| `output_schema` | object | Estructura esperada de salida |
| `on_success` | string | Siguiente sub-estado o gate |
| `on_failure` | string | Sub-estado o gate alternativo |
| `side_effects` | array | Efectos secundarios declarativos |
| `retry` | int | Reintentos automáticos en caso de fallo |
| `on_entry_message` | string | Texto inicial para el usuario/LLM |
| `prompt` | string | Prompt para `llm_task` |
| `options` | array | Opciones válidas para `user_input` |

---

## 4. Taxonomía de ejecución

### 4.1 `session_bootstrap`

```
session_bootstrap
  - asigna session_id si no existe
  - detecta si hay proyecto/sprint/tarea existente
  - si no: pide al usuario proyecto, objetivo y tareas iniciales
  - crea sprint y primera tarea
```

### 4.2 `auto`

```
auto
  - el motor ejecuta TaskRegistry.execute(action, state, context)
  - side_effects se ejecutan automáticamente
  - si ok → on_success
  - si falla → on_failure
  - silencio total hacia el LLM unless on_failure requiere intervención
```

### 4.3 `llm_task`

```
llm_task
  - el motor devuelve: prompt + input_schema + required_fields
  - el LLM responde con datos estructurados
  - el motor valida contra output_schema
  - si válido → on_success
  - si inválido → on_failure
```

### 4.4 `user_input`

```
user_input
  - el motor devuelve: user_message + options
  - el usuario elige una opción
  - el LLM traduce la elección a datos estructurados si hace falta
  - el motor valida y decide siguiente paso
```

### 4.5 `manual`

```
manual
  - el motor muestra on_entry_message + required_fields
  - el agente/LLM ejecuta pasos externos
  - cuando completa required_fields, llama submit
```

---

## 5. SideEffects como ciudadanos de primera clase

Formato:

```json
{
  "type": "update_cpg | save_ckg | update_delta_graph | emit_event | persist_snapshot | run_script",
  "params": {}
}
```

Ejemplos:

```json
[
  {"type": "update_cpg", "paths": "artefactos_impactados"},
  {"type": "save_ckg", "nodes": ["decision-042", "pattern-003"]},
  {"type": "update_delta_graph"},
  {"type": "emit_event", "name": "task_created"},
  {"type": "persist_snapshot"},
  {"type": "run_script", "command": "python3 scripts/analyze_refactored.py ."}
]
```

El motor ejecuta side-effects después de la tarea pura y antes de decidir `on_success/on_failure`. Si un side-effect falla, el motor lo registra y continúa o aborta según la estrategia declarada.

---

## 6. TaskRegistry y tareas puras

### 6.1 Registro

```python
TaskRegistry.register("review_code", review_code)
TaskRegistry.register("update_cpg", update_cpg)
TaskRegistry.register("save_ckg", save_ckg)
TaskRegistry.register("run_tests", run_tests)
TaskRegistry.register("build_delta_graph", build_delta_graph)
```

### 6.2 Contrato de tarea pura

```python
@dataclass
class TaskResult:
  ok: bool
  data: dict
  side_effects: list[SideEffect] = field(default_factory=list)
  error: str | None = None
```

Ejemplo:

```python
def review_code(ctx: dict) -> TaskResult:
  files = ctx.get("artefactos_impactados", [])
  findings = run_static_analysis(files)
  cpg = update_cpg(files)

  return TaskResult(
    ok=True,
    data={
      "passed": len(findings) == 0,
      "findings": findings,
      "summary": f"{len(findings)} hallazgos"
    },
    side_effects=[
      {"type": "update_cpg", "result": cpg}
    ]
  )
```

---

## 7. Ejemplos de escenarios

### 7.1 Desarrollo completo de una tarea

```
SESSION_BOOTSTRAP
  execution: session_bootstrap
  ↓
TASK_DEFINITION
  execution: llm_task
  prompt: "Define la tarea con: objective, domain, type, files."
  output_schema: {task_id, objective, ...}
  side_effects: [emit event: task_created]
  ↓
DESIGN_GATHER
  execution: auto
  action: gather_context
  side_effects: [update_cpg, build_delta_graph]
  ↓
DESIGN_ANALYZE
  execution: llm_task
  prompt: "Analiza 6→3 capas."
  input_schema: [cpg_context, ckg_context]
  output_schema: {q1, a1, q2, a2, q3, a3}
  ↓
DESIGN_REVIEW
  execution: user_input
  user_message: "Diseño listo. ¿Aprobar / Rechazar / Refinar?"
  options:
    - decision: approve
    - decision: reject
    - decision: refine
  ↓
CKG_SAVE
  execution: auto
  action: save_to_ckg
  side_effects: [save_ckg, update_delta_graph]
  ↓
DEVELOPMENT
  execution: auto
  action: implement_task
  side_effects: [update_cpg]
  ↓
AUTO_REVIEW
  execution: auto
  action: review_code
  side_effects: [update_cpg]
  ↓
QA_RUN
  execution: auto
  action: run_tests
  side_effects: []
  ↓
QA_VALIDATION
  execution: llm_task
  prompt: "Actúa como QA. Valida resultados de tests."
  output_schema: {qa_approved, evidence}
  ↓
PO_VALIDATION
  execution: llm_task
  prompt: "Actúa como PO. Resume y valida contra objetivo."
  output_schema: {po_approved, feedback}
  ↓
USER_REPORT
  execution: user_input
  user_message: "Tarea completada. ¿Proceder / Refinar / Cancelar?"
  options:
    - decision: proceed
    - decision: refine
    - decision: cancel
  ↓
CLOSE_TASK
  execution: auto
  action: close_task
  side_effects: [emit event: task_completed, persist_snapshot]
```

### 7.2 Flujo de refinamiento

```
REFINE.DIAGNOSE
  execution: llm_task
  prompt: "Diagnostica qué necesita refinamiento."
  output_schema: {target, reason}
  ↓
REFINE.ROUTE
  execution: auto
  action: route_refinement
  side_effects: []
  ↓
  → si target = analyze → DESIGN_ANALYZE
  → si target = implement → DEVELOPMENT
  → si target = verify → QA_RUN
```

### 7.3 Sesión sin estado previo

```
SESSION_BOOTSTRAP
  execution: session_bootstrap
  - detecta: sin session
  - pide: "Selecciona proyecto o crea uno nuevo"
  - side_effects: [assign_session_id]
  ↓
  → si crea proyecto → TASK_DEFINITION
  → si existe sprint → SPRINT.ACTIVE
```

---

## 8. Ejecución real: ejemplo paso a paso

### 8.1 Caso: `ANALYZE.GATHER` con actualización de CPG

**Definición en `workflow.json`:**

```json
{
  "key": "GATHER",
  "execution": "auto",
  "action": "gather_context",
  "side_effects": [
    {"type": "update_cpg", "paths": "artefactos_impactados"}
  ],
  "on_success": "ANALYZE",
  "on_failure": "ANALYZE"
}
```

**Lo que hace el motor:**

1. `session_id = "sess-123"`
2. current = `ANALYZE.GATHER`
3. Ejecuta `TaskRegistry.execute("gather_context", state, context)`
4. Side-effect: `update_cpg(paths="artefactos_impactados")`
5. Resultado → guarda en `state.sub_state_data["GATHER"]`
6. Avanza a `ANALYZE.ANALYZE`
7. Devuelve `StepResult` con `state_snapshot` actualizado

**Lo que ve el LLM:**

```json
{
  "current": "ANALYZE.ANALYZE",
  "execution": "llm_task",
  "prompt": "Analiza la tarea usando el contexto recopilado...",
  "required_fields": ["q1", "a1", "q2", "a2", "q3", "a3"]
}
```

### 8.2 Caso: `IMPLEMENT.CODE_REVIEW_SCAN`

**Definición:**

```json
{
  "key": "CODE_REVIEW_SCAN",
  "execution": "auto",
  "action": "review_code",
  "side_effects": [
    {"type": "update_cpg", "paths": "artefactos_impactados"}
  ],
  "on_success": "KNOWLEDGE_UPDATE",
  "on_failure": "REPORT_GATE"
}
```

**Motor:**

1. Ejecuta `review_code(state)`
2. Si findings > 0 → `on_failure = REPORT_GATE`
3. Si findings == 0 → `on_success = KNOWLEDGE_UPDATE`

**LLM no interviene** a menos que `on_failure` lo requiera.

### 8.3 Caso: `USER_GATE` con traducción

**Definición:**

```json
{
  "key": "USER_GATE",
  "execution": "user_input",
  "user_message": "INFORME PRE-IMPLEMENTACION...",
  "options": [
    {"decision": "proceed", "description": "Proceder"},
    {"decision": "refine", "requires": ["notes"]},
    {"decision": "cancel", "requires": ["reason"]}
  ],
  "on_success": "WORK",
  "on_failure": "SPRINT"
}
```

**Motor:**

1. Devuelve al LLM: `{execution: "user_input", user_message: "...", options: [...]}`
2. LLM recibe decisión del usuario: `"proceder"`
3. LLM llama: `submit(session_id, {decision: "proceed"})`
4. Motor valida `decision` contra `options`
5. Avanza a `WORK`

---

## 9. Arquitectura empresarial — Capas

```
┌────────────────────────────────────────────────────────────┐
│                        API Layer                            │
│  step(), submit(), status(), transition()                   │
│  (única entrada para LLM / usuario / agentes)               │
├────────────────────────────────────────────────────────────┤
│                   Workflow Engine                           │
│  - Carga workflow.json → WorkflowGraph                      │
│  - Ejecuta sub-estados según execution type                │
│  - Aplica side_effects                                      │
│  - Decide on_success / on_failure                           │
│  - Persiste sprint-state.json                               │
├────────────────────────────────────────────────────────────┤
│                  Task Registry                              │
│  review_code, update_cpg, save_ckg, run_tests, ...         │
│  Tareas puras: state, context → TaskResult                  │
├────────────────────────────────────────────────────────────┤
│               Side Effects Engine                           │
│  update_cpg, save_ckg, update_delta_graph, emit_event,     │
│  persist_snapshot, run_script                               │
├────────────────────────────────────────────────────────────┤
│                    Event Bus                                │
│  task_created, analysis_approved, qa_failed, ...           │
├────────────────────────────────────────────────────────────┤
│                    Persistencia                             │
│  sprint-state.json  /  snapshots  /  event_log              │
├────────────────────────────────────────────────────────────┤
│ Grafos externos (solo invocados desde side_effects)         │
│  CPG (cpg.json)  /  CKG (ckg.json)  /  DeltaGraph           │
└────────────────────────────────────────────────────────────┘
```

**Límite de responsabilidad:**

- **API Layer**: exponer solo `step/submit/status/transition`.
- **Workflow Engine**: orquestar nodos, ejecutar tareas, aplicar side-effects.
- **Task Registry**: lógica pura reutilizable.
- **Side Effects Engine**: efectos externos declarativos.
- **Event Bus**: comunicación entre dominios sin acoplamiento.
- **Persistencia**: estado durable del workflow.
- **Grafos externos**: datos de contexto, no controlan el flujo.

---

## 10. Mapeo: definición actual → v3

| Campo actual | Campo v3 | Cambio |
|------------|---------|------|
| `mode: auto` | `execution: auto` | Renombre semántico |
| `mode: manual` | `execution: manual` | Renombre semántico |
| `auto_complete` | `action` + `side_effects` | Script externo → tarea registrada |
| `on_entry_message` | `prompt` / `user_message` | Más específico por tipo |
| `required_fields` | `input_schema` / `required_fields` | Unificación |
| `on_failure_loop` | `retry` + `on_failure` | Más expresivo |
| `data_contract` | `output_schema` | Contrato explícito |

**Compatibilidad:** el loader actual puede mapear automáticamente los campos legacy a la nueva estructura en `_build_graph()`.

---

## 11. Checklist de implementación

### Dominio
- [ ] `ExecutionType` enum en `domain/models.py`
- [ ] `TaskResult` dataclass
- [ ] `SideEffect` dataclass
- [ ] `SubState` extensions: `execution`, `action`, `input_schema`, `output_schema`, `side_effects`, `on_success`, `on_failure`
- [ ] `TaskRegistry` en `domain/registry.py`

### Infraestructura
- [ ] `TaskRegistry` implementación concreta
- [ ] `SideEffectEngine` en `infrastructure/side_effects.py`
- [ ] `ScriptRunner` migrado a side-effect type `run_script`
- [ ] `EventBus` integrado al motor

### Aplicación
- [ ] `WorkflowEngine.step()` unificado
- [ ] `WorkflowEngine.submit()` con validación de schemas
- [ ] `SubmitDataUseCase` refactorizado para usar `ExecutionType`
- [ ] `TransitionUseCase` refactorizado para usar `ExecutionType`

### API
- [ ] `commands.py` expone solo `step/submit/status/transition`
- [ ] `walker.py` CLI consume la nueva API

### Workflow definition
- [ ] Reescribir `workflow.json` con `execution` y `action`
- [ ] Registrar todas las acciones en `TaskRegistry`
- [ ] Migrar `auto_complete` legacy a `action` + `side_effects`

### Testing
- [ ] Test unitario por ExecutionType
- [ ] Test de TaskRegistry
- [ ] Test de SideEffectEngine
- [ ] Test de integración end-to-end de flujo completo

---

## 12. Preguntas abiertas

1. **Session bootstrap:** ¿el `session_id` se asigna en el motor o en la API layer?  
   Sugerencia: API layer asigna, motor lo usa.
2. **Side-effects fallidos:** ¿qué hace el motor?  
   Sugerencia: registra en `error` de `TaskResult`, decide `on_failure` o continúa según estrategia declarada.
3. **LLM malicioso / datos inválidos:** ¿bloqueo o reintento?  
   Sugerencia: `retry` en sub-estado + `on_failure` a nodo de error.
4. **Migración de `workflow.json`:** ¿auto o manual?  
   Sugerencia: auto con mapeo legacy + override manual por nodo.

---

*Documento generado: 2026-07-23. Base: sesión de diseño workflow v3.*
