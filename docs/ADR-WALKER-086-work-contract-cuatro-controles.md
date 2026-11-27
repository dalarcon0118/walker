# ADR-WALKER-086 — Estrategia 4D: WorkContract para duplicidad, desviación, diluvio y disfraz

- **Estado:** propuesto (2026-11-24) — pendiente de aprobación del usuario.
- **Fecha:** 2026-11-24
- **Estrategia:** [docs/4D.md](4D.md) — narrativa del bucle y mapa por capa.
- **Relacionado:** ADR-WALKER-004/036 (delta SSOT), ADR-WALKER-043 (protocolo
  SSOT), ADR-WALKER-077 (sprint por sesión), ADR-WALKER-080 (plan de vuelo),
  ADR-WALKER-083 (gate por efecto), ADR-WALKER-084 (bucle metacognitivo),
  ADR-WALKER-085 (resolver único de sprint).
- **Spec:** [SPEC-WALKER-045](SPEC-WALKER-045-work-contract-agent-controls.md).

## Contexto

El bucle `usuario → agente → walker → harness` ya tiene las piezas de memoria y
de proceso: CKG (decisiones/convenciones), CPG (estructura/call graph), Delta
(sprints/specs/tareas), Trace (conducta), workflow FSM (fases y gates) y
flight-control (inyección y bloqueo). Sin embargo, el agente sigue mostrando
cuatro fallos sistemáticos que no son de conocimiento sino de **control**:

| Fallo | Síntoma medido | Qué falta |
| :--- | :--- | :--- |
| **Duplicidad** | crea código nuevo sin buscar si ya existe | una búsqueda de reuso obligatoria y barata antes de diseñar |
| **Desviación** | el goal original se diluye a medida que avanza la sesión | un ancla de goal reinyectada y una cadena de trazabilidad goal→spec→criterio→archivo |
| **Diluvio** | un diff enorme que nadie puede revisar | un budget por spec y una review obligatoria como unidad de cierre |
| **Disfraz** | declara `completed` sin que los criterios se cumplan; el mismo problema reaparece sesiones después | un manifest de verificación ejecutable, revalidación al cerrar y detección de recurrencia |

El estado actual tiene gates valiosos pero incompletos:

- `DESIGN_ANALYZE` tiene un ADR-lite (`design_brief`) pero **no exige** una
  decisión de reuso con candidatos de CKG/Delta/CPG.
- `STATE_CONSTRUCTION` tiene `plan` pero no un budget de archivos/líneas.
- `SpecValidator` acepta “criterios **o** predicados”; no exige que **todos**
  los criterios estén mapeados.
- `_completion_missing` exige `commit_hash`, `completed_at`, `review_verdict` y
  `evidence`, pero **no** exige que `verify_predicates` haya corrido sobre ese
  commit.
- `close_spring` liquida specs `in_review` y exige revisión independiente, pero
  no revalida manifests: una verificación vieja puede sobrevivir a un cambio de
  código.
- `brief` inyecta plan/impacto/decisiones, pero no candidatos de reuso, estado
  criterio-por-criterio ni recurrencia.
- El harness bloquea bind/intake/metacog/scope, pero no “código nuevo sin
  scan”, “write fuera de budget” ni “done sin manifest”.

La lección de ADR-085 se aplica de nuevo: **no crear cuatro políticas
paralelas**. Un solo artefacto, un solo lector y un solo escritor.

## Estrategia 4D

La estrategia completa vive en [docs/4D.md](4D.md): **D**uplicidad,
**D**esviación, **D**iluvio y **D**isfraz. Cada D ataca un eslabón distinto del
bucle y no se resuelve con el mismo tipo de control:

- **Duplicidad** → control de búsqueda: hacer barato y obligatorio el scan de
  reuso antes de crear.
- **Desviación** → control de atención: reinyectar el goal y verificar la
  cadena goal→spec→criterio→archivo en cada checkpoint.
- **Diluvio** → control de compromiso: budget por spec y review como unidad de
  cierre.
- **Disfraz** → control de evidencia: manifest ejecutable, revalidación al
  cerrar y recurrencia con regresión.

Este ADR no reemplaza la estrategia: la convierte en decisiones, invariantes y
gates verificables. La spec la convierte en fases y tests.

## Decisión

1. **Adoptar `WorkContract` como artefacto operativo por spec.** Vive en
   `spec.task_context.contract`, con `goal`, `task`, `reuse`, `acceptance`,
   `verification` y `recurrence`. No es una entidad nueva de dominio: es la
   proyección persistida de datos que ya existen en Delta, CKG, CPG y Trace.
2. **Un lector y un escritor.** `brief` (y `preflight`) proyecta el contrato;
   `capture`/`spec_predicates`/`spec_status` lo escriben. No se agregan cuatro
   tools nuevas: se agregan campos, gates y eventos sobre el mismo contrato.
