# Casos de Uso con Escenarios — Workflow Scrum Master Refactor

> **Sprint:** `cognitive-mcp-workflow-scrum-master-refactor-20260709`
> **Fuente:** Sesión de documentación 2026-07-09 (`ses_0b6f539d2ffeuqIQBYZYh7LpBB`)
> **Propósito:** Referencia del flujo **Usuario → LLM → MCP** para entender cómo se integra el módulo Agent con el módulo Workflow.

---

## Metodología empleada

1. **Mapear actores del sistema**
2. **Identificar punto de entrada único** (`WorkflowRouter.route(sessionID, op, data)`)
3. **Cubrir el árbol de decisión del router** — cada rama = un caso de uso
4. **Añadir bordes del sistema** (condiciones transversales)

### Actores

```
Usuario Humano ──► LLM (agente IA) ──► MCP (WorkflowRouter)
                     ▲                      │
                     └── (siempre decide) ───┘
```

---

## Árbol de decisión → Casos de Uso

```
                      ┌─ sessionID existe ──┬─ op=current ──► UC-01: Consultar estado
                      │                     ├─ op=next    ──► UC-02: Avanzar paso
                      │                     ├─ op=previous ─► UC-03: Historial
                      │                     └─ op inválida ─► UC-04: Op rechazada
sessionID ──► Router ─┤
                      └─ sessionID NO existe ─► UC-05: Clasificar mensaje nuevo

Bordes transversales:
  Enforcement rechaza  ──► UC-04
  Error en ejecución   ──► UC-06: Error / rollback
  Ciclo multi-turno    ──► UC-07: Conversación larga
  LLM desvía del camino ──► UC-08: Desvío del LLM
```

---

## Caso de Uso Principal: LLM → MCP con sessionID

**Actor:** LLM (agente de IA)
**Entrada:** `tool_call(sessionID, op, data)`

### Narrativa

1. El LLM invoca una tool del MCP con `sessionID`, `op` (opcional), `data` (opcional).
2. **WorkflowRouter** recibe la llamada como punto de entrada único.
3. Verifica existencia de `sessionID`:
   - **Flujo A** (existe): el workflow ya está en progreso.
   - **Flujo B** (no existe): es una nueva interacción.

### Flujo A — Session existente

4. `WorkflowRouter` delega a **WorkflowEngine** para recuperar `WorkflowSessionState`.
5. Engine construye respuesta contextual: `normativeInstructions`, `current`, `next`, `previous`, `schema`.
6. El LLM **decide** qué op invocar (`current` / `next` / `previous`).
7. `WorkflowRouter` valida la op contra **EnforcementTable** (Allow/Deny/Delegate).
8. Si permitida → `WorkflowEngine` ejecuta y emite eventos.
9. Nuevo estado se persiste y retorna al LLM.

### Flujo B — Session nueva

4. `WorkflowRouter` delega al **MessageClassifier** (módulo existente).
5. El clasificador determina intención, dominio y tipo de tarea.
6. Se crea `WorkflowSessionState` + `WorkflowDefinition`.
7. Retorna la clasificación al LLM para iniciar el workflow.

### Las 3 ops del workflow

| Op | Cuándo se usa | Lo que retorna el MCP |
|----|---------------|------------------------|
| `current` | **Primera vez siempre** (default). El LLM se orienta | Estado actual: fase, step, instrucciones normativas |
| `next` | Cuando el LLM decide avanzar | Siguiente step + datos + schema |
| `previous` | Cuando el LLM necesita contexto previo | Historial del step anterior + resultados |

**Decisión arquitectónica clave:** `WorkflowRouter` recibe la tool call. Si sessionID existe → `WorkflowEngine`; si no → `MessageClassifier`. **El LLM es quien decide** con el contexto recibido — el MCP indica dirección (next/previous) pero no fuerza la decisión.

---

## UC-05: Clasificar mensaje nuevo (sesión nueva)

**Actor:** LLM sin sessionID
**Entrada:** `tool_call(sin sessionID, data: mensaje_del_usuario)`
**Ejercita:** MessageClassifier + WorkflowEngine + creación de estado

1. `WorkflowRouter` recibe la llamada sin `sessionID`.
2. Detecta que no hay sesión activa → delega a **MessageClassifier**.
3. MessageClassifier analiza el mensaje → `Classification(intent, domain, confidence)`.
4. **WorkflowEngine** crea `WorkflowSessionState`: genera sessionID, asigna fase ONBOARDING, carga `WorkflowDefinition`, inicializa `normativeInstructions`.
5. Persiste el estado.
6. Retorna al LLM: `{sessionID, classification, normativeInstructions, current}`.

**Variantes:**
- Clasificación falla (confidence < threshold) → error pidiendo más info.
- Dominio no soportado → `MessageClassifier` retorna `unknown_domain` → fase IDLE.

---

## UC-02: Avanzar paso (next)

**Actor:** LLM con sesión activa
**Entrada:** `tool_call(sessionID, "next", data: {campos del schema})`
**Ejercita:** Router + Enforcement + Engine + CognitiveGPS + EventBus

1. `WorkflowRouter.route(sessionID, "next", data)` → verifica sessionID.
2. `EnforcementTable.check(phase, "next")` → permite? Si Denied → UC-04.
3. `WorkflowEngine.advance(state, step)`:
   a. Carga `WorkflowSessionState`.
   b. Valida `data` contra `schema`.
   c. Si requiere datos cognitivos → `CognitiveGPS.get_distance(sessionID)`.
   d. Ejecuta `StepInstruction`.
   e. Calcula siguiente step según `WorkflowDefinition`.
   f. Actualiza estado.
