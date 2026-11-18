# Roadmap — integración walker ↔ harness ↔ agente por etapa del proceso

- **Estado:** propuesto
- **Base:** ADR-077..082, registro de sesiones, plan de vuelo, gates, métricas de
  adherencia, harness de 15 modos.

## Principio de reparto

| Actor | Rol | No hace |
| :--- | :--- | :--- |
| **Walker** | sistema de registro y conocimiento: sesiones, plan, specs, decisiones (CKG), trazas, verificación (tests/predicados), workflow de fases | no decide por el agente ni inyecta texto masivo |
| **Harness (OpenCode + plugin)** | ciclo de sesión, hooks, gates en los bordes, inyección del bloque corto, métricas | no reemplaza el juicio del agente |
| **Agente** | produce los artefactos (plan, hipótesis, cambios, verificación), usa las tools | no declara terminado sin evidencia |

Regla: **el harness hace cumplir los bordes; walker provee los hechos; el agente
produce el juicio.** Lo que no se puede medir (trace/métrica) no entra.

## Etapas y contrato

| Etapa | Walker aporta | Harness exige/inyecta | Agente produce | Aceptación |
| :--- | :--- | :--- | :--- | :--- |
| 0 Intake | proyecto, sprint, sesión | gate: sesión ligada + tarea declarada | declara objetivo y archivos | `implement_task` con `objective` y `files` |
| 1 Observar | `preflight` (impacto, tests, complejidad), `agent_context` (decisiones), última sesión | bloque del plan con baseline y decisión | lee y cita el impacto | `baseline_injected` antes de la primera mutación |
| 2 Analizar | CKG de decisiones; spec derivada | plan con `hypotheses`/`evidence` | hipótesis y evidencia | ≥1 hipótesis registrada antes de escribir |
| 3 Diseñar | `acceptance` derivada; scope | gate: criterios y alcance no vacíos | plan aprobado por el usuario (no trivial) | `acceptance` no vacío y `scope` no vacío |
| 4 Implementar | specs, delta | gate de alcance (edit/write/shell) | cambios dentro del scope | `scope_denials` acotado; `first_mutation.in_scope` |
| 5 Verificar | `run_tests`, `verify_predicates` (verde/rojo) | gate de cierre: verde obligatorio | corre y corrige | `verified` nivel `tools`, `verify_failed=false` |
| 6 Cerrar | `capture`, `documenting_knowledge`, `session_context` | emite métricas y cierra/reabre sesión | documenta decisiones y cierre | nodo CKG ligado a sesión/spec; métricas completas |
| 7 Continuar | última sesión, sprint, plan, tareas abiertas | bloque de binding con el estado previo | decide continuar o nuevo goal | `session_bind=resume_available` con goal y `next` |

## Backlog

### P0 — Base confiable (prerrequisito de todo)

- **T0.1** Registro de sesiones con ciclo de vida y jerarquía. **Hecho** (ADR-080,
  `session_bind/open/close/list`, `parent_session_id`).
- **T0.2** Plan de vuelo persistido (`goal`, `acceptance`, `task`, `scope`,
  `next`, `project`). **Hecho** (ADR-080/082).
- **T0.3** Gates de entrada y alcance con fail-open. **Hecho** (ADR-081).
- **T0.4** Baseline de riesgo y decisión del CKG en el bloque (≤400 chars,
  estable). **Hecho** (ADR-082).
- **T0.5** Estado por sesión persistente entre ejecuciones y métricas
  (`first_mutation`, `scope_denials`, `writes`, `verified`). **Hecho**.
- **T0.6** Sesión reabrible (`closed` = inactividad) y tests multi-ejecución.
  **Hecho**.
- **T0.7** Evicción de sesiones cerradas del `Map` (memoria). **Pendiente**.
- **T0.8** Cierre solo con verificación **verde** (`run_tests` ejecuta y
  devuelve pass/fail; el gate no acepta “corrí algo”). **Pendiente**: wire
  `verified` a verde y bloquear cierre en rojo con el tail del fallo.
- **T0.9** Orden “plan antes de la primera escritura”. **Decisión pendiente**:
  o se acepta que el baseline informa pasos siguientes, o el gate exige que la
  declaración ocurra en un turno anterior y bloquea la primera escritura.
- **T0.10** Evicción/memoria y debounce de cierre por inactividad (opcional).

