# Diseño: Flujo de transformación de Spec en el workflow

## 0. Contexto: Orquestador existente

**Hallazgo:** El workflow ya tiene un orquestador SAGA funcional:
- `OrchestrationService` (`features/engine/application/orchestrator.py`) — entry point unificado
- `SideEffectEngine` (`features/engine/infrastructure/side_effects.py`) — implementa SAGA con compensaciones reales

**Implicación:** El EDM no es un orquestador nuevo. Es un **componente** que se integra con la arquitectura existente:
- `transform_spec` se registra como handler modular en `SideEffectEngine`
- `OrchestrationService` inyecta el EDM y pasa la Spec en el contexto
- Las compensaciones existentes (`restore_cpg_snapshot`, `delete_ckg_node`, etc.) se reutilizan

---

## 1. Documento Spec como entidad viajera

La Spec es un **documento único** que viaja por el workflow, enriqueciéndose estado a estado. Cada nodo recibe la Spec actual, agrega campos, y devuelve la Spec actualizada.

**Formato de transporte entre nodos:**
```json
{
  "spec": { ... },
  "delta_updates": { ... },
  "memory_ops": [
    {"target": "delta", "op": "upsert_spec", "data": {...}},
    {"target": "ckg", "op": "save", "data": {...}},
    {"target": "cpg", "op": "index", "data": {...}},
    {"target": "trace", "op": "emit", "event": "spec_specified"}
  ]
}
```

---

## 2. Mapa de transformación Spec ↔ Workflow

| Workflow node | Spec status | Campos agregados | Memory ops |
|---|---|---|---|
| `SPRINT_PLANNING.PLANNING` | `draft` | `objective`, `domain`, `type`, `files` básicos | Delta: create Spring + Specs; Trace: `sprint_planned` |
| `SPRINT_PLANNING.PLAN_APPROVAL` | `draft` | `spring_id`, `priority` | Delta: spring.status=active; Trace: `plan_approved` |
| `SPRINT_PLANNING.TASK_SPEC` | `specified` | `problem_statement`, `evidence`, `predicates`, `impacted_artifacts`, `task_context`, `risks`, `quality_gates` | Delta: upsert Spec; CKG: save problem+evidence; CPG: index files; Trace: `spec_specified` |
| `SPRINT_PLANNING.TASK_REVIEW` | `specified` o `approved` | Si `approval_type=decision`: + `decision`, `architecture`, `implementation` | Delta: upsert Spec; CKG: save ADR si es decision; Trace: `task_reviewed` |
| `IMPLEMENT.USER_GATE` | `in_progress` | `loop_verification`, `loop_acceptance` | Delta: upsert Spec; Trace: `impl_started` |
| `IMPLEMENT.WORK` | `in_progress` | `task_context` enriquecido | Delta: upsert Spec; CPG: update changes; CKG: pending_update; Trace: `impl_work_done` |
| `IMPLEMENT.CODE_REVIEW_SCAN` | `in_review` | `review_metrics` | Delta: upsert Spec; CPG: scope code_review; Trace: `code_review_scanned` |
| `IMPLEMENT.CODE_REVIEW_RESULT` | `in_review` | `review_conventions`, `review_architecture`, `review_verdict` | Delta: upsert Spec; CKG: query violated conventions; Trace: `code_review_decided` |
| `IMPLEMENT.KNOWLEDGE_UPDATE` | `in_progress` | `task_context` final | Delta: upsert Spec; CKG: save updates; Trace: `knowledge_updated` |
| `IMPLEMENT.REPORT_GATE` | `completed` | `commit_hash`, `commit_url`, `completed_at` | Delta: upsert Spec; CKG: save_to_ckg; Trace: `spec_completed` |
| `VERIFY.QA` | `completed` | (validación) | CPG: final metrics; Trace: `qa_completed` |
| `VERIFY.PO_ACCEPT` | `completed` | (confirmación) | Trace: `task_accepted` |
| `RETRO` | `completed` | `lecciones`, `reglas`, `acciones` | Delta: update Spring; CKG: save_to_ckg; Trace: `retro_completed` |
| `CLOSE` | `completed` | (cierre) | Delta: spring.status=closed; Trace: `sprint_closed` |

---

## 3. Nodo `SPEC_APPROVE` integrado en `TASK_REVIEW`

No se crea un nodo nuevo. `TASK_REVIEW` extiende su output_schema:

```json
{
  "decision": "approve",
  "approval_type": "simple | decision",
  "feedback": "...",
  "decision_data": {
    "statement": "...",
    "rationale": "...",
    "pattern": {...},
    "affected_modules": [...],
    "affected_paths": [...],
    "implementation_strategy": "...",
    "steps": [...],
    "quality_gates": [...]
  }
}
```

