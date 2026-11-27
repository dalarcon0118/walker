# SPEC-WALKER-045 — WorkContract y los cuatro controles del agente

- **Estado:** borrador (2026-11-24) — pendiente de aprobación.
- **Estrategia:** [docs/4D.md](4D.md) — Duplicidad, Desviación, Diluvio y Disfraz.
- **ADR:** [ADR-WALKER-086](ADR-WALKER-086-work-contract-cuatro-controles.md).
- **Sprint:** `SPR-4D-20261124` (planning; F0a/F0b/F0c + F1a–F1d al final, con `depends_on`).
- **Alcance:** walker core + workflow nodes + flight-control harness.
- **Estado del plan:** persistido en DeltaGraph; aprobación del PO pendiente.
- **Relacionado:** ADR-WALKER-077/080/083/084/085.

## 1. Problema

El agente tiene memoria (CKG/CPG/Delta/Trace) y proceso (workflow/gates), pero
falla en cuatro puntos de control:

1. **Duplicidad:** crea código nuevo antes de buscar reuso en CKG, Delta y CPG.
2. **Desviación:** pierde el goal original a medida que avanza la sesión.
3. **Diluvio:** produce diffs demasiado grandes para revisar.
4. **Disfraz:** declara `completed` sin verificar los criterios; el problema
   reaparece sesiones después.

La causa común no es falta de información: es que el contrato de trabajo no
está explícito, no se reinyecta y no se valida por evidencia.

## 2. Objetivo

Definir y persistir un `WorkContract` por spec, con cuatro controles
ejecutables, para que:

- el reuso se decida con candidatos reales antes de diseñar;
- el goal se mantenga anclado y todo write sea trazable a un criterio;
- el diff se acote por spec y se revise antes de seguir;
- `completed` y `close` exijan un manifest de verificación vigente;
- la recurrencia reabra la spec anterior con un test de regresión.

## 3. No-objetivos

- No agregar cuatro tools nuevas: se extienden `brief`/`preflight` (lectura) y
  `capture`/`spec_predicates`/`spec_status` (escritura).
- No cambiar el esquema de `Spec` fuera de `task_context`.
- No implementar embeddings o un motor de similitud externo en F0/F1.
- No reemplazar el workflow FSM: se agregan campos, gates y checkpoints.
- No juzgar intención del agente; se observan efectos y evidencia.

## 4. WorkContract

Persistido en `spec.task_context.contract`:

```json
{
  "schema_version": 1,
  "goal": {
    "statement": "resultado observable",
    "original_intent": "pedido original del usuario",
    "version": 1,
    "approved_by": "user",
    "approved_at": "ISO-8601",
    "invalidation_conditions": ["si ... el goal ya no aplica"]
  },
  "task": {
    "spec_id": "SPEC-001",
    "objective": "...",
    "scope": ["archivo/a.py"],
    "budget": {"max_files": 3, "max_loc": 200, "max_new_files": 1}
  },
  "reuse": {
    "scanned_at": "ISO-8601",
    "candidates": [
      {
        "source": "ckg|cpg|delta",
        "id": "adr-077|SprintService.resolve_context|SPEC-014",
        "why": "por qué se parece",
        "similarity": 0.0
      }
    ],
    "decision": "reuse|extend|new",
    "rationale": "...",
    "waiver": ""
  },
  "acceptance": [
    {
      "criterion": "...",
      "predicate": {"type": "command_exit", "command": "..."},
      "status": "pending|pass|fail",
      "evidence": "..."
    }
  ],
  "verification": {
    "commit_hash": "...",
    "revision": "...",
    "verified_at": "ISO-8601",
    "runner": "pytest|command|manual",
    "manifest": [],
    "reviewed_by": "sesión distinta del autor",
    "stale": false
  },
  "recurrence": {
    "of_spec": "SPEC-008",
    "root_cause": "...",
    "regression_predicate": "..."
  }
}
```

Reglas de persistencia:

- El contrato se escribe en el mismo save que la spec (no hay estado
  intermedio).
- `schema_version` permite leer specs sin contrato: `{}` es válido y se
  completa al iniciar/reabrir.
- `brief` proyecta un subconjunto acotado; no devuelve el contrato completo si
  excede el budget de contexto.
- `capture`/`spec_status` son los únicos writers del bloque `verification`.

## 5. Los cuatro controles

### 5.1 Reuse control

**Señal:** objetivo/archivos/símbolos nuevos contra CKG, Delta y CPG.

**Fuentes:**

