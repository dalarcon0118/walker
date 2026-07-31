# Análisis y Especificación de Gaps de Contexto en Workflow v3

## Status

Implementado — Fase 1, 2 y 3 completadas (Jul 2026)

---

## Tabla de Contenidos

1. [Resumen Ejecutivo](#1-resumen-ejecutivo)
2. [Estado Actual: Cobertura por Nodo](#2-estado-actual-cobertura-por-nodo)
3. [Categorías de Gaps](#3-categorías-de-gaps)
4. [Fase 1 — Contexto para sub_estados user_input](#4-fase-1--contexto-para-sub_estados-user_input)
5. [Fase 2 — Completar contexto en sub_estados llm_task](#5-fase-2--completar-contexto-en-sub_estados-llm_task)
6. [Fase 3 — Nuevos ContextProviders](#6-fase-3--nuevos-contextproviders)
7. [Especificación Técnica de Nuevos Providers](#7-especificación-técnica-de-nuevos-providers)
8. [Plan de Implementación Priorizado](#8-plan-de-implementación-priorizado)
9. [Criterios de Verificación](#9-criterios-de-verificación)

---

## 1. Resumen Ejecutivo

El sistema de ContextComposer introdujo 7 ContextProviders (`project`, `task`, `state`, `ckg`, `cpg`, `trace`, `delta`) que permiten a cada sub_estado declarar qué capas de datos necesita. Sin embargo, la migración dejó **14 sub_estados con contexto incompleto o inexistente**:

- **10 sub_estados `user_input`** no declaran NINGÚN provider → el humano toma decisiones sin contexto.
- **4 sub_estados `llm_task`** tienen contexto parcial → faltan providers críticos para su función.
- **2 nodos enteros** (verify, project_init) casi no tienen contexto declarado.
- Existen oportunidades para **3 nuevos providers** que resolverían necesidades recurrentes.

Este documento detalla cada gap y especifica los cambios necesarios.

---

## 2. Estado Actual: Cobertura por Nodo

Providers existentes: `project`, `task`, `state`, `ckg`, `cpg`, `trace`, `delta`

### 2.1 analyze.node.json

| Sub_estado | Ejecución | Tiene | No tiene | Impacto |
|-----------|-----------|-------|----------|---------|
| GATHER | auto | — | — | ✅ Side effect, no necesita contexto |
| **ANALYZE** | llm_task | project, task, state, ckg, cpg, trace | delta | 🟡 El análisis 6->3 no ve tareas de sprints anteriores |
| **REVIEW** | user_input | **NINGUNO** | task, project, state | 🔴 El usuario no ve el análisis que aprueba/rechaza |
| **KNOWLEDGE_CAPTURE** | llm_task | project, task, state | ckg, cpg, trace | 🟡 El LLM no sabe qué conocimiento YA existe en CKG → puede duplicar; no ve estructura de código |
| **KNOWLEDGE_REVIEW** | user_input | **NINGUNO** | task, state, project | 🔴 El usuario no ve el conocimiento capturado |

### 2.2 design.node.json

| Sub_estado | Ejecución | Tiene | No tiene | Impacto |
|-----------|-----------|-------|----------|---------|
| DESIGN_GATHER | auto | — | — | ✅ Side effect, no necesita contexto |
| **DESIGN_ANALYZE** | llm_task | project, task, state, ckg, cpg | trace, delta | 🟡 El diseñador no ve historial de sesión ni tareas previas |
| **DESIGN_REVIEW** | user_input | **NINGUNO** | task, project, state | 🔴 El usuario no ve el diseño que evalúa |
| CKG_SAVE | auto | — | — | ✅ Engine action |

### 2.3 implement.node.json

| Sub_estado | Ejecución | Tiene | No tiene | Impacto |
|-----------|-----------|-------|----------|---------|
| **USER_GATE** | user_input | project, task, state | ckg, cpg, trace, delta | 🟡 El usuario ve briefing parcial — faltan patrones pasados, estructura de código, historial |
| WORK | auto | — | — | ✅ Engine action |
| CODE_REVIEW_SCAN | auto | — | — | ✅ Engine action |
| **CODE_REVIEW_RESULT** | user_input | **NINGUNO** | task, cpg, ckg, state, project | 🔴 El usuario no ve findings de code review ni qué código se revisó |
| **KNOWLEDGE_UPDATE** | llm_task | project, task, state | ckg, cpg | 🟡 No sabe qué conocimiento actualizar — puede duplicar o perder contexto |
| **REPORT_GATE** | user_input | **NINGUNO** | task, project, state | 🔴 El usuario no ve el reporte de implementación |

### 2.4 refine.node.json

| Sub_estado | Ejecución | Tiene | No tiene | Impacto |
|-----------|-----------|-------|----------|---------|
| **DIAGNOSE** | llm_task | project, task, cpg, trace | state, ckg, delta | 🟡 El diagnóstico no ve el estado del sprint, patrones CKG ni historial cross-sprint |
| **KNOWLEDGE_CAPTURE** | llm_task | project, task, state | ckg, trace | 🟡 No ve conocimiento existente en CKG ni el historial de la diagnosis |
| **CONFIRM_GATE** | user_input | **NINGUNO** | task, project, state, trace | 🔴 El usuario no ve el diagnóstico ni la ruta propuesta |
| ROUTE | auto | — | — | ✅ Engine action |
| ESCALATION | auto | — | — | ✅ Engine action |

### 2.5 sprint.node.json

| Sub_estado | Ejecución | Tiene | No tiene | Impacto |
|-----------|-----------|-------|----------|---------|
| PLANNING | llm_task | project, state, ckg, delta | trace, cpg | 🟡 No aprende de sprints anteriores ni conoce el codebase |
| **PLAN_APPROVAL** | user_input | **NINGUNO** | project, state, task | 🔴 El usuario no ve el plan del sprint |
| ACTIVE | auto | — | — | ✅ Dashboard |
| **REVIEW** | user_input | **NINGUNO** | project, state, delta | 🔴 El usuario no ve qué se logró en el sprint |
| RETRO | llm_task | project, state, ckg, delta | trace, cpg | 🟡 No ve historial de sesión ni cambios de código |
| **CLOSE_CONFIRM** | user_input | **NINGUNO** | project, state | 🔴 El usuario no ve resumen del sprint |
| CLOSE | auto | — | — | ✅ Engine action |

### 2.6 sprint_planning.node.json

| Sub_estado | Ejecución | Tiene | No tiene | Impacto |
|-----------|-----------|-------|----------|---------|
| GOAL | llm_task | project, state, ckg, delta | trace | 🟡 No aprende de retrospectivas anteriores |
| **ADD_TASKS** | llm_task | project, state, delta | ckg, cpg, trace | 🟡 No ve convenciones de proyecto ni estructura de código actual |
| VALIDATE | auto | — | — | ✅ Engine action |
| REVIEW | llm_task | project, state, ckg | delta, trace | 🟡 No ve historial cross-sprint ni retrospectivas pasadas |

### 2.7 verify.node.json

| Sub_estado | Ejecución | Tiene | No tiene | Impacto |
|-----------|-----------|-------|----------|---------|
| QA_RUN | auto | — | — | ✅ Engine action |
| PREDICATE_CHECK | auto | — | — | ✅ Engine action |
| QA | user_input | task, cpg, ckg | project, state, trace | 🟡 El revisor QA no ve el contexto del proyecto, estado del sprint, ni historial de tests |
| **PO_ACCEPT** | user_input | **NINGUNO** | task, project, state | 🔴 El PO no ve la tarea completada ni los criterios de aceptación |

### 2.8 project_init.node.json

| Sub_estado | Ejecución | Tiene | No tiene | Impacto |
|-----------|-----------|-------|----------|---------|
| DETECT | auto | — | — | ✅ |
| LEGACY_MAP | auto | — | — | ✅ |
| SCENARIO | user_input | — | — | ✅ OK — init temprano, no hay contexto todavía |
| GATHER_SPECS | llm_task | project, state | — | ✅ Ok para init |
| BUILD_PROJECT → FIRST_SPRINT | varios | — | — | ✅ Engine actions |

---

## 3. Categorías de Gaps

### 🔴 Gap Tipo A: user_input SIN contexto (10 occurrences)

El problema más grave. El humano recibe un prompt sin datos para decidir:

| # | Nodo | Sub_estado | Decisión | Necesita mínimo |
|---|------|-----------|----------|----------------|
| A1 | analyze | REVIEW | approve/reject analysis | task, state, project |
| A2 | analyze | KNOWLEDGE_REVIEW | approve/revise knowledge | task, state, project |
| A3 | design | DESIGN_REVIEW | approve/reject/refine design | task, state, project |
| A4 | implement | CODE_REVIEW_RESULT | approve/refine/cancel | task, cpg, ckg, state |
| A5 | implement | REPORT_GATE | proceed/refine/cancel | task, project, state |
| A6 | refine | CONFIRM_GATE | approve/modify/cancel | task, state, trace, project |
| A7 | sprint | PLAN_APPROVAL | approve/revise plan | project, state |
| A8 | sprint | REVIEW | accept/needs_work | project, state, delta |
| A9 | sprint | CLOSE_CONFIRM | close/cancel | project, state |
| A10 | verify | PO_ACCEPT | accept/reject task | task, project, state |

### 🟡 Gap Tipo B: llm_task con contexto parcial (7 occurrences)

El LLM tiene algunos providers pero le faltan capas críticas:

| # | Nodo | Sub_estado | Tiene | Le falta | Razón |
|---|------|-----------|-------|---------|-------|
| B1 | analyze | KNOWLEDGE_CAPTURE | project, task, state | ckg, cpg, trace | Sin CKG no sabe qué conocimiento ya existe → duplica |
| B2 | implement | KNOWLEDGE_UPDATE | project, task, state | ckg, cpg | Sin CKG no sabe qué actualizar |
| B3 | refine | DIAGNOSE | project, task, cpg, trace | state, ckg, delta | Sin state no ve contexto del sprint; sin CKG no ve patrones |
| B4 | refine | KNOWLEDGE_CAPTURE | project, task, state | ckg, trace | Sin CKG puede duplicar conocimiento |
| B5 | sprint_planning | ADD_TASKS | project, state, delta | ckg, cpg, trace | Sin CKG no ve convenciones; sin CPG no conoce el codebase |
| B6 | sprint | PLANNING | project, state, ckg, delta | trace, cpg | Sin trace no aprende de sprints anteriores |
| B7 | sprint | RETRO | project, state, ckg, delta | trace, cpg | Sin trace ni cpg no tiene datos objetivos de la sprint |

### 🟢 Gap Tipo C: Oportunidades de nuevos providers (3 propuestos)

| # | Provider | Qué resuelve | Para qué nodos |
|---|----------|-------------|---------------|
| C1 | SprintProvider | Acceso al backlog completo del sprint (tasks con estado, completados, burndown) | sprint.REVIEW, sprint.CLOSE_CONFIRM, verify.PO_ACCEPT, implement.REPORT_GATE |
| C2 | DiffProvider | Diferencia estructurada del código (git diff desde último checkpoint) | implement.CODE_REVIEW_RESULT, implement.KNOWLEDGE_UPDATE, verify.QA |
| C3 | TestResultsProvider | Resultados de tests estructurados (cobertura, failures históricos) | verify.QA, refine.DIAGNOSE |

---

## 4. Fase 1 — Contexto para sub_estados user_input

### 4.1 Principio general

Todo `user_input` debe recibir al menos `task + state + project` para que el humano tenga:
- **task**: qué tarea se está evaluando (objective, validation, predicates, artifacts)
- **state**: dónde estamos en el workflow (sprint goal, node position)
- **project**: convenciones del proyecto para contexto

### 4.2 Especificación por sub_estado

#### A1: analyze.REVIEW

```json
{
  "key": "REVIEW",
  "context": {
    "sources": [
      {"provider": "project", "config": {}},
      {"provider": "task", "config": {}},
      {"provider": "state", "config": {"query": {}}}
    ],
    "focus": {
      "role": "reviewing analysis results for task {task_id}",
      "constraints": [
        "Present the 6->3 analysis summary from the state layer",
        "Show task objective, impacted files, and risks from TASK layer",
        "Ask user to approve or reject the analysis"
      ],
      "context_priority": "project → task → state"
    }
  }
}
```

#### A2: analyze.KNOWLEDGE_REVIEW

```json
{
  "key": "KNOWLEDGE_REVIEW",
  "context": {
    "sources": [
      {"provider": "project", "config": {}},
      {"provider": "task", "config": {}},
      {"provider": "state", "config": {"query": {}}}
    ],
    "focus": {
      "role": "reviewing captured knowledge for task {task_id}",
      "constraints": [
        "Show the artifacts, rules, decisions, and warnings that will be saved to CKG",
        "Ask user to approve or revise"
      ],
      "context_priority": "project → task → state"
    }
  }
}
```

#### A3: design.DESIGN_REVIEW

```json
{
  "key": "DESIGN_REVIEW",
  "context": {
    "sources": [
      {"provider": "project", "config": {}},
      {"provider": "task", "config": {}},
      {"provider": "state", "config": {"query": {}}}
    ],
    "focus": {
      "role": "reviewing architectural design for task {task_id}",
      "constraints": [
        "Present the 6->3 design analysis from the state layer",
        "Show task objective and impact analysis from TASK layer",
        "Ask user to approve, reject, or refine"
      ],
      "context_priority": "project → task → state"
    }
  }
}
```

#### A4: implement.CODE_REVIEW_RESULT

```json
{
  "key": "CODE_REVIEW_RESULT",
  "context": {
    "sources": [
      {"provider": "project", "config": {}},
      {"provider": "task", "config": {}},
      {"provider": "cpg", "config": {}},
      {"provider": "ckg", "config": {}},
      {"provider": "state", "config": {"query": {}}}
    ],
    "focus": {
      "role": "reviewing code review results for task {task_id}",
      "constraints": [
        "Show code review findings from the scan results",
        "Cross-reference with TASK layer for acceptance criteria",
        "Cross-reference with CPG for impacted files",
        "Cross-reference with CKG for quality conventions",
        "Ask user to approve, refine, analyze, or cancel"
      ],
      "context_priority": "project → task → cpg → ckg → state"
    }
  }
}
```

#### A5: implement.REPORT_GATE

```json
{
  "key": "REPORT_GATE",
  "context": {
    "sources": [
      {"provider": "project", "config": {}},
      {"provider": "task", "config": {}},
      {"provider": "state", "config": {"query": {}}}
    ],
    "focus": {
      "role": "presenting post-implementation report for task {task_id}",
      "constraints": [
        "Show what was implemented, impacted files, and test results",
        "Cross-reference with TASK layer to verify all requirements were met",
        "Ask user to proceed to verification, refine, or cancel"
      ],
      "context_priority": "project → task → state"
    }
  }
}
```

#### A6: refine.CONFIRM_GATE

```json
{
  "key": "CONFIRM_GATE",
  "context": {
    "sources": [
      {"provider": "project", "config": {}},
      {"provider": "task", "config": {}},
      {"provider": "state", "config": {"query": {}}},
      {"provider": "trace", "config": {"max_events": 10}}
    ],
    "focus": {
      "role": "confirming refinement route for task {task_id}",
      "constraints": [
        "Show the root cause diagnosis and proposed refinement target",
        "Show trace history to explain what triggered the refinement",
        "Ask user to approve, modify, or cancel"
      ],
      "context_priority": "project → task → trace → state"
    }
  }
}
```

#### A7: sprint.PLAN_APPROVAL

```json
{
  "key": "PLAN_APPROVAL",
  "context": {
    "sources": [
      {"provider": "project", "config": {}},
      {"provider": "state", "config": {"query": {}}}
    ],
    "focus": {
      "role": "approving sprint plan",
      "constraints": [
        "Show the sprint goal, task list, and task breakdown from STATE layer",
        "Show project conventions from PROJECT layer",
        "Ask user to approve or revise"
      ],
      "context_priority": "project → state"
    }
  }
}
```

#### A8: sprint.REVIEW

```json
{
  "key": "REVIEW",
  "context": {
    "sources": [
      {"provider": "project", "config": {}},
      {"provider": "state", "config": {"query": {}}},
      {"provider": "delta", "config": {"max_results": 10}}
    ],
    "focus": {
      "role": "reviewing sprint \"{sprint_goal}\"",
      "constraints": [
        "Show sprint completed tasks, pending tasks, and overall progress from STATE layer",
        "Show cross-sprint history from DELTA layer for context",
        "Ask user to accept or mark as needs_work"
      ],
      "context_priority": "project → state → delta"
    }
  }
}
```

#### A9: sprint.CLOSE_CONFIRM

```json
{
  "key": "CLOSE_CONFIRM",
  "context": {
    "sources": [
      {"provider": "project", "config": {}},
      {"provider": "state", "config": {"query": {}}}
    ],
    "focus": {
      "role": "confirming sprint close for \"{sprint_goal}\"",
      "constraints": [
        "Show sprint summary: total tasks, completed, pending, and retrospective highlights",
        "Ask user to close or cancel (return to active)"
      ],
      "context_priority": "project → state"
    }
  }
}
```

#### A10: verify.PO_ACCEPT

```json
{
  "key": "PO_ACCEPT",
  "context": {
    "sources": [
      {"provider": "project", "config": {}},
      {"provider": "task", "config": {}},
      {"provider": "state", "config": {"query": {}}}
    ],
    "focus": {
      "role": "product owner reviewing completed task {task_id}",
      "constraints": [
        "Show completed task details from TASK layer (objective, acceptance criteria)",
        "Show verification results from STATE layer (test results, QA decision)",
        "Ask user to accept or reject"
      ],
      "context_priority": "project → task → state"
    }
  }
}
```

### 4.3 Resumen de providers a agregar en Fase 1

| Provider | A1 | A2 | A3 | A4 | A5 | A6 | A7 | A8 | A9 | A10 | Total |
|----------|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:-----:|
| project | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | 10 |
| task | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |   |   |   | ✓ | 7 |
| state | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | 10 |
| cpg |   |   |   | ✓ |   |   |   |   |   |   | 1 |
| ckg |   |   |   | ✓ |   |   |   |   |   |   | 1 |
| trace |   |   |   |   |   | ✓ |   |   |   |   | 1 |
| delta |   |   |   |   |   |   |   | ✓ |   |   | 1 |

---

## 5. Fase 2 — Completar contexto en sub_estados llm_task

### 5.1 KNOWLEDGE_CAPTURE (analyze) — B1

**Problema**: El LLM captura conocimiento sin saber qué ya existe en CKG → duplica. No ve el código concreto que analizó.

**Solución**: Agregar `ckg` + `cpg` + `trace`

```json
{
  "sources": [
    {"provider": "project", "config": {}},
    {"provider": "task", "config": {}},
    {"provider": "state", "config": {"query": {}}},
    {"provider": "ckg", "config": {"query": {"type": "decision"}, "max_nodes": 10}},
    {"provider": "cpg", "config": {"max_results": 10}},
    {"provider": "trace", "config": {"max_events": 5}}
  ]
}
```

### 5.2 KNOWLEDGE_UPDATE (implement) — B2

**Problema**: Similar al anterior — el LLM actualiza conocimiento sin saber el estado actual del CKG.

**Solución**: Agregar `ckg` + `cpg`

```json
{
  "sources": [
    {"provider": "project", "config": {}},
    {"provider": "task", "config": {}},
    {"provider": "state", "config": {"query": {}}},
    {"provider": "ckg", "config": {"query": {"type": "decision"}, "max_nodes": 10}},
    {"provider": "cpg", "config": {"max_results": 15}}
  ]
}
```

### 5.3 DIAGNOSE (refine) — B3

**Problema**: El diagnóstico no tiene contexto del sprint ni conocimiento del proyecto, solo ve el código y el trace.

**Solución**: Agregar `state` + `ckg` + `delta`

```json
{
  "sources": [
    {"provider": "project", "config": {}},
    {"provider": "task", "config": {}},
    {"provider": "cpg", "config": {"query": {"file": "_walker"}, "max_results": 20}},
    {"provider": "trace", "config": {"max_events": 10}},
    {"provider": "state", "config": {"query": {}}},
    {"provider": "ckg", "config": {"query": {"type": "pattern"}, "max_nodes": 10}},
    {"provider": "delta", "config": {"max_results": 5}}
  ]
}
```

### 5.4 KNOWLEDGE_CAPTURE (refine) — B4

**Problema**: Captura lecciones aprendidas sin conocer el CKG existente ni el trace del diagnóstico.

**Solución**: Agregar `ckg` + `trace`

```json
{
  "sources": [
    {"provider": "project", "config": {}},
    {"provider": "task", "config": {}},
    {"provider": "state", "config": {"query": {}}},
    {"provider": "ckg", "config": {"query": {"type": "pattern"}, "max_nodes": 10}},
    {"provider": "trace", "config": {"max_events": 8}}
  ]
}
```

### 5.5 ADD_TASKS (sprint_planning) — B5

**Problema**: Agrega tareas al plan sin conocer convenciones del proyecto, estructura de código, ni retrospectivas pasadas.

**Solución**: Agregar `ckg` + `cpg` + `trace`

```json
{
  "sources": [
    {"provider": "project", "config": {}},
    {"provider": "state", "config": {"query": {}}},
    {"provider": "delta", "config": {"max_results": 10}},
    {"provider": "ckg", "config": {"query": {"type": "pattern"}, "max_nodes": 10}},
    {"provider": "cpg", "config": {"max_results": 15}},
    {"provider": "trace", "config": {"max_events": 5}}
  ]
}
```

### 5.6 PLANNING (sprint) — B6

**Problema**: Planifica el sprint sin conocer retrospectivas anteriores ni la estructura actual del código.

**Solución**: Agregar `trace` + `cpg`

```json
{
  "sources": [
    {"provider": "project", "config": {}},
    {"provider": "state", "config": {"query": {}}},
    {"provider": "ckg", "config": {"query": {"type": "decision"}, "max_nodes": 20}},
    {"provider": "delta", "config": {"max_results": 10}},
    {"provider": "trace", "config": {"max_events": 10}},
    {"provider": "cpg", "config": {"max_results": 20}}
  ]
}
```

### 5.7 RETRO (sprint) — B7

**Problema**: La retrospectiva no tiene datos objetivos del historial de sesión ni de los cambios de código.

**Solución**: Agregar `trace` + `cpg`

```json
{
  "sources": [
    {"provider": "project", "config": {}},
    {"provider": "state", "config": {"query": {}}},
    {"provider": "ckg", "config": {"query": {"type": "pattern"}, "max_nodes": 15}},
    {"provider": "delta", "config": {"max_results": 10}},
    {"provider": "trace", "config": {"max_events": 20}},
    {"provider": "cpg", "config": {"max_results": 20}}
  ]
}
```

### 5.8 Resumen de cambios Fase 2

| Provider | B1 | B2 | B3 | B4 | B5 | B6 | B7 | Total |
|----------|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:-----:|
| ckg | ✓ | ✓ | ✓ | ✓ | ✓ |   |   | 5 |
| cpg | ✓ | ✓ |   |   | ✓ | ✓ | ✓ | 5 |
| trace | ✓ |   |   | ✓ | ✓ | ✓ | ✓ | 5 |
| state |   |   | ✓ |   |   |   |   | 1 |
| delta |   |   | ✓ |   |   |   |   | 1 |

---

## 6. Fase 3 — Nuevos ContextProviders

### 6.1 SprintProvider

**Problema que resuelve**: Actualmente `StateProvider` da el estado del workflow (posición actual, sprint_id, goal) pero NO da el backlog completo del sprint: lista de tareas con sus estados (pending, in_progress, completed, blocked), métricas de completado, burndown.

**Para qué nodos es crítico**:
- `sprint.REVIEW` — necesita mostrar qué se completó vs qué quedó pendiente
- `sprint.CLOSE_CONFIRM` — necesita resumen del sprint con métricas
- `verify.PO_ACCEPT` — necesita ver la tarea en contexto del sprint
- `implement.REPORT_GATE` — necesita ver el progreso del sprint

**Diseño**:

```python
class SprintProvider(ContextProvider):
    """Capa: backlog completo del sprint activo.
    
    Proporciona: lista de tareas con estado, goal, métricas de completado,
    dependencies entre tareas, burndown.
    
    NO usa QueryManager — lee sprint-state.json directamente.
    """

    def instructions(self, config, ctx) -> str:
        return "Layer: sprint — full sprint backlog with task statuses, completion metrics, and burndown. Use to understand sprint progress."

    def collect(self, config, ctx, query_manager=None) -> ContextPiece | None:
        # Lee sprint-state.json del chain_dir
        # Extrae: sprint_id, goal, tasks[{id, status, objective, type, domain}], 
        #         completed_count, total_count
        # Formatea como texto estructurado
        ...
```

**Formato de salida propuesto**:
```
sprint: Sprint 3 — "Migrate workflow engine to DDD"
status: active
tasks: 5 total • 3 completed • 1 in_progress • 1 pending
completion: 60%

tasks:
  TASK-001 [completed] Implement ExecutionType enum
  TASK-002 [completed] Create TaskRegistry pattern
  TASK-003 [in_progress] Migrate OrchestratorService
  TASK-004 [pending] Add unit tests for TaskRegistry
  TASK-005 [pending] Update documentation
```

### 6.2 DiffProvider

**Problema que resuelve**: Cuando se hace code review o QA, no hay una capa que diga "qué cambió exactamente". El CPG da estructura del código actual pero no la diferencia contra el estado anterior. Esto obliga al LLM a inferir cambios comparando manualmente.

**Para qué nodos es crítico**:
- `implement.CODE_REVIEW_RESULT` — necesita mostrar qué cambió para que el humano decida
- `implement.KNOWLEDGE_UPDATE` — necesita saber qué cambió para registrar correctamente
- `verify.QA` — necesita ver el diff para evaluar calidad

**Diseño**:

```python
class DiffProvider(ContextProvider):
    """Capa: diff estructurado del código modificado.
    
    Proporciona: archivos modificados, líneas añadidas/eliminadas,
    funciones cambiadas, imports nuevos/eliminados.
    
    Lee git diff desde el último checkpoint registrado en trace.
    """
    
    LAYER = "diff"  # Opcional: podría usar QueryManager si hay un index de diffs
    
    def instructions(self, config, ctx) -> str:
        return "Layer: diff — code changes in the current task (files changed, lines added/removed, modified functions). Use for code review."

    def collect(self, config, ctx, query_manager=None) -> ContextPiece | None:
        # Encuentra el último checkpoint desde el trace
        # Ejecuta git diff --stat + git diff para cambios
        # Parsea y estructura: archivos, funciones, imports
        ...
```

**Formato de salida propuesto**:
```
diff: 3 files changed • +89 lines • -12 lines

modified:
  domain/models.py:
    +ExecutionType enum (5 variants)
    +TaskRegistry class
    -LegacyTaskDispatcher class
  
  application/service.py:
    +import ExecutionType from domain.models
    +import TaskRegistry from domain.models
    ~refactor execute_task() to use TaskRegistry
    
  tests/test_execution_type.py: [NEW]
    +test_execution_type_has_5_variants
    +test_each_variant_is_unique
```

### 6.3 TestResultsProvider

**Problema que resuelve**: Actualmente `QA_RUN` captura stdout/stderr como strings planos. No hay una capa estructurada de resultados de tests que el LLM pueda consultar. Esto fuerza al QA prompt a depender del output textual de `run_tests` sin estructura.

**Para qué nodos es crítico**:
- `verify.QA` — necesita resultados de tests estructurados para presentar al humano
- `refine.DIAGNOSE` — necesita saber qué tests fallaron para diagnosticar causa raíz
- `implement.CODE_REVIEW_RESULT` — necesita correlacionar code review findings con test failures

**Diseño**:

```python
class TestResultsProvider(ContextProvider):
    """Capa: resultados estructurados de la ejecución de tests.
    
    Proporciona: exit_code, resultados por test (passed/failed/error),
    cobertura, línea de fallo, mensaje de error.
    
    Lee del output capturado por QA_RUN o del reporte de tests.
    """
    
    def instructions(self, config, ctx) -> str:
        return "Layer: test_results — structured test output (passed/failed tests, coverage, error messages). Use for QA decisions."

    def collect(self, config, ctx, query_manager=None) -> ContextPiece | None:
        # Lee el resultado más reciente de QA_RUN desde el trace o state
        # Parsea stdout para extraer: total, passed, failed, errors, coverage
        # Si hay un reporte estructurado (junit.xml, coverage.json), lo parsea
        ...
```

**Formato de salida propuesto**:
```
test_results:
  exit_code: 1
  total: 24 • passed: 22 • failed: 2 • errors: 0
  coverage: 78.5%

  failed tests:
    test_task_registry_route: AssertionError at line 45
      Expected route_to_target('analyze'), got route_to_target('implement')
    test_execution_type_serialize: TypeError at line 102
      Object of type ExecutionType is not JSON serializable
```

---

## 7. Especificación Técnica de Nuevos Providers

### 7.1 SprintProvider — detalle de implementación

```python
class SprintProvider(ContextProvider):
    """Capa: backlog completo del sprint activo."""

    def instructions(self, config: dict, ctx: Any) -> str:
        return "Layer: sprint — full sprint backlog with task statuses, completion metrics, and burndown."

    def collect(self, config: dict, ctx: Any, query_manager: Any = None) -> ContextPiece | None:
        chain_dir = getattr(ctx, "chain_dir", None)
        if not chain_dir:
            return None
        
        sprint_state_path = os.path.join(chain_dir, "sprint-state.json")
        if not os.path.exists(sprint_state_path):
            return None
        
        try:
            with open(sprint_state_path) as f:
                state = json.load(f)
        except (json.JSONDecodeError, OSError):
            return None
        
        parts = []
        
        # Sprint identity
        sprint_id = state.get("sprint_id", "")
        goal = state.get("sprint_goal", "")
        status = state.get("sprint_status", "")
        parts.append(f"sprint: {sprint_id} — \"{goal}\"")
        parts.append(f"status: {status}")
        
        # Task inventory
        tasks = state.get("tasks", [])
        total = len(tasks)
        completed = sum(1 for t in tasks if t.get("status") == "completed")
        in_progress = sum(1 for t in tasks if t.get("status") == "in_progress")
        pending = total - completed - in_progress
        parts.append(f"tasks: {total} total • {completed} completed • {in_progress} in_progress • {pending} pending")
        if total > 0:
            parts.append(f"completion: {int(completed / total * 100)}%")
        
        # Task list
        if tasks:
            parts.append("")
            parts.append("tasks:")
            for t in tasks:
                tid = t.get("task_id", "?")
                tstatus = t.get("status", "?")
                tobjective = t.get("objective", t.get("task_description", ""))[:80]
                parts.append(f"  {tid} [{tstatus}] {tobjective}")
        
        content = "\n".join(parts)
        return ContextPiece(
            provider="sprint",
            name="sprint backlog",
            instructions=self.instructions(config, ctx),
            content=content,
            tokens=len(content) // 4,
            priority=0,  # Misma prioridad que state
        )
```

**Registro** (en `__init__.py:create_default_registry`):
```python
registry.register("sprint", SprintProvider())
```

### 7.2 DiffProvider — detalle de implementación

```python
import subprocess


class DiffProvider(ContextProvider):
    """Capa: diff estructurado del código modificado."""

    def instructions(self, config: dict, ctx: Any) -> str:
        return "Layer: diff — code changes (files changed, lines added/removed, modified functions). Use for code review."

    def collect(self, config: dict, ctx: Any, query_manager: Any = None) -> ContextPiece | None:
        workspace = getattr(ctx, "workspace", getattr(ctx, "repo_path", None))
        if not workspace:
            return None
        
        # Encontrar el último checkpoint desde el trace
        # Por defecto: diff contra HEAD~1 o el commit base de la tarea
        base_ref = config.get("base_ref", "HEAD")
        
        try:
            # git diff --stat para resumen
            stat_result = subprocess.run(
                ["git", "diff", "--stat", f"{base_ref}..HEAD"],
                capture_output=True, text=True, cwd=workspace,
                timeout=10
            )
            stat_output = stat_result.stdout.strip()
            
            # git diff para cambios detallados (limitado)
            diff_result = subprocess.run(
                ["git", "diff", f"{base_ref}..HEAD", "--", "*.py"],
                capture_output=True, text=True, cwd=workspace,
                timeout=10
            )
            diff_output = diff_result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return None
        
        if not stat_output:
            return ContextPiece(
                provider="diff",
                name="code diff",
                instructions=self.instructions(config, ctx),
                content="(no changes detected)",
                tokens=5,
                priority=25,
            )
        
        # Parsear stat para extraer métricas
        parts = [stat_output.split("\n")[0]]  # "X files changed, +N -M"
        
        # Parsear archivos modificados del stat
        files = []
        for line in stat_output.split("\n")[1:]:
            line = line.strip()
            if line and not line.startswith(" ") and not line.startswith("\t"):
                files.append(line.split("|")[0].strip())
        
        if files:
            parts.append("")
            parts.append("files:")
            for f in files[:15]:
                parts.append(f"  • {f}")
            if len(files) > 15:
                parts.append(f"  ... (+{len(files) - 15} more)")
        
        # Diff detallado: funciones modificadas (heurística simple)
        if diff_output:
            parts.append("")
            parts.append("changes (key functions):")
            added_lines = []
            for line in diff_output.split("\n"):
                if line.startswith("+") and not line.startswith("+++") and line.strip("+").strip():
                    clean = line[1:].strip()
                    if clean.startswith(("def ", "class ", "    def ")):
                        added_lines.append(f"  +{clean}")
            parts.extend(added_lines[:20])
        
        content = "\n".join(parts)
        return ContextPiece(
            provider="diff",
            name="code diff",
            instructions=self.instructions(config, ctx),
            content=content,
            tokens=len(content) // 4,
            priority=25,
        )
```

**Registro**:
```python
registry.register("diff", DiffProvider())
```

### 7.3 TestResultsProvider — detalle de implementación

```python
class TestResultsProvider(ContextProvider):
    """Capa: resultados estructurados de tests."""

    def instructions(self, config: dict, ctx: Any) -> str:
        return "Layer: test_results — structured test output (passed/failed, coverage, errors). Use for QA."

    def collect(self, config: dict, ctx: Any, query_manager: Any = None) -> ContextPiece | None:
        # Leer desde el último resultado en trace
        if query_manager is None:
            return None
        
        # Query al trace para encontrar el último QA_RUN
        result = query_manager.query_layer("trace", {"event": "qa_run"})
        if not result.get("ok", False):
            return None
        
        # Extraer datos del último evento QA_RUN
        events = []
        for items in result.get("data", {}).get("results", {}).values():
            events.extend(items)
        
        if not events:
            return None
        
        latest = events[-1]
        data = latest.get("data", {})
        
        parts = []
        exit_code = data.get("exit_code", "?")
        stdout = data.get("stdout", "")
        
        parts.append(f"exit_code: {exit_code}")
        
        # Parseo básico de stdout pytest
        passed = 0
        failed = 0
        errors = 0
        
        for line in stdout.split("\n"):
            line = line.strip()
            if "passed" in line and "failed" in line:
                # pytest summary line: "24 passed, 2 failed"
                import re
                nums = re.findall(r"(\d+)\s+(passed|failed|error)", line)
                for num, status in nums:
                    if status == "passed":
                        passed = int(num)
                    elif status == "failed":
                        failed = int(num)
                    elif status == "error":
                        errors = int(num)
        
        total = passed + failed + errors
        parts.append(f"total: {total} • passed: {passed} • failed: {failed} • errors: {errors}")
        
        # Extraer líneas de FAILURES del stdout
        if failed > 0 or errors > 0:
            in_failures = False
            failure_lines = []
            for line in stdout.split("\n"):
                if "FAILURES" in line or "FAILED" in line:
                    in_failures = True
                if in_failures:
                    failure_lines.append(line)
                    if len(failure_lines) > 30:
                        break
            
            if failure_lines:
                parts.append("")
                parts.append("failures:")
                parts.extend(f"  {l}" for l in failure_lines[:25])
        
        content = "\n".join(parts)
        return ContextPiece(
            provider="test_results",
            name="test results",
            instructions=self.instructions(config, ctx),
            content=content,
            tokens=len(content) // 4,
            priority=5,  # Alta prioridad para QA
        )
```

**Registro**:
```python
registry.register("test_results", TestResultsProvider())
```

---

## 8. Plan de Implementación Priorizado

### ✅ Fase 1 — Contexto para user_input (Completada)
**10 sub_estados modificados** — cada uno ahora tiene `context` block con providers específicos.

| # | Sub_estado | Providers | Archivo |
|---|-----------|-----------|---------|
| A1 | analyze.REVIEW | project, task, state | analyze.node.json |
| A2 | analyze.KNOWLEDGE_REVIEW | project, task, state | analyze.node.json |
| A3 | design.DESIGN_REVIEW | project, task, state | design.node.json |
| A4 | implement.CODE_REVIEW_RESULT | project, task, cpg, ckg, state | implement.node.json |
| A5 | implement.REPORT_GATE | project, task, state | implement.node.json |
| A6 | refine.CONFIRM_GATE | project, task, trace, state | refine.node.json |
| A7 | sprint.PLAN_APPROVAL | project, state | sprint.node.json |
| A8 | sprint.REVIEW | project, state, delta | sprint.node.json |
| A9 | sprint.CLOSE_CONFIRM | project, state | sprint.node.json |
| A10 | verify.PO_ACCEPT | project, task, state | verify.node.json |

### ✅ Fase 2 — Completar llm_task (Completada)
**7 sub_estados modificados** — agregados providers faltantes.

| # | Sub_estado | Tenía | Se agregó |
|---|-----------|-------|-----------|
| B1 | analyze.KNOWLEDGE_CAPTURE | project, task, state | ckg, cpg, trace |
| B2 | implement.KNOWLEDGE_UPDATE | project, task, state | ckg, cpg |
| B3 | refine.DIAGNOSE | project, task, cpg, trace | state, ckg, delta |
| B4 | refine.KNOWLEDGE_CAPTURE | project, task, state | ckg, trace |
| B5 | sprint_planning.ADD_TASKS | project, state, delta | ckg, cpg, trace |
| B6 | sprint.PLANNING | project, state, ckg, delta | trace, cpg |
| B7 | sprint.RETRO | project, state, ckg, delta | trace, cpg |

### ✅ Fase 3 — Nuevos providers (Completada)
**3 nuevos providers implementados y registrados** en `providers.py` + `__init__.py`:

| Provider | Archivo | Propósito |
|----------|---------|-----------|
| SprintProvider | providers.py:645-712 | Lee sprint-state.json, formatea backlog con task statuses y % completado |
| DiffProvider | providers.py:715-802 | Ejecuta git diff, parsea archivos modificados y funciones |
| TestResultsProvider | providers.py:805-888 | Lee trace del último QA_RUN, parsea stdout pytest |

Todos registrados en `create_default_registry()` como `"sprint"`, `"diff"`, `"test_results"`.
Tests: 103 pasan, 0 regresiones.

---

## 9. Criterios de Verificación

### 9.1 Tests existentes

```bash
# Ejecutar tests core
python -m pytest scripts/tests/test_basic.py scripts/tests/test_skill_e2e.py -v

# Ejecutar tests v3
python -m pytest scripts/tests/v3/ -v
```

### 9.2 Verificación manual

Para cada sub_estado modificado, verificar:
1. El archivo `.node.json` tiene sintaxis JSON válida (`python -m json.tool file.json`)
2. La propiedad `context.sources` lista los providers correctos según la especificación
3. Las propiedades `focus.role`, `focus.constraints` y `focus.context_priority` están presentes
4. El provider `"project"` aparece como PRIMER source en todos los casos

### 9.3 Verificación de prompts

Los prompts asociados a cada sub_estado deben referenciar las capas de contexto disponibles, no herramientas antiguas (`walker task code_context:query`, etc.). Verificar:
- `verify/qa.prompt.json`
- `implement/user-gate.prompt.json`
- Cualquier prompt que referencie `"instruction:"` multilínea con comandos `walker`

### 9.4 Prueba de integración

```python
# Test de contexto: verificar que los providers se resuelven correctamente
def test_user_gate_has_context():
    registry = create_default_registry()
    assert registry.get("project") is not None
    assert registry.get("task") is not None
    assert registry.get("state") is not None
    # ... etc

def test_all_user_input_have_context():
    nodes = load_all_nodes("workflow.v3/nodes/")
    for node in nodes:
        for sub in node["sub_states"]:
            if sub.get("execution") == "user_input":
                assert "context" in sub, f"{node['label']}.{sub['key']} missing context"
                assert len(sub["context"]["sources"]) >= 3, \
                    f"{node['label']}.{sub['key']} needs at least project+task+state"
```