- Si `approval_type=simple`: Spec pasa a `status=specified` (o mantiene)
- Si `approval_type=decision`: Spec pasa a `status=approved` + `type=decision` y se enriquece con `decision`, `architecture`, `implementation`

---

## 4. Side effect `transform_spec`

Cada nodo que transforme la Spec ejecuta este side effect estándar:

```json
{
  "type": "transform_spec",
  "params": {
    "status": "specified",
    "fields": {
      "problem_statement": "...",
      "evidence": [...]
    }
  }
}
```

El motor del workflow ejecuta `transform_spec` que:
1. Lee la Spec actual desde DeltaGraph
2. Fusiona los nuevos campos
3. Actualiza el status
4. Ejecuta las `memory_ops` asociadas
5. Persiste la Spec completa

---

## 5. Orquestador de memorias por fase

No es un componente nuevo, es una **matriz de operaciones** que el side effect `transform_spec` ejecuta según la fase:

| Fase | DeltaGraph | CKG | CPG | TraceGraph |
|---|---|---|---|---|
| `specified` | upsert Spec | save problem+evidence | index files | emit `spec_specified` |
| `approved` | upsert Spec + decisión | save ADR | index contracts | emit `decision_approved` |
| `in_progress` | upsert Spec | pending_update | update changes | emit `impl_started` |
| `in_review` | upsert Spec | query conventions | scope code_review | emit `code_review_scanned` |
| `completed` | upsert Spec + commit | save_to_ckg | mark implemented | emit `spec_completed` |

---

## 6. Vinculación Spec ↔ ADR

En `TASK_SPEC`, si el LLM detecta que la Spec requiere decisión arquitectónica:
1. Crea un ADR con `generated_from: SPEC-NNN`
2. La Spec tiene `related_decisions: [ADR-WALKER-NNN]`
3. Si el ADR aprobado genera nuevas Specs, se crean con `generated_from: ADR-WALKER-NNN`

Side effect: `create_adr_if_needed` en `TASK_SPEC`.

---

## 7. Cierre con commit

En `IMPLEMENT.REPORT_GATE`, cuando el usuario aprueba (`proceed`):
1. Se captura el último commit del repositorio
2. Se agrega `commit_hash`, `commit_url`, `completed_at` a la Spec
3. Se marca `status=completed`

Side effect: `capture_commit` en `REPORT_GATE`.

---

## 8. Casos de uso en delta_tracking

### Existentes (modificar)
- `SpecifySpecUseCase`: extender para guardar TODOS los campos de `specified`
- `UpdateSpecStatusUseCase`: agregar validación de transiciones

### Nuevos
- `ApproveSpecUseCase`: status `approved` + campos de decisión/arquitectura
- `StartImplementationUseCase`: status `in_progress` + `loop_verification`
- `StartCodeReviewUseCase`: status `in_review` + `review_metrics`
- `CompleteSpecUseCase`: status `completed` + commit info
- `TransformSpecUseCase`: caso genérico que fusiona campos y persiste (usado por side effect `transform_spec`)

---

## 9. Modificaciones a nodos del workflow

### `sprint.node.json`
- `TASK_SPEC`: agregar `memory_ops` y side effect `transform_spec` + `create_adr_if_needed`
- `TASK_REVIEW`: extender `output_schema` con `approval_type` y `decision_data`; agregar side effect `transform_spec`

### `implement.node.json`
- `USER_GATE`: agregar side effect `transform_spec` (status → `in_progress`)
- `WORK`: agregar side effect `transform_spec` (task_context enriquecido)
- `CODE_REVIEW_SCAN`: agregar side effect `transform_spec` (status → `in_review`, `review_metrics`)
- `CODE_REVIEW_RESULT`: agregar side effect `transform_spec` (`review_conventions`, `review_architecture`, `review_verdict`)
- `KNOWLEDGE_UPDATE`: agregar side effect `transform_spec` (task_context final)
- `REPORT_GATE`: agregar side effect `transform_spec` (status → `completed`, commit info) + `capture_commit`

---

## 10. Resumen de cambios

| Archivo | Cambio |
|---|---|
| `schemas/spec-walker.schema.json` | Schema unificado de Spec (ya creado) |
| `schemas/specs/SPEC-001.json` | Ejemplo validado (ya creado) |
| `features/engine/workflow/nodes/sprint.node.json` | `TASK_SPEC` + `TASK_REVIEW` con `transform_spec` |
| `features/engine/workflow/nodes/implement.node.json` | Todos los sub-nodos con `transform_spec` |
| `features/delta_tracking/application/use_cases_sprint.py` | Extender `SpecifySpecUseCase` + nuevos casos de uso |
| `features/delta_tracking/domain/entities.py` | Extender `Spec` con todos los campos del schema |
| `features/delta_tracking/domain/services/spec_service.py` | Agregar validaciones de transiciones |
| `features/delta_tracking/mcp.py` | Exponer nuevas herramientas si es necesario |