- Delta: specs por objetivo/`problem_statement`/archivos; `related_specs`.
- CKG: decisiones/convenciones con `applies_to`; `supersedes`.
- CPG: outline, call graph, símbolos y similitud de funciones.

**Gate:**

- `DESIGN_ANALYZE` exige `reuse_decision` con `candidates`, `decision`,
  `rationale`; `new` con candidato de similitud alta exige waiver del PO.
- El harness niega `edit`/`write` de archivo nuevo o símbolo top-level si no
  existe `reuse_scan` para la tarea.
- `CODE_REVIEW_SCAN` marca duplicado/clon como finding.

**Remedio:** `walker <project>/brief '{"task_id":"...","files":[...]}'` con
`reuse=true` o el comando de scan que exponga la tool.

**Aceptación:**

- [ ] Un `DESIGN_ANALYZE` sin `reuse_decision` no avanza.
- [ ] `new` con similitud ≥ umbral y sin waiver queda en `needs_refine`.
- [ ] El harness bloquea un write nuevo sin `reuse_scan` y el deny incluye el
      comando exacto.

### 5.2 Goal control

**Señal:** goal/versión/acceptance vs tarea/scope/archivos actuales.

**Fuentes:** sprint goal, `session_plan`, spec objective, `WorkContract.goal`,
CKG decisions, Trace.

**Gate:**

- Cadena `goal → spec → criterio → archivo` verificada en `SPRINT.ACTIVE`,
  `DESIGN_REVIEW`, `USER_GATE`, `REPORT_GATE`, `PO_ACCEPT`.
- Cambio de goal solo por `session_plan` aprobado o `goal_invalidation`.
- El harness reinyecta el ancla por turno y detecta cambio de tema/scope.

**Remedio:** `session_plan` con `goal`, `acceptance`, `scope`, `next` y
`approved_by_user=true`; o `transition goal_invalidation` con `reason`.

**Aceptación:**

- [ ] Un write fuera del scope no pasa sin `session_plan` aprobado.
- [ ] El flight card muestra goal, criterio actual y próximo paso.
- [ ] `goal_invalidation` deja `goal.version+1` y reason en Trace.

### 5.3 Budget control

**Señal:** archivos tocados, líneas cambiadas, archivos nuevos, review debt.

**Fuentes:** Trace (`writes`, `files`), `what_changed`, `plan_diff`, CPG.

**Gate:**

- La spec declara `budget`; al excederlo, `WORK` no sigue: split de spec o
  ampliación aprobada.
- `CODE_REVIEW_RESULT`/`REPORT_GATE` exigen `review_verdict` y findings.
- `SPRINT.REVIEW` agrega reviews; `CLOSE` bloquea review pendiente.

**Remedio:** `transition edit_plan` para dividir la spec, o `session_plan`
`mode=add` con aprobación y nuevo budget.

**Aceptación:**

- [ ] Un write que excede `max_files`/`max_loc` se bloquea con remedio.
- [ ] No hay `REPORT_GATE` sin `review_verdict`.
- [ ] `SPRINT.CLOSE` lista specs con review pendiente y no cierra.

### 5.4 Verification control

**Señal:** criterios sin predicado, predicado fallido, manifest stale, review
del autor, recurrencia.

**Fuentes:** `acceptance_criteria`, `predicates`, `verify_predicates`,
`review_findings`, CKG (fallo/fix), Delta (specs previas).

**Gate:**

- `PLAN_APPROVAL`/`TASK_SPEC`: `unmapped=[]` o waiver del PO.
- `VERIFY.PREDICATE_CHECK`: todos los predicados verdes; manifest con commit y
  revisión.
- `PO_ACCEPT`: `accept` solo con manifest vigente y reviewer ≠ autor.
- `SPRINT.CLOSE`: revalida manifests; stale bloquea.
- Recurrencia: `reopen_spec` con `regression_predicate`; no spec nueva que
  oculte el fallo.

**Remedio:** `spec_predicates` para mapear, `verify_predicates` para ejecutar,
`spec_status` con artefactos para completar, `reopen_spec` para recurrencia.

**Aceptación:**

- [ ] `spec_status completed` falla si falta manifest o está stale.
- [ ] `close_spring` falla si una spec completada tiene verificación stale.
- [ ] Una recurrencia detectada reabre la spec anterior y exige regresión.

## 6. Mapa por capa