3. **Cuatro controles, cada uno en su capa:**
   - **Reuse control:** `reuse_decision` obligatoria en `DESIGN_ANALYZE`; el
     harness no permite crear archivo/símbolo nuevo sin un `reuse_scan` de la
     tarea.
   - **Goal control:** `goal` inmutable durante el sprint salvo
     `session_plan` aprobado o `goal_invalidation`; el harness reinyecta el
     ancla y detecta drift/scope expansion.
   - **Budget control:** budget por spec (`max_files`, `max_loc`,
     `max_new_files`); al excederlo se detiene la escritura y se exige review o
     split aprobado.
   - **Verification control:** cada criterio tiene predicado; `completed`
     exige `verification_manifest` verde del commit actual; `close` revalida;
     la recurrencia reabre la spec anterior con test de regresión.
4. **Capas con responsabilidades separadas:**
   - **Usuario:** decide reuso/nuevo, aprueba goal y cambios, acepta con
     evidencia. No escribe código ni certifica.
   - **Agente:** propone el contrato, ejecuta una spec, declara a qué criterio
     sirve cada write, se detiene en el budget. No se auto-certifica.
   - **Walker:** persiste el contrato, impone invariantes, calcula
     similitud/recurrencia y emite eventos. No juzga intención.
   - **Harness:** inyecta el contrato, observa y bloquea por efecto con remedio
     exacto. No reimplementa reglas de negocio.
5. **Primero medir, después bloquear.** F0 solo proyecta y emite eventos; F1
   hace advisory los campos; F2 activa gates duros; F3 agrega similitud de
   código y recurrencia. Cada deny incluye el comando que lo desbloquea
   (ADR-084/FC-INV-04).
6. **La unidad de revisión es la spec, no el sprint.** `SPRINT.REVIEW` agrega
   reviews de specs; `SPRINT.CLOSE` no cierra con review pendiente, verificación
   stale o criterios sin predicado.

## Invariantes

- **WC-1:** `completed` implica `verification_manifest.verified=true` para el
  `commit_hash` actual y `reviewed_by` distinto del autor.
- **WC-2:** no hay `close` de sprint si alguna spec completada tiene
  verificación stale o review pendiente.
- **WC-3:** no hay `DESIGN_ANALYZE` aprobado sin `reuse_decision` con
  candidatos y rationale (o waiver del PO).
- **WC-4:** no hay write de código nuevo sin `reuse_scan` de la tarea; el scan
  es barato, determinista y trazable.
- **WC-5:** el goal solo cambia por `session_plan` aprobado por el usuario o
  por `goal_invalidation`; nunca por inferencia del agente.
- **WC-6:** cada acceptance criterion tiene predicado ejecutable o waiver
  explícito; `unmapped` no vacío bloquea la aprobación del plan.
- **WC-7:** el budget se evalúa antes de la review; excederlo no se “arregla”
  con más código, se divide la spec o se aprueba la ampliación.
- **WC-8:** una recurrencia reabre la spec anterior (`reopen_reason` +
  `regression_predicate`); no se crea una spec nueva que oculte el fallo.

## failure_scenarios

```json
"failure_scenarios": [
  {
    "fallo": "El agente crea un módulo nuevo que ya existía en CKG/CPG/Delta",
    "causa_raiz": "la búsqueda de reuso es opcional y el write no tiene gate de scan",
    "mitigacion": "reuse_scan obligatorio en DESIGN_ANALYZE; harness bloquea archivo/símbolo nuevo sin evento reuse_scan; candidatos inyectados en brief",
    "deteccion": "eventos reuse_scan/reuse_hit/duplicate_blocked; review_code marca clones"
  },
  {
    "fallo": "El goal original se pierde y el agente implementa otra cosa",
    "causa_raiz": "el goal solo vive en el prompt inicial; no se reinyecta ni se compara con la escritura",
    "mitigacion": "goal anchor por turno; cadena goal→spec→criterio→archivo; session_plan/goal_invalidation obligatorios ante drift",
    "deteccion": "goal_anchor_injected, drift_detected, scope_expansion en Trace"
  },
  {
    "fallo": "Un diff de cientos de líneas llega a review y nadie lo puede revisar",
    "causa_raiz": "no hay budget de spec ni checkpoint de review antes de seguir escribiendo",
    "mitigacion": "budget max_files/max_loc/max_new_files; gate de write al exceder; review por spec antes de REPORT_GATE",
    "deteccion": "budget_exceeded, oversized_diff, review_coverage"
  },
  {
    "fallo": "El agente declara completed y dos sesiones después el problema reaparece",
    "causa_raiz": "completed exige artefactos pero no que los predicados hayan corrido sobre el commit actual; recursión no se detecta",
    "mitigacion": "verification_manifest con commit/revisión; completion invariant; close revalida; recurrencia reabre con regression_predicate",
    "deteccion": "false_done, stale_verification, reopened_spec, recurrence_detected"
  },
  {
    "fallo": "El harness bloquea sin remedio y el agente aprende a rodearlo",
    "causa_raiz": "gates nuevos sin comando accionable o con falsos positivos",
    "mitigacion": "cada deny incluye remedio exacto; F0 mide antes de F2; umbrales configurables y visibles",
    "deteccion": "gate.decision con rule/effect/remedy; denials sin remedio = 0"
  }
]
```

## Métricas de calidad por fase

### F0 — Observabilidad y contrato mínimo

