---
description: >
  Cognitive GPS engine — models system transformation from current state
  (G_actual) to desired state (G_objetivo). Orchestration layer.
name: delta
allowed-tools: Read Grep Glob Write Bash Edit
allowed-commands: >
  Run ONLY python3 .claude/skills/delta/scripts/walker.py.
  Do NOT run spring.py, task.py, or any other script.
  The workflow engine calls them automatically.
when_to_use: >
  User asks "what should I do now", "how is it going", "what's missing",
  "what's the next step", "is this feature ready", "what risks are there",
  or wants to plan, evaluate progress, or decide between alternatives.
---

# Delta Skill — Workflow Graph Engine (v3.1)

> **Rule #1:** Only `walker.py`. Never spring.py, task.py, or internal scripts.
> **Rule #2:** The node tells you what to do. The engine loads an instruction prompt (from `prompts/` via `prompt_id`) and shows it to you. Read it carefully — that is your instruction. You can interpret the prompt and ask clarifying questions if the context requires it.
> **Rule #3:** `submit` is your only data command. Fill the fields the node asks for, the engine advances.
> **Rule #4:** `transition` changes nodes when the current one is complete.
> **Rule #5:** The walker manages all state files. You only interact through walker commands.
> **Rule #6:** Use context tasks instead of direct CPG/CKG/delta-spring. Invoke them by name via `walker task <nombre>`.
> **Rule #7:** You DO NOT know CPG, CKG, or delta-spring exist. You only know context tasks. The engine routes them internally.
> **Rule #8:** During refinement sessions, identify design decisions as they are made and capture them as `decision` nodes in CKG via `walker task knowledge:capture-decision`. Each decision must have: type=decision, label="ADR: \<title\>", description (rationale), metadata with task_id, source, scope, severity.
>
> 📚 **Architecture docs:** `wiki/docs/CPG-CKG-ARCHITECTURE.md`

---

## Session lifecycle

### First time (`walker start`)

```
walker start
  │
  ├── walker.py checkea: ¿existe sesión activa?
  │     ├── Sí → carga session_id → walker step
  │     └── No → walker step sin session_id
  │
  ├── Engine recibe step():
  │     ├── Sin session_id → auto-genera sess-{uuid}
  │     └── Con session_id → valida vs SessionStore
  │           ├── Válida → devuelve estado actual
  │           └── Inválida → error + reinicia
  │
  └── Engine ejecuta SPRINT.INIT (bootstrap):
        ├── assign_session → crea session_id + persiste
        ├── detect_context → busca proyectos en .cognitive/projects/
        ├── register_catalog → tareas namespaced
        ├── init_trace_log
        └── emite system_initialized
```

**Importante:** `session_id` se valida a nivel de engine, no de sub-estado.
El INIT recibe el session_id ya asignado — no necesita pedirlo al LLM.

### Commands

```
walker status <chain_id>              ← Where am I?
walker submit <chain_id> [--k v ...]  ← Send data to current sub-state
walker transition <gate> <chain_id>   ← Change nodes (inter-node)

# Context tasks (via TaskRegistry, when available)
walker task <nombre> [--param value]  ← Execute namespaced context task
```

---

## Context tasks (namespaced)

The LLM **never invokes** CPG, CKG, or delta-spring directly. Instead, use these tasks registered in the TaskRegistry. The engine routes them to the appropriate service internally.

### Code context

| Task | Qué hace | Ejemplo |
|------|----------|---------|
| `walker task code_context:query --query "function:validate"` | Buscar símbolos, funciones, clases, archivos | `function:submit`, `class:DeltaGraph`, `file:engine.py` |
| `walker task code_context:read --file "scripts/engine.py"` | Leer structure de un archivo (firmas, imports, calls, métricas) | Sin --verbose: solo firmas. Con --verbose: incluye bodies cortos |
| `walker task code_context:query --query "bug:dead_branches"` | Análisis de bugs (código muerto, missing returns, infinite loops) | `bug:taint_paths file:state.py` |
| `walker task code_context:query --query "security:all"` | Análisis de seguridad (SQLi, XSS, hardcoded secrets, command injection) | `security:sqli`, `security:data_leak` |
| `walker task code_context:query --query "trace:funcA->funcB"` | Traza de call chain entre funciones | `trace:cmd_status->complete_task` |