| Capa | Responsabilidad | No debe hacer |
| :--- | :--- | :--- |
| **Usuario** | decidir reuso/nuevo, aprobar goal/cambios, aceptar con evidencia | escribir código, certificar técnicamente |
| **Agente** | proponer contrato, ejecutar una spec, mapear criterios, detenerse en budget | auto-certificar `completed`, cambiar goal sin aprobación |
| **Walker** | persistir contrato, calcular similitud/recurrencia, imponer invariantes, emitir Trace | juzgar intención, reemplazar al reviewer |
| **Harness** | inyectar contrato, observar efectos, bloquear con remedio, medir | reimplementar reglas de negocio o duplicar políticas |

## 7. Fases

### F0 — Observabilidad y contrato mínimo

**Cambios:**

- `brief`/`preflight`: proyectar `contract`, `reuse`, `verification`,
  `recurrence` (vacíos si no existen).
- `capture`/`spec_status`: escribir `task_context.contract` de forma atómica.
- Trace: `reuse_scan`, `goal_anchor_injected`, `budget_snapshot`,
  `criteria_unmapped`, `verification_stale`, `false_done`.
- Tests BDD con `tmp_path` y specs sin contrato.

**Checklist:**

- [ ] M-01 El contrato se escribe en el mismo save que la spec — anti-patrón:
      **Transaction Script**.
- [ ] M-02 `brief` proyecta desde una sola fuente, sin recalcular por
      superficie — anti-patrón: **Duplicate Code**.
- [ ] M-03 El schema versionado tolera specs sin contrato — anti-patrón:
      **Anemic Domain Model**.

### F1 — Contratos en el workflow (advisory)

**Cambios:**

- `DESIGN_ANALYZE`: `reuse_decision` requerido; candidatos desde
  `brief`/`preflight`.
- `STATE_CONSTRUCTION`: `budget` y `scope` en el plan.
- `TASK_SPEC`/`PLAN_APPROVAL`: acceptance → predicates, `unmapped` visible.
- `VERIFY`: `verification_manifest` con commit/revisión.
- Prompts: goal anchor y criterion table.

**Checklist:**

- [ ] M-02 `reuse_decision` usa las tres fuentes, sin heurística local —
      anti-patrón: **Magic Numbers**.
- [ ] M-03 Cada criterio se mapea o queda en `unmapped` visible — anti-patrón:
      **Boolean Trap**.
- [ ] M-05 El manifest se ata a commit/revisión y detecta stale — anti-patrón:
      **Hidden Dependency**.

### F2 — Gates duros del harness

**Cambios:**

- Gate `reuse`: write nuevo sin `reuse_scan` → deny con comando.
- Gate `budget`: write sobre budget → deny con split/approval.
- Gate `done`: `completed`/`close` sin manifest vigente → deny.
- Gate `drift`: write fuera de scope o goal vacío → deny con `session_plan`.
- Eventos `gate.decision` con `rule_id`, `effect`, `remedy`.

**Checklist:**

- [ ] M-04 Cada deny emite `gate.decision` con remedio — anti-patrón:
      **Eventual Consistency sin safeguards**.
- [ ] M-05 Los gates son deterministas y no mezclan políticas — anti-patrón:
      **God Class**.
- [ ] M-01 La evidencia se persiste post-commit — anti-patrón: **Hidden
      Dependency**.

### F3 — Aprendizaje (similitud y recurrencia)

**Cambios:**

- Detector de clones/similitud de funciones (configurable por lenguaje).
- `recurrence_of` edge en Delta y nodo `fallo→causa→fix→regresión` en CKG.
- Reapertura automática sugerida en `brief` y `session_bind resume`.
- Dashboard de métricas de los cuatro controles.

**Checklist:**

- [ ] M-02 Una sola política de similitud/umbral — anti-patrón: **Magic
      Numbers**.
- [ ] M-03 La recurrencia es idempotente y exige regresión — anti-patrón:
      **Anemic Domain Model**.
- [ ] M-06 El detector no está acoplado a un lenguaje/índice rígido —
      anti-patrón: **Not Invented Here**.

## 7.1 PO Review 2026-11-24 — refinamiento del plan 086

**Veredicto:** el plan es correcto en intención pero **no ejecutable tal como está**.
Se aprueba solo con estos refinamientos; F1a–F1d no arrancan hasta cerrar F0.

### Hallazgos

1. **F0 está sobredimensionado.** `preflight` mide `knowledge.py` con 76
   callers, `handle_brief` cyclo 151, `capture.py` 11 callers y
   `spec_status.py` 9 callers. Meter contrato, persistencia y proyecciones en
   una sola spec es un riesgo alto y un diff difícil de revisar.
2. **Faltan dependencias.** F1a–F1d dependen de F0c (proyecciones). El plan no
   las declara porque `sprint_planning` no acepta `depends_on`; se refuerza por
   orden de ejecución y por `TASK_SPEC` al iniciar cada una.
