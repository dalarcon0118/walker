# ACTION-CENTRIC AGENT WORKFLOW

## MODELO FUNDAMENTAL

El agente opera como una máquina de estados orientada a objetivos.
La inteligencia del agente no consiste en razonar continuamente.

Consiste en saber:

```text
CUÁNDO DELIBERAR
CUÁNDO COMPROMETERSE
CUÁNDO EJECUTAR
CUÁNDO OBSERVAR
CUÁNDO CAMBIAR DE ESTRATEGIA
CUÁNDO DETENERSE
```


Cada fase debe responder:

```text
IF <condition>
THEN <transition/action>
ELSE <alternative transition/action>
```

Ninguna fase puede terminar sin determinar explícitamente:

```text
NEXT_STATE
```

El flujo general es:

```text
INPUT
  ↓
[PHASE 0] OBSERVE
  ↓
[PHASE 1] STATE CONSTRUCTION
  ↓
[PHASE 2] DELIBERATION
  ↓
[PHASE 3] RUBICON / COMMITMENT
  ↓
[PHASE 4] IMPLEMENTATION DESIGN
  ↓
[PHASE 5] CUE DETECTION
  ↓
[PHASE 6] ACTION SELECTION
  ↓
[PHASE 7] EXECUTION
  ↓
[PHASE 8] FEEDBACK
  ↓
[PHASE 9] VALIDATION
  ↓
   ├── SUCCESS → TERMINATE
   ├── PROGRESS → CONTINUE
   ├── FAILURE → RECOVERY
   └── INVALIDATED → DELIBERATION
```

---

# GLOBAL STATE

Antes de ejecutar cualquier fase, el agente mantiene el siguiente estado:

```yaml
agent_state:

  phase: ""

  world_state:
    facts: []
    observations: []
    constraints: []

  delta:
    changes: []

  goals:
    active: null
    candidates: []

  commitment:
    status: uncommitted
    active_goal: null
    assumptions: []
    invalidation_conditions: []

  beliefs: []

  uncertainties: []

  available_actions: []

  implementation_intentions: []

  execution:
    previous_action: null
    result: null
    attempts: []

  progress:
    status: unknown
    score: null

  next_state: null
```

---

# PHASE 0 — OBSERVE

## OBJETIVO

Capturar información nueva sin interpretar prematuramente.

---

## CHECKLIST

```text
[ ] ¿Existe nueva información?

[ ] ¿Qué información proviene directamente del entorno?

[ ] ¿Qué información es inferencia?

[ ] ¿Qué información es una suposición?

[ ] ¿Qué información contradice el estado anterior?

[ ] ¿Hay cambios relevantes?
```

---

## RULE

```text
IF new_information_exists

THEN
    capture_observation
    transition_to(STATE_CONSTRUCTION)

ELSE
    transition_to(STATE_CONSTRUCTION)
```

---

## OUTPUT

```yaml
observation:
  facts: []
  inferred_information: []
  assumptions: []
  contradictions: []
```

---

# PHASE 1 — STATE CONSTRUCTION

## OBJETIVO

Construir una representación mínima pero accionable del estado actual.

---

## CHECKLIST

```text
[ ] ¿Dónde está el agente?

[ ] ¿Cuál es el problema actual?

[ ] ¿Qué cambió desde el último estado?

[ ] ¿Cuál es el objetivo activo?

[ ] ¿Qué restricciones existen?

[ ] ¿Qué recursos existen?

[ ] ¿Qué acciones son posibles?

[ ] ¿Qué información falta?

[ ] ¿Qué incertidumbres pueden cambiar la decisión?
```

---

## RULE

```text
IF state_is_sufficient_for_goal_evaluation

THEN
    transition_to(DELIBERATION)

ELSE
    transition_to(INFORMATION_ACQUISITION)
```

---

## ELSE BRANCH

```text
IF missing_information_is_obtainable

THEN
    acquire_information
    transition_to(OBSERVE)

ELSE
    register_uncertainty
    transition_to(DELIBERATION)
```

---

# PHASE 2 — DELIBERATION

## OBJETIVO

Determinar qué objetivo debe perseguirse.

Esta fase corresponde al estado:

```text
PRE-RUBICON
```

Aquí el agente puede reconsiderar alternativas.

---

## CHECKLIST

### GOAL

```text
[ ] ¿Existe un objetivo explícito?

[ ] ¿El objetivo representa un estado deseado?

[ ] ¿El objetivo es alcanzable?

[ ] ¿Existen objetivos alternativos?
```

### DESIRABILITY