### Knowledge context

| Task | Qué hace | Ejemplo |
|------|----------|---------|
| `walker task knowledge:search --query "type:decision scope:workflow"` | Buscar decisiones, patrones, convenciones en el CKG | `type:convention scope:engine` |
| `walker task knowledge:briefing --id "TASK-013"` | Briefing completo de una tarea (convenciones + decisiones + warnings) | `--id TASK-005` |
| `walker task knowledge:capture-decision --label "ADR: ..." --description "..." --scope "workflow-engine" --severity "obligatoria"` | Capturar una decision de diseno en CKG durante refinamiento | `--label "ADR: Prompt externos" --description "..." --scope project --severity recomendada` |

### Spring context (sprints anteriores)

| Task | Qué hace | Ejemplo |
|------|----------|---------|
| `walker task spring:search --query "refine loop detection"` | Buscar tareas similares en sprints anteriores | Busca en el historial de sprints anteriores del proyecto |
| `walker task spring:status --chain "cognitive-mcp-scrum-migration"` | Estado de un sprint anterior | Muestra health, tareas completadas, pendientes y métricas |

### File system

| Task | Qué hace | Ejemplo |
|------|----------|---------|
| `walker task file:search --pattern "**/engine.py"` | Buscar archivos por glob pattern | `--pattern "scripts/**/*.py"` |

**Rule:** If you need code context, do NOT use grep/read directly. Use `walker task code_context:*` instead.

---

## Workflow nodes

### SPRINT — Sprint lifecycle (Scrum adaptado a LLM)

```
PLANNING → ACTIVE → REVIEW → RETRO → CLOSE
```

| Sub-state | Execution | Agent action |
|-----------|-----------|--------------|
| **PLANNING** | llm_task | `submit --goal "<goal>" --expected_result "<result>" --tasks '<json>'` — define Sprint Goal + backlog. Colabora con el usuario (PO). |
| **ACTIVE** | auto | Engine muestra dashboard. Espera transición del usuario para trabajar tareas o cerrar. |
| **REVIEW** | user_input | `submit --decision accept\|needs_work --feedback "<razón>"` — usuario revisa el incremento. |
| **RETRO** | llm_task | `submit --lecciones '[]' --reglas '[]' --acciones '[]'` — captura aprendizajes en CKG. |
| **CLOSE** | auto | Engine cierra y archiva el sprint. |

### DESIGN — Design phase (nuevo en v3.1)

```
DESIGN_GATHER → DESIGN_ANALYZE → DESIGN_REVIEW → CKG_SAVE
```

| Sub-state | Execution | Agent action |
|-----------|-----------|--------------|
| **DESIGN_GATHER** | auto | Engine recopila contexto (código, conocimiento, sprints). Side-effects: update_code_index, build_project_graph. |
| **DESIGN_ANALYZE** | llm_task | `submit --q1 "..." --a1 "..." --q2 "..." --a2 "..." --q3 "..." --a3 "..."` — análisis 6→3 capas |
| **DESIGN_REVIEW** | user_input | `submit --decision approve\|reject\|refine --evidence "..."` |
| **CKG_SAVE** | auto | Engine guarda en CKG. Side-effects: save_ckg, update_delta_graph. |

### ANALYZE — Task analysis

```
GATHER → ANALYZE → REVIEW → KNOWLEDGE_CAPTURE
```

| Sub-state | Execution | Agent action |
|-----------|-----------|--------------|
| **GATHER** | auto | Engine recopila contexto de la tarea |
| **ANALYZE** | llm_task | `submit --q1 "..." --a1 "..." --q2 "..." --a2 "..." --q3 "..." --a3 "..."` |
| **REVIEW** | user_input | `submit --decision approve --evidence "..."` o `--decision reject --notes "..."` |
| **KNOWLEDGE_CAPTURE** | llm_task | `submit --artefactos '[]' --reglas '[]' --decisiones '[]' --advertencias '[]'` |

### IMPLEMENT — Implementation

```
USER_GATE → WORK → CODE_REVIEW_SCAN ⇄ WORK → KNOWLEDGE_UPDATE → REPORT_GATE
```