3. **Acceptance vacía.** Las 5 specs tienen predicados, pero
   `acceptance_criteria` está vacío y `spec_predicates` reporta
   `criteria_not_in_spec`. Para el control de “Disfraz”, el criterio tiene que
   existir en la spec, no solo en el bundle de predicados.
4. **Faltan budgets.** Sin `max_files`/`max_loc` no hay control de “Diluvio”.
5. **Estado del workflow inconsistente.** El workflow está `SPRINT.ACTIVE`
   mientras el sprint 086 sigue `planning` y el plan está
   `approved_by_user=false`. Se resuelve al aprobar o al volver a
   `SPRINT.PLANNING`.

### Refinamiento de F0

F0 se parte en tres incrementos, cada uno con su propio test y presupuesto:

| Incremento | Alcance | Archivos | Budget | Depende de |
| :--- | :--- | :--- | :--- | :--- |
| **F0a** | Schema `WorkContract` + validador puro | `domain/work_contract.py`, `tests/test_work_contract_schema_bdd.py` | 2 archivos / 150 LOC | — |
| **F0b** | Persistencia atómica en `capture`/`spec_status` | `handlers/capture.py`, `handlers/spec_status.py`, `tests/test_work_contract_persist_bdd.py` | 3 archivos / 120 LOC | F0a |
| **F0c** | Proyección en `brief`/`preflight` + eventos Trace | `memories/handlers/knowledge.py`, `tests/test_work_contract_brief_bdd.py`, test de Trace | 3 archivos / 150 LOC | F0a, F0b |

`knowledge.py` solo entra en F0c, después de que el contrato y la persistencia
estén verdes. F0c es el incremento de mayor riesgo y requiere review
independiente.

### Presupuesto del sprint

| Spec | Foco | Budget |
| :--- | :--- | :--- |
| F0a | schema + validador | 2 archivos / 150 LOC |
| F0b | persistencia | 3 archivos / 120 LOC |
| F0c | proyecciones | 3 archivos / 150 LOC |
| F1a | reuse control | 4 archivos / 180 LOC |
| F1b | goal + criterios | 4 archivos / 180 LOC |
| F1c | manifest + stale | 4 archivos / 160 LOC |
| F1d | budget control | 4 archivos / 160 LOC |

### Orden de ejecución

`F0a → F0b → F0c → F1a → F1b → F1c → F1d`.

**Decisión PO aplicada:** el sprint 086 se limita a F0. `SPEC-001..003` son
F0a/F0b/F0c en `draft`; `SPEC-004..007` (F1a–F1d) quedaron en `backlog` para el
siguiente sprint. El goal del 086 es “F0: WorkContract + observabilidad”.

### Condición de aprobación

El plan se aprueba cuando el PO confirme este refinamiento. Recién ahí:
`sprint_status active` → `spec_start SPEC-001` → F0a. Sin aprobación, el
workflow debe volver a `SPRINT.PLANNING`.

## 8. failure_scenarios

```json
"failure_scenarios": [
  {
    "fallo": "El agente crea un archivo nuevo sin reuse_scan y duplica lógica",
    "causa_raiz": "el gate de reuso no existe o el scan no es barato",
    "mitigacion": "reuse_scan obligatorio; candidatos en brief; deny con comando",
    "deteccion": "reuse_scan ausente en Trace; duplicate_blocked en review"
  },
  {
    "fallo": "El goal cambia sin aprobación y el sprint entrega otra cosa",
    "causa_raiz": "el ancla no se reinyecta y session_plan no es obligatorio",
    "mitigacion": "goal anchor por turno; drift gate; goal_invalidation explícito",
    "deteccion": "drift_detected/scope_expansion; goal_version sin aprobación"
  },
  {
    "fallo": "Un diff excede el budget y la review se vuelve imposible",
    "causa_raiz": "no hay budget ni checkpoint de review por spec",
    "mitigacion": "budget en la spec; deny al exceder; review antes de REPORT_GATE",
    "deteccion": "budget_exceeded, oversized_diff, review_coverage"
  },
  {
    "fallo": "Se marca completed sin que los criterios se cumplan",
    "causa_raiz": "completion no exige manifest ni mapeo de criterios",
    "mitigacion": "manifest por commit; completion invariant; PO_ACCEPT con tabla de criterios",
    "deteccion": "false_done, criteria_unmapped, verification_stale"
  },
  {
    "fallo": "El código cambia después del verde y el cierre pasa igual",
    "causa_raiz": "el cierre no revalida commit/revisión del manifest",
    "mitigacion": "close revalida; stale bloquea; re-verificar antes de cerrar",
    "deteccion": "stale_verification en close"
  },
  {
    "fallo": "El mismo problema reaparece en otra sesión y se crea una spec nueva",
    "causa_raiz": "no hay detección de recurrencia ni reopen con regresión",
    "mitigacion": "recurrence_of + reopen_reason + regression_predicate",
    "deteccion": "recurrence_detected, reopened_spec, regression_rate"
  }
]
```