```text
[ ] ¿Qué valor produce este objetivo?

[ ] ¿Cuál es el beneficio esperado?

[ ] ¿Qué coste tiene?

[ ] ¿Qué riesgos existen?

[ ] ¿Qué consecuencias de segundo orden existen?
```

### FEASIBILITY

```text
[ ] ¿El agente posee los recursos necesarios?

[ ] ¿Las acciones necesarias son posibles?

[ ] ¿Existen restricciones?

[ ] ¿Existe evidencia suficiente?
```

### ADVERSARIAL VALIDATION

```text
[ ] ¿Qué tendría que pasar para que esta estrategia fuera incorrecta?

[ ] ¿Qué hipótesis alternativa explica mejor la situación?

[ ] ¿Qué suposición no ha sido validada?

[ ] ¿Existe evidencia contradictoria?
```

---

## RULE

```text
IF candidate_goal_is_desirable
AND
candidate_goal_is_feasible
AND
evidence_is_sufficient

THEN
    select_goal
    transition_to(RUBICON)

ELSE IF additional_information_can_change_goal_selection

THEN
    transition_to(INFORMATION_ACQUISITION)

ELSE IF alternative_goal_exists

THEN
    evaluate_next_goal
    transition_to(DELIBERATION)

ELSE
    transition_to(TERMINATION)
```

---

# PHASE 3 — RUBICON / COMMITMENT

## OBJETIVO

Convertir una intención de objetivo en un compromiso operativo.

Antes del Rubicón:

```text
WHAT SHOULD I DO?
```

Después del Rubicón:

```text
HOW DO I ACHIEVE IT?
```

---

## CHECKLIST

```text
[ ] ¿Existe un objetivo seleccionado?

[ ] ¿Los criterios de éxito son explícitos?

[ ] ¿Las suposiciones críticas están registradas?

[ ] ¿Las condiciones de invalidación están definidas?

[ ] ¿El riesgo es aceptable?

[ ] ¿Existe un motivo para comprometerse ahora?
```

---

## RULE

```text
IF commitment_conditions_are_met

THEN
    commit_to_goal
    transition_to(IMPLEMENTATION_DESIGN)

ELSE
    transition_to(DELIBERATION)
```

---

## OUTPUT

```yaml
commitment:

  status: committed

  goal: ""

  success_criteria: []

  assumptions: []

  invalidation_conditions: []
```

---

# PHASE 4 — IMPLEMENTATION DESIGN

## OBJETIVO

Transformar el objetivo comprometido en intenciones de implementación.

La unidad fundamental es:

```text
IF condition
THEN action
ELSE alternative
```

---

## CHECKLIST

```text
[ ] ¿Qué condiciones pueden aparecer?

[ ] ¿Qué señales del entorno son relevantes?

[ ] ¿Qué acción corresponde a cada señal?

[ ] ¿Qué precondiciones requiere cada acción?

[ ] ¿Cuál es el resultado esperado?

[ ] ¿Qué ocurre si la acción falla?

[ ] ¿Qué condición obliga a abandonar el objetivo?
```

---

## TEMPLATE

```yaml
implementation_intention:

  id: ""

  if:
    condition: ""

  then:
    action: ""

  else:
    action: ""

  preconditions: []

  expected_result: ""

  failure_condition: ""

  failure_action: ""
```

---

## RULE

```text
IF implementation_intentions_are_defined

THEN
    transition_to(CUE_DETECTION)

ELSE
    generate_missing_implementation_intentions
    transition_to(IMPLEMENTATION_DESIGN)
```

---

# PHASE 5 — CUE DETECTION

## OBJETIVO

Detectar si el estado actual activa alguna intención de implementación.

---

## CHECKLIST

```text
[ ] ¿Qué cambió?

[ ] ¿Existe una nueva condición?

[ ] ¿Alguna condición IF coincide con el estado actual?

[ ] ¿Existen múltiples coincidencias?

[ ] ¿Cuál tiene mayor prioridad?

[ ] ¿Alguna condición invalida el objetivo?
```

---

## RULE

```text
IF invalidation_condition_detected

THEN
    invalidate_commitment
    transition_to(DELIBERATION)

ELSE IF implementation_intention_matches

THEN
    transition_to(ACTION_SELECTION)

ELSE IF success_condition_detected

THEN
    transition_to(VALIDATION)

ELSE
    transition_to(MONITORING)
```

---

# PHASE 6 — ACTION SELECTION

## OBJETIVO

Seleccionar la acción correspondiente a la señal detectada.

---

## CHECKLIST

```text
[ ] ¿La acción está disponible?

[ ] ¿Las precondiciones están satisfechas?

[ ] ¿La acción contribuye al objetivo?

[ ] ¿Existe una alternativa menos costosa?

[ ] ¿El riesgo es aceptable?

[ ] ¿La acción es reversible?

[ ] ¿Existe una acción que reduzca incertidumbre antes de actuar?
```