| Sub-state | Execution | Agent action |
|-----------|-----------|--------------|
| **USER_GATE** | user_input | Presentar reporte. `submit --decision proceed\|refine\|cancel` |
| **WORK** | auto | Engine implementa la tarea |
| **CODE_REVIEW_SCAN** | auto | Engine ejecuta análisis estático + quality gates. Si fallan → loop a WORK. |
| **KNOWLEDGE_UPDATE** | llm_task | `submit --actualizaciones '[]' --nuevas_reglas '[]' --nuevas_advertencias '[]'` |
| **REPORT_GATE** | user_input | `submit --decision proceed\|refine\|cancel` |

#### Quality Gates (CODE_REVIEW_SCAN)

**A) Umbrales globales:**

| Variable | Default | Gate |
|---|---|---|
| `DELTA_QLTY_MAX_ERRORS` | 0 | Errores de linter |
| `DELTA_QLTY_MAX_WARNINGS` | 10 | Warnings de linter |
| `DELTA_QLTY_MAX_SECURITY_HIGH` | 0 | Issues de seguridad HIGH |
| `DELTA_QLTY_MAX_TOTAL_ISSUES` | 30 | Issues totales |
| `DELTA_QLTY_MAX_SMELLS` | 5 | Code smells |

**B) Quality gates de la tarea** (desde `plan.json` → `quality_gates[]`):
- `<path> pasa lint` — 0 errores en ese archivo
- `No security issues` — zero security HIGH
- `Max <N> warnings` — warnings dentro del límite
- `tests/test_*.py pasa` — informativo (se verifica en QA_RUN)

Si alguna falla → `exit_code=1` → loop a WORK.

### VERIFY — Verification

```
QA_RUN → QA → PO_ACCEPT
```

| Sub-state | Execution | Agent action |
|-----------|-----------|--------------|
| **QA_RUN** | auto | Engine ejecuta tests |
| **QA** | user_input | `submit --decision approve --evidence "..."` o `--decision reject --notes "..."` |
| **PO_ACCEPT** | user_input | `submit --decision accept --feedback "..."` o `--decision reject --feedback "..."` |

### REFINE — Refinement cycle (v3.1 con loop detection)

```
DIAGNOSE → KNOWLEDGE_CAPTURE → ROUTE
   │
   └── (si attempt ≥ 3) → ESCALATION (auto → SPRINT)
```

| Sub-state | Execution | Agent action |
|-----------|-----------|--------------|
| **DIAGNOSE** | llm_task | El engine inyecta: source_node, attempt, error_context. El LLM diagnostica usando context tasks, determina target (analyze\|implement\|verify\|sprint) y reason. |
| **KNOWLEDGE_CAPTURE** | llm_task | `submit --lecciones '[]' --reglas '[]' --decisiones '[]' --advertencias '[]'` — capturar aprendizajes en CKG |
| **ROUTE** | auto | Engine rutea al nodo correcto según el target |
| **ESCALATION** | auto (si attempt≥3) | Engine escala al sprint con alerta de límite de refinamiento alcanzado |

**En DIAGNOSE** el prompt (prompts/refine/diagnose.prompt.json) instruye al LLM a usar context tasks para investigar la causa raíz, NO a invocar CPG/CKG/delta-spring directamente.

---

## Node transitions

