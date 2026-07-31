# Casos de Uso — Integración Agent · Messages · Workflow · ScrumMaster

> **Sprint:** `cognitive-mcp-agent-messages-workflow-scrum-integration-20260710`
> **Patrón:** Clasificación dirigida por esquema + enrutamiento data-driven
> **Relacionado:** `cognitive-mcp-workflow-scrum-master-refactor-20260709/use-cases.md` (UC internos de Workflow)

---

## Arquitectura de integración

```
Usuario
   │ texto
   ▼
LLM (agente de IA — llena ClassificationSchema)
   ▲ │
   │ │ tool_call(sessionID, op, data) + classification JSON
   │ ▼
┌──────────────────────────────────────────────────────────┐
│ AGENT = front-door / driving adapter (interfaz del MCP)    │
│  • Presenta ClassificationSchema al LLM                    │
│  • Valida la salida del LLM                                │
│  • Graba el mensaje en Messages SIEMPRE (por sessionID)    │
│  • Enruta por is_development:                              │
│      false → FREE (solo Messages)                          │
│      true  → escala a Workflow (SCRUM)                     │
└───────┬──────────────────────────┬────────────────────────┘
        │ MessagePort               │ escalate (sessionID compartido)
        ▼                           ▼
┌──────────────┐        ┌─────────────────────────────────────┐
│ MESSAGES     │        │ WORKFLOW = dueño estado dev          │
│ (BC storage) │        │ WorkflowRouter · SessionManager ·     │
│ record/class │        │ Engine · EnforcementTable ·           │
│ ify_context  │        │ WorkflowClassifier(fallback)          │
└──────────────┘        │  SIEMPRE graba user+result por session│
                        │  SCRUM → orquesta ScrumMaster          │
                        └──────────────┬────────────────────────┘
                                       │ ScrumIntegrator (READ-ONLY)
                                       ▼
                              ┌──────────────────────┐
                              │ SCRUMMASTER (SSOT)    │
                              │ project/sprint/task   │
                              │ sprint goal · verify  │
                              └──────────────────────┘
```

**Regla de oro:** Agent escribe en Messages; Workflow lee de Messages. Solo Workflow toca ScrumMaster. Mensajes no entran a Workflow salvo que sean tareas de desarrollo.

---

## Contrato: ClassificationSchema (AGT-101)

Agent entrega este esquema al LLM; el LLM lo llena por cada mensaje del usuario:

```json
{
  "is_development": true,
  "domain": "backend | frontend | mobile | ...",
  "type": "feature | bugfix | refactor | ...",
  "task_id": "TASK-xxx | null",
  "confidence": 0.92
}
```

- `is_development` es la **señal de enrutamiento** (Agent decide FREE vs SCRUM).
- Si `confidence` baja o falta → Agent cae a `MessagePort.classify_context()` (fallback de código).
- El esquema se valida antes de enrutar (no se enruta con JSON sucio).

---

## UC-09: Promoción FREE → SCRUM (CASO NUEVO)

**Actor:** LLM (vía Agent)
**Entrada:** mensaje del usuario + `ClassificationSchema` llenado
**Ejercita:** Agent (schema+router) → Messages (graba) → Workflow (escala) → ScrumMaster (orquesta)

### Flujo

```
Turno 1 (FREE):
  User: "hola"
   → Agent presenta esquema, LLM llena {is_development:false}
   → Agent graba en Messages (sessionID=X)
   → No escala a Workflow. LLM libre.

Turno N (FREE → SCRUM):
  User: "implementa login con OAuth"
   → LLM llena {is_development:true, domain:backend, type:feature}
   → Agent valida el JSON
   → Agent graba en Messages (sessionID=X, mismo de la conversación)
   → Agent escala a Workflow pasando sessionID=X
   → Workflow crea WorkflowSessionState(session_id=X)  ← sessionID COMPARTIDO
   → Workflow orquesta ScrumMaster: create_project / create_sprint / verify_task
   → Se enganchan recursos cognitivos (code graph, etc.)

Turnos siguientes (SCRUM):
  → SessionRouter ya tiene sessionID=X → SCRUM
  → Todo va directo a Workflow (no re-clasifica)
  → Workflow lee historial de Messages (mismo sessionID=X)
```

### Postcondición
- El sessionID se conserva de extremo a extremo (continuidad de conversación).
- Mensajes de FREE y SCRUM conviven en Messages bajo el mismo sessionID.
- Workflow solo existe para la porción SCRUM.

### SessionRouter (AGT-102)
`sessionID → FREE | SCRUM`. `mark_scrum(session_id)` promueve conservando el id. Agent no re-clasifica sesiones ya SCRUM.

---

## UC-05 (refinado): Agent como front-door clasificador

El UC-05 original decía "WorkflowRouter recibe la llamada sin sessionID → MessageClassifier".
Refinado: **Agent** recibe la llamada (es la interfaz del MCP), presenta el esquema,
el LLM clasifica, y Agent decide si escala a Workflow. WorkflowRouter sigue siendo
el entry point de la lógica de Workflow, pero el LLM no lo toca directo sin pasar por Agent.

---

## Matriz de responsabilidades

| Módulo | Rol | Dueño de | Depende de |
|--------|-----|----------|-----------|
| **Agent** | Front-door MCP: esquema, validación, enrutamiento | — (coordina) | Messages vía `MessagePort`; Workflow vía llamada |
| **Messages** | Storage de mensajes + clasificación (fallback) | mensajes | — |
| **Workflow** | Orquestador dev: estado, fase/step, enforcement | `WorkflowSessionState` | Messages vía `MessagePort`; ScrumMaster vía `ScrumIntegrator` |
| **ScrumMaster** | SSOT scrum: project/sprint/task + sprint goal | scrum | — |

---

## Decisiones SSOT (del sprint)

| Decisión | Significado |
|----------|-------------|
| `Agent_es_front_door` | Agent = driving adapter. Escala a Workflow solo si `is_development=true` |
| `Messages_SSOT_storage` | Messages es BC propio; Agent escribe, Workflow lee |
| `Clasificacion_LLM_primario_fallback_codigo` | LLM llena schema (primario); `classify_context()` es fallback |
| `sessionID_compartido` | Promoción FREE→SCRUM conserva sessionID |
| `ScrumMaster_READ_ONLY` | Workflow orquesta vía ScrumIntegrator; ScrumContext frozen |
| `Enrutamiento_determinista` | Ruta = `if is_development` sobre JSON validado; sin IA en la ruta |