- [ ] M-01 El contrato se escribe en el mismo save que la spec y nunca queda a
      medias — anti-patrón a evitar: **Transaction Script**.
- [ ] M-02 `brief`/`preflight` proyectan `contract`, `reuse`, `verification` y
      `recurrence` desde una sola fuente — anti-patrón a evitar: **Duplicate
      Code / Shotgun Surgery**.
- [ ] M-03 El schema del contrato es versionado, idempotente y tolera specs
      viejas sin contrato — anti-patrón a evitar: **Anemic Domain Model**.

### F1 — Contratos en el workflow (advisory)

- [ ] M-02 `reuse_decision` sale de CKG/Delta/CPG y no de una heurística local
      — anti-patrón a evitar: **Magic Numbers**.
- [ ] M-03 Cada criterio se mapea a un predicado o queda en `unmapped` visible
      — anti-patrón a evitar: **Boolean Trap**.
- [ ] M-05 El manifest se ata a `commit_hash`/`revision` y detecta stale —
      anti-patrón a evitar: **Hidden Dependency**.

### F2 — Gates duros del harness

- [ ] M-04 Cada decisión emite `gate.decision` con `rule_id`, `effect` y
      remedio; el deny nunca es silencioso — anti-patrón a evitar: **Eventual
      Consistency sin safeguards**.
- [ ] M-05 Los gates de reuse/budget/done son deterministas, testeables y no
      mezclan políticas — anti-patrón a evitar: **God Class**.
- [ ] M-01 La evidencia del gate se persiste post-commit y no dentro de la
      transacción — anti-patrón a evitar: **Hidden Dependency**.

### F3 — Aprendizaje (similitud y recurrencia)

- [ ] M-02 Una sola política de similitud/umbral, configurable y compartida —
      anti-patrón a evitar: **Magic Numbers**.
- [ ] M-03 La recurrencia es un edge idempotente con `reopen_reason` y
      `regression_predicate` — anti-patrón a evitar: **Anemic Domain Model**.
- [ ] M-06 El detector de clones no está acoplado a un lenguaje/índice rígido —
      anti-patrón a evitar: **Not Invented Here**.

## Checklist de segundo orden

1. **Módulos que importan esto:** `memories/handlers/knowledge` (brief,
   preflight, session_context), `tasks/handlers` (spec_derive, spec_predicates,
   verification, spec_status), `delta_tracking` (SpecValidator, completion,
   close), `workflow` (prompt_context, orchestrator, nodos), `composition`,
   `code_analysis` (similitud/clones) y `agent-plugins/walker-flight-control`.
2. **Side effects:** el contrato se persiste con la spec; los gates de harness
   emiten Trace; no hay escrituras ocultas ni dobles writes.
3. **Eventos de dominio:** `reuse_scan`, `reuse_decision`, `goal_anchor`,
   `drift_detected`, `budget_exceeded`, `verification_manifest`,
   `stale_verification`, `recurrence_detected`, `gate.decision`.
4. **Tests que dependen:** brief/preflight, prompt_context, spec_status,
   verify_predicates, close de sprint, flight-control y los nuevos BDD de
   WorkContract.
5. **Cambios en MCP/CLI/CKG/Delta/Trace:** `brief` agrega proyecciones;
   `capture`/`spec_status` escriben contrato; Delta no cambia de esquema (solo
   `task_context`); CKG gana nodos de reuso/fallo; Trace gana eventos.

## Alternativas rechazadas

- **Cuatro tools nuevas (`reuse_scan`, `goal_guard`, `budget_check`,
  `done_verify`).** Rechazada: duplica el mecanismo y crea cuatro políticas que
  pueden divergir; viola ADR-084 (un solo lector) y reproduce el problema 1.
- **Confiar en que el agente declare “ya revisé”.** Rechazada: el problema 4
  demuestra que la declaración no es evidencia.
- **Bloquear todo desde el día 1.** Rechazada: sin métricas, los gates generan
  falsos positivos y el agente aprende a rodearlos (ADR-084).
- **Cerrar por tests verdes solamente.** Rechazada: un test verde no cubre
  criterios no mapeados ni código cambiado después de verificar.
- **Un sprint = un diff gigante.** Rechazada: la unidad revisable es la spec.

## Consecuencias

- El agente dedica más tiempo a buscar/mapear antes de escribir; el costo se
  paga en el diseño, no en la review.
- El `brief` crece unos cientos de caracteres; se mantiene acotado y se corta
  por prioridad (reuse/goal/criteria primero).
- Los sprints con specs viejas siguen funcionando: el contrato es opcional en
  lectura y se completa al reabrir/actualizar.
- Un falso `completed` deja de ser un estado silencioso: queda como
  `verification_stale` o `false_done` y bloquea el cierre.
- El harness se vuelve más estricto; cada deny debe incluir remedio para no
  repetir la fricción medida en ADR-084.

## Estado de implementación

No implementado. La Fase 0 está especificada en
[SPEC-WALKER-045](SPEC-WALKER-045-work-contract-agent-controls.md). Este ADR no
se considera aprobado hasta que el usuario confirme el alcance y el spec pase
su checklist de aceptación.
