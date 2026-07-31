# Workflow Guide for Agents

> Solo walker. Sin spring.py, sin task.py, sin comandos internos.
> El agente se mueve entre estados del workflow y el nodo le dice qué necesita.

---

## Cómo funciona

```
1. El agente está en un nodo/sub-estado del workflow
2. El nodo le dice en qué modo está (auto/manual) y qué campos requiere
3. Si mode=auto → el motor ejecuta solo, el agente solo espera
4. Si mode=manual → el agente provee datos vía submit y avanza
5. Si el nodo se completa → el agente usa transition para ir al siguiente
```

> 📚 **Lectura recomendada:** [CPG-CKG-ARCHITECTURE.md](CPG-CKG-ARCHITECTURE.md) — documentación completa de los sistemas de grafos.

## Comandos del agente

| Comando | Para qué |
|---------|----------|
| `walker status [chain_id]` | Ver dónde estoy y qué necesito |
| `walker current [chain_id]` | Ver sub-estado actual |
| `walker submit [chain_id] --key value ...` | Enviar datos que el nodo requiere |
| `walker transition <gate> [chain_id]` | Avanzar al siguiente nodo |
| `walker ckg-query [chain_id] <query>` | Buscar en el CKG: `type:decision`, `scope:TASK-013`, `neighbors:ComponentX` |

> **Nota:** `<chain_id>` puede ser el nombre corto del sprint (ej: `workflow-engine-v2-20260723`) o el path completo. El walker resuelve automáticamente el chain_id escaneando todos los proyectos. Usa `proyecto:chain_id` si hay ambigüedad.

## Transiciones disponibles

```
SPRINT ────────── start_task ──────→ ANALYZE
SPRINT ────────── edit_plan ───────→ SPRINT.EDIT
SPRINT ────────── close_sprint ────→ SPRINT.CLOSE_CHECK
SPRINT ────────── back_to_active ──→ SPRINT.ACTIVE
SPRINT ────────── start_planning ──→ SPRINT_PLANNING
SPRINT ────────── plan_tasks ──────→ SPRINT_PLANNING.ADD_TASKS
ANALYZE ───────── analysis_approved → IMPLEMENT
ANALYZE ───────── needs_refine ────→ REFINE
IMPLEMENT ─────── implementation_done → VERIFY
IMPLEMENT ─────── needs_refine ────→ REFINE
IMPLEMENT ─────── cancel ──────────→ SPRINT.ACTIVE
VERIFY ────────── task_complete ───→ SPRINT.ACTIVE
VERIFY ────────── needs_refine ────→ REFINE
REFINE ────────── reroute_analyze ─→ ANALYZE
REFINE ────────── reroute_implement → IMPLEMENT
REFINE ────────── reroute_verify ──→ VERIFY
REFINE ────────── reroute_sprint ──→ SPRINT.ACTIVE
SPRINT_PLANNING ─ plan_complete ───→ SPRINT.ACTIVE
SPRINT_PLANNING ─ cancel_planning ─→ SPRINT.INIT
```

## Nodos y sub-estados

### SPRINT — Ciclo de vida del sprint

| Sub-estado | Modo | Qué necesita el agente |
|------------|------|----------------------|
| INIT | manual | Nada específico. Usar `transition start_planning` o `transition start_task` |
| CHECK | auto | Nada — el motor chequea sprints disponibles |
| CREATE | manual | `submit --project_id <id> --goal "<goal>" --tasks '<json>'` |
| ACTIVE | auto | Nada — el motor muestra dashboard. Usar `transition start_task` |
| EDIT | manual | `submit --action add-task --objective "..." --type feature --domain engine --result_state "..."` |
| CLOSE_CHECK | auto | Nada — el motor verifica condiciones de cierre |
| HANDLE_PENDING | manual | `submit --decision backlog|cancel_pending|defer` |
| CLOSE | auto | Nada — el motor cierra el sprint |

### ANALYZE — Fase de análisis

| Sub-estado | Modo | Qué necesita el agente |
|------------|------|----------------------|
| GATHER | auto | Nada — el motor recopila contexto |
| ANALYZE | manual | `submit --q1 "..." --a1 "..." --q2 "..." --a2 "..." --q3 "..." --a3 "..."` |
| REVIEW | manual | `submit --decision approve --evidence "..."` o `--decision reject --notes "..."` |
| KNOWLEDGE_CAPTURE | manual | `submit --artefactos '[]' --reglas '[]' --decisiones '[]' --advertencias '[]'` |

### IMPLEMENT — Fase de implementación

| Sub-estado | Modo | Qué necesita el agente |
|------------|------|----------------------|
| USER_GATE | manual | `submit --decision proceed|refine|cancel` |
| WORK | auto | Nada — el motor implementa la tarea |
| CODE_REVIEW_SCAN | auto | **Evalúa quality gates.** Si fallan, `on_failure_loop` → WORK para corregir. El agente solo espera. |
| KNOWLEDGE_UPDATE | manual | `submit --actualizaciones '[]' --nuevas_reglas '[]'` |
| REPORT_GATE | manual | `submit --decision proceed|refine|cancel` |

#### Quality Gates en CODE_REVIEW_SCAN

El scan evalúa 2 capas:
- **A) Umbrales globales** (env vars: `DELTA_QLTY_MAX_ERRORS=0`, `DELTA_QLTY_MAX_WARNINGS=10`, etc.)
- **B) Quality gates de la tarea** desde `plan.json` → `quality_gates[]` (`"<path> pasa lint"`, `"No security issues"`, etc.)