## 9. Criterios de aceptación globales

- [ ] `brief` devuelve `contract`, `reuse`, `verification` y `recurrence` para
      una spec con contrato; `{}`/vacíos para una spec vieja, sin romper.
- [ ] `spec_status completed` falla si falta `verification_manifest` o si el
      `commit_hash` no coincide.
- [ ] `close_spring` falla si una spec completada tiene `verification.stale`.
- [ ] `DESIGN_ANALYZE` no avanza sin `reuse_decision`.
- [ ] El harness bloquea write nuevo sin `reuse_scan` y write sobre budget, con
      remedio exacto en el mensaje.
- [ ] Una recurrencia detectada reabre la spec anterior y exige
      `regression_predicate`.
- [ ] La suite completa sigue verde y los tests nuevos usan `tmp_path`.

## 10. Plan de tests

- `tests/test_work_contract_bdd.py`: schema, persistencia atómica, lectura de
  specs viejas, proyección de `brief`.
- `tests/test_reuse_control_bdd.py`: candidatos CKG/Delta/CPG, decisión,
  waiver, deny del harness.
- `tests/test_goal_control_bdd.py`: goal anchor, drift por scope/tema,
  `session_plan`/`goal_invalidation`.
- `tests/test_budget_control_bdd.py`: budget, split, review por spec, cierre.
- `tests/test_verification_manifest_bdd.py`: mapeo de criterios, manifest,
  stale, close, recurrencia.
- `tests/test_flight_control_work_contract_bdd.py`: gates de harness con
  `gate.decision` y remedio.
- E2E: un proyecto `tmp_path` con una spec que duplica, otra que se desvía, un
  diff sobre budget y un falso `completed`; el sistema debe detectar los cuatro
  y dejar evidencia en Trace.

## 11. Rollout y compatibilidad

- F0 es read-only para specs existentes: `contract={}` y `brief` no falla.
- F1 agrega campos requeridos solo a specs nuevas o reabiertas.
- F2 se activa por configuración (`WORK_CONTRACT_GATES=off|advisory|enforce`)
  para medir falsos positivos antes de bloquear.
- F3 es opt-in por proyecto (`reuse.similarity`, `clone_detector`).
- Los gates duros no se activan hasta que F0/F1 tengan métricas y remedios
  probados.

## 12. Checklist de segundo orden

1. **Módulos que importan esto:** `memories/handlers/knowledge`,
   `tasks/handlers/*`, `delta_tracking/domain/spec_service`,
   `delta_tracking/application/services/sprint_service`, `workflow/*`,
   `code_analysis/*`, `composition`, `agent-plugins/walker-flight-control`.
2. **Side effects:** persistencia del contrato con la spec; eventos de Trace;
   no hay writes ocultos.
3. **Eventos:** `reuse_scan`, `reuse_decision`, `goal_anchor`, `drift_detected`,
   `budget_exceeded`, `verification_manifest`, `stale_verification`,
   `recurrence_detected`, `gate.decision`.
4. **Tests:** brief/preflight, prompt_context, spec_status, verify_predicates,
   close, flight-control y los BDD nuevos.
5. **MCP/CLI/CKG/Delta/Trace:** `brief` gana proyecciones; `capture`/
   `spec_status` escriben contrato; Delta usa `task_context`; CKG gana
   reuso/fallo; Trace gana eventos; no hay tools nuevas.

## 13. Preguntas abiertas

- ¿La similitud de F3 usa embeddings, MinHash o AST normalizado? Empezar por
  AST/token shingles para no agregar dependencias.
- ¿El budget se mide en líneas del diff o en líneas nuevas? Propuesta: líneas
  nuevas + archivos tocados.
- ¿La recurrencia se detecta por texto o por predicado fallido? Propuesta:
  ambas señales; la segunda es más fuerte.
- ¿El waiver del PO se registra en la spec o en el CKG? Propuesta: spec para
  el caso puntual; CKG si se repite como excepción.
- ¿Quién firma la review independiente en un agente? Propuesta: otra sesión o
  el usuario; el autor nunca.