4. Emite `StepCompletedEvent` + `PhaseAdvancedEvent`.
5. Persiste y retorna nuevo contexto.

---

## UC-04: Op rechazada por Enforcement

**Actor:** LLM pide op no permitida en la fase actual
**Ejercita:** Router + EnforcementTable

1. `WorkflowRouter.route(sessionID, "next")` → `RouteAction`.
2. `EnforcementTable.check(phase, "next")` → `EnforceResult(allowed=False, reason, allowed_ops)`.
3. Router **no delega a Engine**. Retorna error directo con `allowed_ops`.
4. LLM decide: `current` para consultar, o `previous` para historial.

**Reglas:**

| Condición | Resultado |
|-----------|-----------|
| Op permitida en fase | `allowed=True` → pasa a Engine |
| Op no permitida | `allowed=False` + `reason` + `allowed_ops` |
| Op requiere delegación | `allowed=True` + `delegate_to="handler_name"` |

---

## UC-06: Error en step + rollback

**Actor:** LLM con datos inválidos / fallo de sistema
**Ejercita:** Engine (error handling) + EventBus + preservación de estado

1. Route + Enforcement OK (igual UC-02).
2. `WorkflowEngine.advance()` ejecuta `StepInstruction`.
3. Ocurre error (validación / sistema / negocio).
4. Engine **captura y NO avanza** el estado.
5. Emite `ErrorEvent(phase, step, error, sessionID)`.
6. Preserva estado anterior intacto.
7. Retorna: `error`, `error_type`, `rollback_available: true`, `suggested_action`.
8. LLM decide: reintentar (`next` con datos corregidos) o cancelar (`previous`).

**Postcondición:** El estado del workflow NO avanzó.

---

## UC-08: LLM desvía del camino sugerido

**Actor:** LLM ignora `next` y elige `previous`
**Ejercita:** Router + Enforcement + Engine (previous consultivo)

1. `WorkflowRouter.route(sessionID, "previous")` → `RouteAction`.
2. `EnforcementTable.check(phase, "previous")` → permitido.
3. `WorkflowEngine.previous(state)`:
   a. Carga estado actual.
   b. Recupera historial del step anterior.
   c. **Registra desviación** en trail cognitivo.
   d. NO modifica `current_phase` (previous es consulta).
4. Retorna contexto + `deviation_warning`.
5. LLM re-evalúa.

**Variantes:**
- Previous destructivo: en ciertas fases SÍ muta estado (rollback). EnforcementTable lo controla.
- Desviación recurrente (3+ veces): advertencia se intensifica.

**Regla de diseño (ADR-005):** El workflow **NUNCA bloquea** al LLM. Solo advierte. El LLM es la inteligencia.

---

## Integración con componentes (matriz)

| Componente | Rol en el caso de uso | Casos de uso |
|-----------|----------------------|--------------|
| **WorkflowRouter** | Punto de entrada. SessionID existe → Engine; no → Classifier | UC-01, UC-02, UC-04, UC-05, UC-06, UC-08 |
| **WorkflowEngine** | Recupera/guarda estado, ejecuta avance | UC-02, UC-05, UC-06, UC-08 |
| **EnforcementTable** | Valida ops por fase (Allow/Deny/Delegate) | UC-02, UC-04, UC-08 |
| **MessageClassifier** | Clasifica mensajes nuevos (sin sessionID) | UC-05 |
| **CognitiveGPS** | Data provider: distance, predicates, history | UC-02, UC-06 |
| **WorkflowEventBus** | Eventos: StepCompleted, PhaseAdvanced, Error, RouteDecision, Classification | UC-02, UC-04, UC-06 |

---

## Decisiones arquitectónicas aplicables (SSOT del sprint)

| Decisión | Significado |
|-----------|-------------|
| `MCP_no_bloquea` | El LLM puede llamar cualquier tool; el workflow solo controla su propio avance de fase |
| `CognitiveState` | Data provider puro (R(D)→S), no decide |
| `EnforcementTable` | Advierte (ADR-005), no bloquea. Desviación con escalación progresiva |
| `MessageClassifier` | Vive en `features/workflow/application/classifier.py`; clasifica `development\|non_development` |
| `SessionFactory` | Usa `SessionService.save()` existente |
| `dispatch_pipeline` | Entry point que delega a Orchestrator (~600 líneas), mantiene GPS phase |

---

## Relevancia para la integración Agent ↔ Workflow (tarea actual)

- El punto de integración Agent→Workflow está en **UC-05** y en `MessageClassifier`.
- Hoy `features/agent/application/message_gate.py` y `llm_response_hook.py` importan **directo** de `features.messages` (MessageService, MessageClassifier) — antipatrón que Workflow ya resolvió con `MessagePort`.
- La arquitectura documentada dicta que `MessageClassifier` resuelve el Flujo B (sessionID no existe). Agent debe consumirlo vía `MessagePort`, no importándolo.
- `WorkflowOrchestrator` (ya con `MessageServiceAdapter`) orquesta UC-02/UC-06.
- **Pendiente (UC-09):** documentar `Agent → WorkflowOrchestrator → MessageStoreGate` con la misma metodología.