Si alguna falla → `exit_code=1` → loop a WORK. El ciclo se repite hasta que el código pasa todos los gates.

### VERIFY — Fase de verificación

| Sub-estado | Modo | Qué necesita el agente |
|------------|------|----------------------|
| QA_RUN | auto | Nada — el motor ejecuta tests |
| QA | manual | `submit --decision approve --evidence "..."` o `--decision reject --notes "..."` |
| PO_ACCEPT | manual | `submit --decision accept --feedback "..."` o `--decision reject --feedback "..."` |

### REFINE — Refinamiento

| Sub-estado | Modo | Qué necesita el agente |
|------------|------|----------------------|
| DIAGNOSE | manual | `submit --target analyze|implement|verify|sprint --reason "..."` |
| ROUTE | auto | Nada — el motor redirige al nodo correcto |

### SPRINT_PLANNING — Planificación

| Sub-estado | Modo | Qué necesita el agente |
|------------|------|----------------------|
| GOAL | manual | `submit --goal "..." --expected_result "..." --roles '{}'` |
| ADD_TASKS | manual | `submit --task '{"objective":"...","type":"feature","domain":"engine","result_state":"..."}'` |
| VALIDATE | auto | Nada — el motor valida el plan |
| REVIEW | manual | `submit --decision approve` o `--decision revise --notes "..."` |

## Flujo típico

### Crear sprint nuevo
```bash
walker transition start_planning <chain>
# → SPRINT_PLANNING.GOAL
submit --goal "Mi sprint" --expected_result "..." --roles '{"dev":1}'
# → SPRINT_PLANNING.ADD_TASKS
submit --task '{"objective":"Hacer X","type":"feature","domain":"engine","result_state":"..."}'
# → sigue en ADD_TASKS para mas tareas
submit --done true
# → SPRINT_PLANNING.VALIDATE (auto) → SPRINT_PLANNING.REVIEW
submit --decision approve
# → plan_complete → SPRINT.ACTIVE
```

### Trabajar una tarea existente
```bash
walker status <chain_id>
# → SPRINT.ACTIVE — muestra tareas pendientes
walker transition start_task <chain_id>
# → ANALYZE.GATHER (auto) → ANALYZE.ANALYZE
submit --q1 "..." --a1 "..." --q2 "..." --a2 "..." --q3 "..." --a3 "..."
# → ANALYZE.REVIEW
submit --decision approve --evidence "..."
# → ANALYZE.KNOWLEDGE_CAPTURE
submit --artefactos '[]' --reglas '[]' --decisiones '[]' --advertencias '[]'
# → analysis_approved → IMPLEMENT.USER_GATE
submit --decision proceed
# → IMPLEMENT.WORK (auto) → CODE_REVIEW_SCAN (auto)

# Si quality gates fallan → loop a WORK automaticamente:
#   CODE_REVIEW_SCAN exit_code=1 → on_failure_loop → WORK
#   WORK corrige codigo → CODE_REVIEW_SCAN nuevamente
# El loop se repite hasta pasar los gates → KNOWLEDGE_UPDATE

submit --actualizaciones '[]' --nuevas_reglas '[]'
# → REPORT_GATE
submit --decision proceed
# → implementation_done → VERIFY.QA_RUN (auto) → QA
submit --decision approve --evidence "15/15 tests OK"
# → PO_ACCEPT
submit --decision accept --feedback "TODO OK"
# → task_complete → SPRINT.ACTIVE
```

### Agregar tareas a un sprint activo
```bash
walker transition plan_tasks <chain_id>
# → SPRINT_PLANNING.ADD_TASKS
submit --task '{"objective":"Nueva tarea","type":"feature","domain":"engine","result_state":"..."}'
# → sigue en ADD_TASKS
submit --done true
# → VALIDATE (auto) → REVIEW
submit --decision approve
# → plan_complete → SPRINT.ACTIVE
```

## Reglas

1. **Solo walker** — no uses spring.py, task.py ni otros scripts directamente
2. **El nodo te dice qué necesita** — si mode=manual, `walker status` muestra los required_fields
3. **submit solo cuando el nodo pide datos** — si mode=auto, el motor ejecuta solo
4. **transition cambia de nodo** — cuando el nodo actual está completo, usa `transition <gate>` para avanzar
5. **Siempre verifica con `status`** — antes de actuar, corre `walker status <chain_id>` para saber dónde estás
6. **Chain_id simplificado** — puedes usar el nombre del sprint en vez del path completo. El walker lo resuelve automáticamente. Ej: `walker status workflow-engine-v2` en vez de `walker status .cognitive/projects/delta-skill/delta/workflow-engine-v2-20260723`
7. **CODE_REVIEW_SCAN puede loopear** — si el código no pasa los quality gates, el scan retorna exit_code=1 y el workflow vuelve a WORK automáticamente para corrección. No intentes avanzar manualmente durante este loop.
8. **Consulta el CKG antes de implementar** — siempre ejecuta `ckg-query "type:convention scope:<modulo>"` y `ckg-query "type:decision scope:<modulo>"` antes de tocar código nuevo. Esto te da el contexto de decisiones y reglas sin leer archivos.
9. **Documenta en el CKG** — si descubres una regla, decisión o advertencia durante la implementación, documéntala en el CKG para que otros agentes aprendan de tu trabajo.