### P1 — Proceso (la brecha actual)

- **T1.1** **Observar automático**: al declarar tarea, inyectar en el bloque
  impacto (ya), **tests que lo cubren** (nombre + ruta) y **última sesión/plan**
  relacionada. Aceptación: el bloque contiene ≥1 test y el `next` de la sesión
  anterior; `baseline_files` no vacío.
- **T1.2** **Handoff de reanudación**: al hacer `session_bind=continue`, el
  bloque muestra goal, tareas abiertas y `next` de la sesión previa.
- **T2.1** **Artefactos de análisis en el plan**: `hypotheses[]` y `evidence[]`;
  el bloque muestra el conteo y el agente los completa antes de escribir.
  Aceptación: una tarea de bugfix no puede pasar a implementar sin ≥1 hipótesis
  (gate opt-in medido).
- **T2.2** **Plantilla de fase como `next`**: para bugfix/refactor, el `next`
  sugiere observar→hipótesis→cambio→verificar (usar los prompts de
  `refine/analyze` como texto corto, no el nodo).
- **T3.1** **Criterios obligatorios**: si `acceptance` queda vacío tras derivar,
  el gate de diseño pide definirlos (o bloquea la implementación no trivial).
- **T3.2** **Alcance obligatorio**: `implement_task` sin `files` en tarea de
  implementación se rechaza con el comando para declararlos.
- **T3.3** **Aprobación del plan** (no trivial): el agente pide confirmación y
  registra `approved_by_user` en el plan; el gate de alcance usa ese plan.
- **T4.1** **Desvío por shell**: extraer el path del redirect y compararlo con el
  scope (hoy se registra `firstMutation` pero no se bloquea).
- **T5.1** **Cierre verde**: `run_tests`/`verify_predicates` con resultado;
  nudge con `output_tail` y archivo; bloquear “terminado” en rojo.
- **T5.2** **Verificación ligada a criterios**: `verify_predicates` lee los
  predicados de la spec (ya existe) y el bloque muestra cuántos verificó.
- **T6.1** **Captura al cierre**: correr `capture` (hecho de sesión) y
  `documenting_knowledge` para las decisiones; ligar al `session_id`/spec.
- **T6.2** **Resumen de cierre** en `session_context`: objetivo, archivos,
  tests, decisiones, `next`; insumo de T1.2.
- **T6.3** **Métricas persistidas** en la sesión (no solo en trace), para
  consultarlas sin escanear el trace.
- **T7.1** **Jerarquía visible**: `session_list` con `children`/`tree` (hecho) y
  el binding mostrando el árbol.

### P2 — Conocimiento

- **T8.1** Convención `aplica a: <path>` obligatoria en decisiones (hecho para
  ADR-082; hacerlo regla de `documenting_knowledge`). Aceptación: una decisión
  sin `aplica a` se guarda pero no se cita en el bloque.
- **T8.2** `knowledge_query` al inicio para convenciones del área (además de
  `agent_context`).
- **T8.3** Capturar resultados de verificación como evidencia en el CKG.

### P3 — Medición y optimización

- **T9.1** Re-medir adherencia con métricas confiables (hecho el instrumento).
- **T9.2** Experimento de dos brazos (gates on/off) con la regla de decisión
  pre-registrada (`docs/EXPERIMENT-flight-control.md`).
- **T9.3** Costo por tarea verificada con modelo de pago (el free reporta 0).
- **T9.4** Monitoreo del presupuesto de atención (chars del bloque, inyecciones).

## Definición de terminado (integración)

1. Por cada etapa, el agente usa al menos una familia de API de walker
   (observar: preflight/agent_context; analizar: plan; diseñar: plan/spec;
   implementar: implement_task/scope; verificar: run_tests/verify_predicates;
   cerrar: capture/documenting_knowledge; continuar: session_bind).
2. `VCR` (cierre verificado) medido en ≥10 tareas, con `after_baseline` y
   `in_baseline` como diagnóstico.
3. El bloque de contexto nunca supera 400 chars y no se repite sin cambio.
4. Ningún fallo de walker bloquea la sesión (fail-open) y ningún id desconocido
   cruza de proyecto.

## Orden recomendado

P0.7 → P0.8 → P1.1/T1.2 → T2.1/T3.1 → T5.1 → T6.1/T6.2 → medir (P3) → decidir
T0.9 y T3.3 con datos.