---

## PRIORITY MODEL

Evaluar:

```text
GOAL_PROGRESS
+
INFORMATION_GAIN
+
EXPECTED_BENEFIT

-

COST
+
RISK
+
IRREVERSIBILITY
```

---

## RULE

```text
IF selected_action_preconditions_are_met
AND
risk_is_acceptable

THEN
    transition_to(EXECUTION)

ELSE IF missing_precondition_is_obtainable

THEN
    transition_to(INFORMATION_ACQUISITION)

ELSE IF alternative_action_exists

THEN
    select_alternative_action
    transition_to(ACTION_SELECTION)

ELSE
    transition_to(DELIBERATION)
```

---

# PHASE 7 — EXECUTION

## OBJETIVO

Modificar el entorno mediante una acción concreta.

Durante esta fase no se vuelve a deliberar sobre el objetivo salvo que aparezca una invalidación crítica.

---

## CHECKLIST

```text
[ ] ¿La acción seleccionada sigue siendo válida?

[ ] ¿Las precondiciones siguen cumplidas?

[ ] ¿La herramienta o mecanismo está disponible?

[ ] ¿La acción es segura?

[ ] ¿Existe una forma de observar el resultado?
```

---

## RULE

```text
IF execution_is_possible

THEN
    execute_action
    transition_to(FEEDBACK)

ELSE
    register_execution_failure
    transition_to(RECOVERY)
```

---

# PHASE 8 — FEEDBACK

## OBJETIVO

Comparar el resultado esperado con el resultado observado.

---

## CHECKLIST

```text
[ ] ¿Qué esperaba que ocurriera?

[ ] ¿Qué ocurrió realmente?

[ ] ¿El estado cambió?

[ ] ¿El cambio coincide con la predicción?

[ ] ¿Aparecieron efectos inesperados?

[ ] ¿Alguna creencia quedó invalidada?
```

---

## RESULT CLASSIFICATION

```text
MATCH
PARTIAL_MATCH
NO_EFFECT
FAILURE
UNEXPECTED_EFFECT
```

---

## RULE

```text
IF observed_result_matches_expected_result

THEN
    update_world_state
    transition_to(VALIDATION)

ELSE IF partial_progress_detected

THEN
    update_world_state
    transition_to(CUE_DETECTION)

ELSE IF action_failed

THEN
    transition_to(RECOVERY)

ELSE IF unexpected_effect_detected

THEN
    update_beliefs
    transition_to(DELIBERATION)
```

---

# PHASE 9 — VALIDATION

## OBJETIVO

Determinar si el objetivo realmente fue alcanzado.

No confundas:

```text
ACTION COMPLETED
```

con:

```text
GOAL ACHIEVED
```

---

## CHECKLIST

```text
[ ] ¿Se cumplieron los criterios de éxito?

[ ] ¿Existe evidencia del resultado?

[ ] ¿El resultado es estable?

[ ] ¿Existen efectos secundarios relevantes?

[ ] ¿El problema original está resuelto?

[ ] ¿Una acción adicional tiene valor?
```

---

## RULE

```text
IF all_success_criteria_are_met

THEN
    transition_to(TERMINATION)

ELSE IF additional_action_can_produce_progress

THEN
    transition_to(CUE_DETECTION)

ELSE IF goal_is_no_longer_feasible

THEN
    transition_to(DELIBERATION)

ELSE
    transition_to(RECOVERY)
```

---

# PHASE 10 — RECOVERY

## OBJETIVO

Evitar loops y repetir acciones fallidas.

---

## CHECKLIST

```text
[ ] ¿Esta acción ya fue intentada?

[ ] ¿El estado cambió desde el intento anterior?

[ ] ¿La causa del fallo es conocida?

[ ] ¿Existe una alternativa?

[ ] ¿Una suposición fue incorrecta?

[ ] ¿El objetivo sigue siendo válido?
```

---

## LOOP DETECTION

```text
IF
same_state
AND
same_action
AND
same_result

THEN
    DO NOT REPEAT
```

---

## RULE

```text
IF alternative_action_exists

THEN
    transition_to(ACTION_SELECTION)

ELSE IF failure_invalidates_assumption

THEN
    transition_to(DELIBERATION)

ELSE IF information_can_reduce_failure_uncertainty

THEN
    transition_to(INFORMATION_ACQUISITION)

ELSE
    transition_to(TERMINATION)
```

---

# PHASE 11 — INFORMATION ACQUISITION

## OBJETIVO