```
SPRINT
  ├── transition start_task ──────→ ANALYZE.GATHER
  ├── transition start_design ────→ DESIGN.DESIGN_GATHER   ← opcional
  ├── transition edit_plan ───────→ SPRINT.PLANNING         ← volver a planificar
  ├── transition start_review ────→ SPRINT.REVIEW           ← iniciar review
  ├── transition back_to_active ──→ SPRINT.ACTIVE
  └── transition close_sprint ────→ SPRINT.CLOSE            ← cerrar directo

DESIGN
  ├── transition design_approved ──→ ANALYZE.GATHER
  └── transition design_needs_refine → REFINE.DIAGNOSE

ANALYZE
  ├── transition analysis_approved → IMPLEMENT.USER_GATE
  └── transition needs_refine ────→ REFINE.DIAGNOSE

IMPLEMENT
  ├── transition implementation_done → VERIFY.QA_RUN
  ├── transition needs_refine ──────→ REFINE.DIAGNOSE
  └── transition cancel ────────────→ SPRINT.ACTIVE

VERIFY
  ├── transition task_complete ───→ SPRINT.ACTIVE
  └── transition needs_refine ────→ REFINE.DIAGNOSE

REFINE
  ├── transition reroute_design ───→ DESIGN.DESIGN_GATHER
  ├── transition reroute_analyze ──→ ANALYZE.GATHER
  ├── transition reroute_implement → IMPLEMENT.WORK
  ├── transition reroute_verify ───→ VERIFY.QA_RUN
  ├── transition reroute_sprint ───→ SPRINT.ACTIVE
  └── transition escalation ───────→ SPRINT.ACTIVE
```

---

## Typical full flow

```bash
# 0. Start session (first time)
walker start
# → Engine bootstrap: asigna sesión, detecta contexto, registra catálogo
# → SPRINT.PLANNING: planificar primer sprint

# 1. Define sprint goal + tasks
walker submit --goal 'Implementar X' --expected_result 'Sistema funcional' \
              --tasks '[{"objective":"Hacer Y","type":"feature","domain":"engine"}]'
# → SPRINT.ACTIVE: dashboard

# 2. Start a task
walker transition start_task
# → ANALYZE.GATHER (auto) → ANALYZE.ANALYZE

# 3. 6→3 layer analysis
walker submit --q1 "..." --a1 "..." --q2 "..." --a2 "..." --q3 "..." --a3 "..."
# → ANALYZE.REVIEW

# 4. Review analysis
walker submit --decision approve --evidence "..."
# → ANALYZE.KNOWLEDGE_CAPTURE

# 5. Capture knowledge
walker submit --artefactos '[...]' --reglas '[...]'
# → analysis_approved → IMPLEMENT.USER_GATE

# 6. User gate
walker submit --decision proceed
# → IMPLEMENT.WORK (auto) → CODE_REVIEW_SCAN (auto)

# 6b. If quality gates fail → loop back to WORK
# CODE_REVIEW_SCAN exit_code=1 → on_failure_loop → WORK (auto)

# 7. Update knowledge
walker submit --actualizaciones '[...]' --nuevas_reglas '[...]'
# → REPORT_GATE

# 8. Report gate
walker submit --decision proceed
# → implementation_done → VERIFY.QA_RUN (auto) → QA

# 9. QA
walker submit --decision approve --evidence "..."
# → PO_ACCEPT

# 10. PO accept
walker submit --decision accept --feedback "..."
# → task_complete → SPRINT.ACTIVE (vuelve al dashboard)

# 11. Cuando todas las tareas están listas → Sprint Review
walker transition start_review
# → SPRINT.REVIEW

# 12. User reviews the sprint
walker submit --decision accept --feedback "Todo correcto"
# → SPRINT.RETRO

# 13. Retrospective
walker submit --lecciones '[{"leccion":"...","causa":"...","solucion":"..."}]'
# → SPRINT.CLOSE (auto) → sprint archivado

# 14. To edit the plan during the sprint:
walker transition edit_plan
# → SPRINT.PLANNING → modificar goal/tasks → submit → SPRINT.ACTIVE
```

---

## Using context tasks during analysis

En lugar de leer archivos directamente o recordar APIs de CPG/CKG, usa:

```bash
# Investigar código existente
walker task code_context:query --query "function:validate scope:engine"
walker task code_context:read --file "scripts/engine.py"

# Buscar conocimiento del proyecto
walker task knowledge:search --query "type:convention scope:workflow"
walker task knowledge:briefing --id "TASK-005"

# Buscar sprints anteriores
walker task spring:search --query "refine loop detection"
walker task file:search --pattern "**/*.py"
```

---

---

## Troubleshooting

```
Don't know where you are?           → walker status
Don't know what fields to use?      → walker status (shows required_fields)
Submit failed?                       → Error tells you the missing fields
Node complete?                       → walker status shows available transitions
Need context?                        → walker task code_context:query --query "..."
Stuck?                               → walker transition back_to_active
```