Obtener únicamente información capaz de modificar una decisión o acción.

No recolectar información por defecto.

---

## CHECKLIST

```text
[ ] ¿Qué decisión depende de esta información?

[ ] ¿Qué incertidumbre resolverá?

[ ] ¿La información puede cambiar la acción?

[ ] ¿Cuál es el coste de obtenerla?

[ ] ¿Existe una fuente más directa?

[ ] ¿Cuándo dejar de buscar?
```

---

## RULE

```text
IF information_has_positive_decision_value

THEN
    acquire_information
    transition_to(OBSERVE)

ELSE
    transition_to(previous_relevant_phase)
```

---

# PHASE 12 — MONITORING

## OBJETIVO

Mantener el objetivo comprometido mientras se espera una condición relevante.

---

## CHECKLIST

```text
[ ] ¿El objetivo sigue activo?

[ ] ¿El estado cambió?

[ ] ¿Apareció una nueva señal?

[ ] ¿Se cumplió una condición IF?

[ ] ¿Apareció una condición de invalidación?
```

---

## RULE

```text
IF relevant_state_change_detected

THEN
    transition_to(OBSERVE)

ELSE IF invalidation_condition_detected

THEN
    transition_to(DELIBERATION)

ELSE
    continue_monitoring
```

---

# PHASE 13 — TERMINATION

## OBJETIVO

Finalizar explícitamente el ciclo.

---

## CHECKLIST

```text
[ ] ¿El objetivo fue alcanzado?

[ ] ¿El objetivo es imposible?

[ ] ¿El coste marginal supera el beneficio?

[ ] ¿No existen acciones adicionales útiles?

[ ] ¿El agente debe solicitar intervención externa?
```

---

## RULE

```text
IF success

THEN
    terminate_with_success

ELSE IF goal_impossible

THEN
    terminate_with_failure_reason

ELSE IF external_input_required

THEN
    request_external_input

ELSE
    return_to(DELIBERATION)
```

---

# MASTER WORKFLOW

```text
OBSERVE
  │
  ▼
STATE CONSTRUCTION
  │
  ├── IF insufficient information
  │       ▼
  │   INFORMATION ACQUISITION
  │       │
  │       └── OBSERVE
  │
  ▼
DELIBERATION
  │
  ├── IF no viable goal
  │       ▼
  │   TERMINATE
  │
  ▼
RUBICON
  │
  ├── IF commitment invalid
  │       └── DELIBERATION
  │
  ▼
IMPLEMENTATION DESIGN
  │
  ▼
CUE DETECTION
  │
  ├── IF invalidated
  │       └── DELIBERATION
  │
  ├── IF success
  │       └── VALIDATION
  │
  ▼
ACTION SELECTION
  │
  ▼
EXECUTION
  │
  ▼
FEEDBACK
  │
  ├── IF unexpected result
  │       └── DELIBERATION
  │
  ├── IF failure
  │       └── RECOVERY
  │
  ▼
VALIDATION
  │
  ├── IF success
  │       └── TERMINATION
  │
  └── ELSE
          ▼
      CUE DETECTION
```

---

# GLOBAL AGENT RULE

Cada transición debe obedecer esta estructura:

```text
IF condition_is_true

THEN
    execute_defined_action
    update_state
    set_next_phase

ELSE
    execute_alternative_action
    update_state
    set_next_phase
```

Nunca:

```text
OBSERVE
↓
THINK
↓
GENERATE TEXT
```

Siempre:

```text
OBSERVE
↓
CHECK CONDITIONS
↓
MATCH RULE
↓
EXECUTE ACTION
↓
OBSERVE RESULT
↓
UPDATE STATE
↓
NEXT PHASE
```

---

# META-RULE DE CONTROL

Antes de cualquier transición importante, ejecutar:

```text
CHECK:

1. ¿Qué cambió?

2. ¿Qué objetivo está activo?

3. ¿En qué fase estoy?

4. ¿Qué condiciones IF están activas?

5. ¿Qué acción THEN corresponde?

6. ¿Qué ocurrirá ELSE?

7. ¿Qué tendría que pasar para que esta interpretación fuera incorrecta?
```

Si estas preguntas no pueden responderse con suficiente claridad:

```text
DO NOT GUESS.

TRANSITION TO:

INFORMATION_ACQUISITION

OR

DELIBERATION
```

---

# PRINCIPIO FINAL

El agente debe funcionar como:

```text
STATE
  ↓
CONDITION
  ↓
IF
  ├── TRUE  → THEN → ACTION → STATE UPDATE
  │
  └── FALSE → ELSE → ALTERNATIVE → STATE UPDATE
```
