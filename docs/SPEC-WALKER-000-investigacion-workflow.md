# Investigación: Flujo de transformación de Spec en el workflow

## 1. Estado actual del workflow

### Nodos existentes

| Nodo | Sub-estados | Ejecución | Propósito |
|---|---|---|---|
| **`SPRINT_PLANNING`** | PLANNING, PLAN_APPROVAL, TASK_SPEC, TASK_REVIEW, ACTIVE | llm_task / user_input | Goal → Specs → Aprobación |
| **`IMPLEMENT`** | USER_GATE, WORK, CODE_REVIEW_SCAN, CODE_REVIEW_RESULT, KNOWLEDGE_UPDATE, REPORT_GATE | llm_task / user_input | Implementación + code review + conocimiento |
| **`VERIFY`** | QA, PO_ACCEPT | llm_task / user_input | Verificación y aceptación |
| **`ANALYZE`** | WORK, REVIEW, KNOWLEDGE_CAPTURE | llm_task / user_input | Análisis |
| **`DESIGN`** | WORK, DESIGN_REVIEW | llm_task / user_input | Diseño |
| **`REFINE`** | DIAGNOSE, WORK | llm_task | Refinamiento |
| **`RETRO`** | (único) | llm_task | Retrospectiva |
| **`CLOSE`** | (único) | auto | Cierre |

### Archivos relevantes

- `features/engine/workflow/nodes/sprint.node.json` — nodos SPRINT_PLANNING
- `features/engine/workflow/nodes/implement.node.json` — nodos IMPLEMENT
- `features/engine/workflow/nodes/verify.node.json` — nodos VERIFY
- `features/engine/workflow/nodes/analyze.node.json` — nodos ANALYZE
- `features/engine/workflow/nodes/design.node.json` — nodos DESIGN
- `features/engine/workflow/nodes/refine.node.json` — nodos REFINE
- `features/engine/workflow/edges.json` — transiciones entre nodos
- `features/engine/workflow/workflow.json` — workflow raíz

---

## 2. Diagnóstico: no hay que crear nodos nuevos

**Confirmado:** ya existen los nodos de implementación y code review.

- `IMPLEMENT` tiene sub-nodos: `USER_GATE`, `WORK`, `CODE_REVIEW_SCAN`, `CODE_REVIEW_RESULT`, `KNOWLEDGE_UPDATE`, `REPORT_GATE`
- `CODE_REVIEW_SCAN` y `CODE_REVIEW_RESULT` están definidos en `implement.node.json`
- `REVIEW` existe en múltiples nodos (sprint_planning, analyze, design, verify)

**Conclusión:** no hay que agregar nodos al workflow. Lo que falta es **conectar** estos nodos existentes con el **documento Spec** y las **memorias**.

---

## 3. Gaps de diseño: workflow actual vs. deseado

### Gap 1: Falta el modelo de documento evolutivo

- **Actual:** El workflow es un flujo de ejecución de nodos. Cada nodo produce outputs aislados. No hay un modelo que gobierne la transformación del documento a través de los estados.
- **Deseado:** Un **modelo de documento evolutivo** (Evolutionary Document Model, EDM) al estilo patrón SAGA, donde:
  - El **documento** (`Spec`) es solo un objeto de datos transportado por el modelo.
  - El **modelo** gobierna:
    - Estado actual del documento
    - Historial de transformaciones (qué nodos lo mutaron, cuándo, con qué campos)
    - Reglas de transición entre estados
    - Validaciones por estado
    - Side effects por estado (operaciones en memorias)
    - Compensaciones en caso de fallo (rollback de estado)
  - Cada nodo del workflow es una **etapa de la SAGA** que:
    1. Recibe el documento en un estado conocido
    2. Aplica transformaciones permitidas por el modelo
    3. Ejecuta side effects en las memorias
    4. Persiste el nuevo estado del documento
    5. Emite eventos de trazabilidad
- **Impacto:** Sin modelo, no hay garantía de consistencia entre el documento y las memorias. Cada nodo es una transacción aislada sin contexto de la SAGA completa.

**Analogía SAGA:**
```
Documento Spec = mensaje/evento que viaja
Modelo EDM = orquestador SAGA
Nodos del workflow = pasos de la SAGA
Side effects = operaciones locales en cada servicio (Delta, CKG, CPG, Trace)
Compensaciones = rollback de estado si un paso falla
```

**Hallazgo crítico:** El orquestador SAGA **ya existe** en el código:

| Componente | Archivo | Rol |
|---|---|---|
| `OrchestrationService` | `features/engine/application/orchestrator.py` | Entry point unificado. Delega en StepOrchestrator, SubmitOrchestrator, TransitionOrchestrator. Coordina SideEffectEngine, ContextComposer, PromptManager, TaskRegistry. |
| `SideEffectEngine` | `features/engine/infrastructure/side_effects.py` | Implementa SAGA pattern: ejecuta side-effects en orden, compensa en orden inverso si falla uno NO optional, idempotencia por key, compensaciones reales (`delete_ckg_node`, `restore_cpg_snapshot`, `rollback_ckg`, `restore_delta_graph`). |

**Implicación:** No hay que crear un orquestador nuevo. El EDM debe **integrarse** con `OrchestrationService` y `SideEffectEngine` existentes.

**Diseño de integración EDM:**

1. **EDM como componente, no como orquestador**
   - `EvolutionaryDocumentModel` es un servicio que:
     - Recibe la Spec actual desde el estado del workflow
     - Aplica transformaciones permitidas según el nodo actual
     - Fusiona campos sin perder datos previos
     - Registra historial de transformaciones
     - Valida transiciones de estado

2. **Nuevo side effect: `transform_spec`**
   - Se registra en `SideEffectEngine` como handler modular
   - Cada nodo del workflow declara `transform_spec` en sus `side_effects`
   - El handler ejecuta:
     1. Lee la Spec actual desde DeltaGraph
     2. Aplica transformaciones del EDM para el nodo actual
     3. Ejecuta memory_ops (Delta, CKG, CPG, Trace)
     4. Persiste la Spec actualizada
     5. Registra en historial

3. **La Spec viaja en el ExecutionContext / WorkflowState**
   - No es un concepto nuevo, es un campo en el estado del workflow
   - Se lee al entrar al nodo, se transforma, se persiste al salir
   - El EDM mantiene el documento consistente entre nodos

4. **Compensaciones existentes se reutilizan**
   - `restore_cpg_snapshot` → rollback de CPG si code review falla
   - `delete_ckg_node` → rollback de CKG si se creó nodo de problema
   - `rollback_ckg` → rollback de conocimiento si falla persistencia
   - `restore_delta_graph` → rollback de delta si falla actualización de Spec

**Contrato del EDM (como componente):**

```python
class EvolutionaryDocumentModel:
    def __init__(self, delta_repo, ckg_port, cpg_port, trace_port):
        self._delta = delta_repo
        self._ckg = ckg_port
        self._cpg = cpg_port
        self._trace = trace_port

    def transform(self, ctx, spec, node_key, side_effects) -> dict:
        """Aplica transformación de Spec para el nodo actual."""
        # 1. Validar transición
        # 2. Aplicar campos del nodo
        # 3. Ejecutar memory_ops
        # 4. Persistir Spec
        # 5. Registrar historial
        pass

    def get_transformation_map(self, spec_id) -> dict:
        """Retorna historial completo de transformaciones."""
        pass

    def can_transition(self, spec, target_node) -> tuple[bool, str]:
        """Valida si la transición es permitida."""
        pass
```

**Responsabilidades del EDM:**

| Responsabilidad | Descripción |
|---|---|
| **Validar transiciones** | No permite saltos ilegales (ej: `draft` → `completed`) |
| **Ejecutar side effects orquestados** | Coordina Delta, CKG, CPG, Trace en el orden correcto |
| **Mantener el documento consistente** | Fusiona campos sin perder datos previos |
| **Registrar trazabilidad** | Cada paso queda en el historial con timestamp y operaciones |
| **Ejecutar compensaciones** | Si un paso falla, revierte los side effects en orden inverso |
| **Exponer el estado actual** | Cualquier consultor puede preguntar "¿dónde está SPEC-001?" |

**Diferencia con el documento Spec:**

- **Documento Spec**: es el objeto de datos (`SPEC-001.json`). Contiene los campos de la especificación.
- **Modelo EDM**: es el servicio/orquestador que gestiona el ciclo de vida de ese documento. Contiene el estado, historial, reglas y side effects.

**Analogía:**
- Documento Spec = pasaporte (datos de la persona)
- Modelo EDM = sistema de control migratorio (registra entradas/salidas, validaciones, historial)

### Gap 2: No hay estado `approved` como nodo explícito

- **Actual:** `TASK_SPEC` → `TASK_REVIEW` → `ACTIVE`. No distingue entre spec simple y decisión arquitectónica.
- **Deseado:** Después de `TASK_REVIEW`, si `type: decision`, debe capturar `decision.statement`, `architecture`, `implementation`.
- **Impacto:** Las decisiones arquitectónicas no tienen un lugar formal en el flujo.

### Gap 3: Code review no es un estado formal de Spec

- **Actual:** `CODE_REVIEW_SCAN` y `CODE_REVIEW_RESULT` son actividades, no estados persistentes.
- **Deseado:** Después del code review, la Spec pasa a `status: in_review` con `review_metrics`, `review_conventions`, `review_architecture`, `review_verdict`.
- **Impacto:** No hay persistencia de métricas ni veredicto de code review.

### Gap 4: No hay persistencia automática de Spec en cada transición

- **Actual:** Los side effects son operaciones genéricas (`capture_decision`, `update_cpg`, `save_to_ckg`).
- **Deseado:** Cada transición ejecuta `transform_spec` que persiste la Spec completa en DeltaGraph.
- **Impacto:** Los campos se pierden entre nodos.

### Gap 5: No hay vinculación Spec ↔ ADR en el flujo

- **Actual:** Si durante `TASK_SPEC` surge una decisión arquitectónica, no hay mecanismo para crear ADR ni vincularlo.
- **Deseado:** `TASK_SPEC` puede crear ADR con `generated_from: SPEC-NNN`; ADR aprobado genera nuevas Specs.
- **Impacto:** Las decisiones arquitectónicas no se registran como ADR.

### Gap 6: Falta el cierre formal con commit

- **Actual:** `REPORT_GATE` es un gate de usuario. No captura `commit_hash` ni marca `completed`.
- **Deseado:** `REPORT_GATE` captura commit info y marca Spec como `completed`.
- **Impacto:** No hay referencia al commit en la Spec completada.

### Gap 7: Las memorias no están orquestadas por fase

- **Actual:** Cada nodo tiene `context.sources` con providers, pero no hay operaciones concretas por fase.
- **Deseado:** Matriz de operaciones por fase que garantice actualización consistente de CKG, CPG, Trace, Delta.
- **Impacto:** No hay garantía de que las memorias se actualicen correctamente.

### Gap 8: Falta el concepto de "enriquecimiento progresivo"

- **Actual:** Cada nodo define su propio schema de entrada/salida aislado.
- **Deseado:** La Spec es un documento que viaja por el workflow, enriqueciéndose estado a estado.
- **Impacto:** No hay un "estado del documento" que se preserve.

---

## 4. Propuesta de diseño (sin crear nodos nuevos)

### 4.1 Mapa de transformación Spec ↔ Workflow

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

### 4.2 Nodo `SPEC_APPROVE` integrado en `TASK_REVIEW`

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

- Si `approval_type=simple`: Spec pasa a `status=specified`
- Si `approval_type=decision`: Spec pasa a `status=approved` + `type=decision` y se enriquece con `decision`, `architecture`, `implementation`

### 4.3 Side effect `transform_spec`

Cada nodo que transforme la Spec ejecuta este side effect estándar, registrado como handler modular en `SideEffectEngine`:

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

El handler `transform_spec` en `SideEffectEngine` ejecuta:
1. Lee la Spec actual desde DeltaGraph
2. Aplica transformaciones del EDM para el nodo actual
3. Ejecuta memory_ops (Delta, CKG, CPG, Trace)
4. Persiste la Spec actualizada
5. Registra en historial

Si el side effect falla, `SideEffectEngine` compensa automáticamente en orden inverso usando las compensaciones existentes (`restore_cpg_snapshot`, `delete_ckg_node`, etc.).

### 4.4 Orquestador de memorias por fase

Matriz de operaciones que el side effect `transform_spec` ejecuta según la fase:

| Fase | DeltaGraph | CKG | CPG | TraceGraph |
|---|---|---|---|---|
| `specified` | upsert Spec | save problem+evidence | index files | emit `spec_specified` |
| `approved` | upsert Spec + decisión | save ADR | index contracts | emit `decision_approved` |
| `in_progress` | upsert Spec | pending_update | update changes | emit `impl_started` |
| `in_review` | upsert Spec | query conventions | scope code_review | emit `code_review_scanned` |
| `completed` | upsert Spec + commit | save_to_ckg | mark implemented | emit `spec_completed` |

### 4.5 Vinculación Spec ↔ ADR

En `TASK_SPEC`, si el LLM detecta que la Spec requiere decisión arquitectónica:
1. Crea un ADR con `generated_from: SPEC-NNN`
2. La Spec tiene `related_decisions: [ADR-WALKER-NNN]`
3. Si el ADR aprobado genera nuevas Specs, se crean con `generated_from: ADR-WALKER-NNN`

Side effect: `create_adr_if_needed` en `TASK_SPEC`.

### 4.6 Cierre con commit

En `IMPLEMENT.REPORT_GATE`, cuando el usuario aprueba (`proceed`):
1. Se captura el último commit del repositorio
2. Se agrega `commit_hash`, `commit_url`, `completed_at` a la Spec
3. Se marca `status=completed`

Side effect: `capture_commit` en `REPORT_GATE`.

---

## 5. Casos de uso en delta_tracking

### Existentes (modificar)

| Caso de uso | Cambio |
|---|---|
| `SpecifySpecUseCase` | Extender para guardar TODOS los campos de `specified` |
| `UpdateSpecStatusUseCase` | Agregar validación de transiciones permitidas |

### Nuevos

| Caso de uso | Responsabilidad |
|---|---|
| `ApproveSpecUseCase` | Cambia a `approved` + campos de decisión/arquitectura |
| `StartImplementationUseCase` | Cambia a `in_progress` + `loop_verification` |
| `StartCodeReviewUseCase` | Cambia a `in_review` + `review_metrics` |
| `CompleteSpecUseCase` | Cambia a `completed` + commit info |
| `TransformSpecUseCase` | Caso genérico que fusiona campos y persiste (usado por side effect `transform_spec`) |

---

## 6. Modificaciones a nodos del workflow

### `sprint.node.json`

| Nodo | Cambios |
|---|---|
| `TASK_SPEC` | Agregar `memory_ops` y side effect `transform_spec` + `create_adr_if_needed` |
| `TASK_REVIEW` | Extender `output_schema` con `approval_type` y `decision_data`; agregar side effect `transform_spec` |

### `implement.node.json`

| Nodo | Cambios |
|---|---|
| `USER_GATE` | Agregar side effect `transform_spec` (status → `in_progress`) |
| `WORK` | Agregar side effect `transform_spec` (task_context enriquecido) |
| `CODE_REVIEW_SCAN` | Agregar side effect `transform_spec` (status → `in_review`, `review_metrics`) |
| `CODE_REVIEW_RESULT` | Agregar side effect `transform_spec` (`review_conventions`, `review_architecture`, `review_verdict`) |
| `KNOWLEDGE_UPDATE` | Agregar side effect `transform_spec` (task_context final) |
| `REPORT_GATE` | Agregar side effect `transform_spec` (status → `completed`, commit info) + `capture_commit` |

---

## 7. Estructura del nodo Spec en DeltaGraph

```
Spec (nodo en DeltaGraph)
  ├── spring_id: string (padre)
  ├── id: SPEC-NNN
  ├── type: feature|bugfix|chore|decision
  ├── status: draft|specified|approved|in_progress|in_review|completed
  ├── objective: string
  ├── problem_statement: string
  ├── evidence: [string]
  ├── predicates: [string]
  ├── impacted_artifacts: {files: [string], touched_contracts: [string]}
  ├── task_context: {observation, structure, behavior, causality, purpose, principles}
  ├── risks: {curvature, pre_churn, commit_trust}
  ├── decision: {statement, rationale, pattern, subsections} (si type=decision)
  ├── architecture: {affected_modules, affected_paths, pattern} (si type=decision)
  ├── implementation: {strategy, steps} (si type=decision)
  ├── loop_verification: [string]
  ├── review_metrics: object
  ├── review_conventions: [string]
  ├── review_architecture: [string]
  ├── review_verdict: string
  ├── commit_hash: string
  ├── commit_url: string
  ├── completed_at: string
  ├── created_at: string
  └── updated_at: string
```

---

## 8. Flujo completo con memorias

```
Usuario: "Necesito X"
    ↓
[SPRINT_PLANNING.PLANNING]
  → DeltaGraph: crea Spring + N Specs (status: draft)
  → TraceGraph: sprint_planned
    ↓
[SPRINT_PLANNING.PLAN_APPROVAL] (usuario aprueba)
  → DeltaGraph: spring.status = active
  → TraceGraph: plan_approved
    ↓
[SPRINT_PLANNING.TASK_SPEC] (por cada Spec)
  → DeltaGraph: Spec.status = specified + todos los campos
  → CKG: guarda problem_statement + evidence
  → CPG: indexa archivos de impacted_artifacts
  → TraceGraph: spec_specified
    ↓
[SPRINT_PLANNING.TASK_REVIEW] (usuario aprueba)
  → DeltaGraph: Spec (sin cambios de estado)
  → TraceGraph: task_reviewed
    ↓
[IMPLEMENT.USER_GATE] (usuario autoriza)
  → DeltaGraph: Spec.status = in_progress
  → TraceGraph: impl_started
    ↓
[IMPLEMENT.WORK] (implementación)
  → DeltaGraph: Spec.task_context enriquecido
  → CPG: actualiza con cambios de código
  → CKG: pending_update
  → TraceGraph: impl_work_done
    ↓
[IMPLEMENT.CODE_REVIEW_SCAN] (automático)
  → DeltaGraph: Spec.status = in_review + review_metrics
  → CPG: scope code_review
  → TraceGraph: code_review_scanned
    ↓
[IMPLEMENT.CODE_REVIEW_RESULT] (usuario decide)
  → DeltaGraph: Spec.review_verdict + review_conventions + review_architecture
  → CKG: consulta convenciones violadas
  → TraceGraph: code_review_decided
    ↓
[IMPLEMENT.KNOWLEDGE_UPDATE]
  → DeltaGraph: Spec.task_context final
  → CKG: guarda updates + nuevas_reglas + nuevas_advertencias
  → TraceGraph: knowledge_updated
    ↓
[IMPLEMENT.REPORT_GATE] (usuario aprueba)
  → DeltaGraph: Spec.status = completed + commit_hash + commit_url + completed_at
  → CKG: save_to_ckg
  → TraceGraph: spec_completed
    ↓
[VERIFY.QA] (automático)
  → CPG: métricas finales
  → TraceGraph: qa_completed
    ↓
[VERIFY.PO_ACCEPT] (usuario acepta)
  → TraceGraph: task_accepted
    ↓
[RETRO]
  → DeltaGraph: Spring.lecciones + reglas + acciones
  → CKG: save_to_ckg
  → TraceGraph: retro_completed
    ↓
[CLOSE]
  → DeltaGraph: Spring.status = closed
  → TraceGraph: sprint_closed
```

---

## 9. Resumen de cambios

| Archivo | Cambio |
|---|---|
| `schemas/spec-walker.schema.json` | Schema unificado de Spec |
| `schemas/specs/SPEC-001.json` | Ejemplo validado |
| `docs/SPEC-WALKER-001-spec-evolution.md` | Diseño completo del flujo |
| `features/engine/application/orchestrator.py` | Inyectar EDM en OrchestrationService |
| `features/engine/infrastructure/side_effects.py` | Registrar handler `transform_spec` |
| `features/engine/domain/models.py` | Agregar `spec_payload` a ExecutionContext o WorkflowState |
| `features/delta_tracking/application/use_cases_sprint.py` | Extender casos existentes + nuevos |
| `features/delta_tracking/domain/entities.py` | Extender `Spec` con todos los campos |
| `features/delta_tracking/domain/services/spec_service.py` | Validaciones de transiciones |
| `features/delta_tracking/mcp.py` | Exponer nuevas herramientas si es necesario |

---

## 10. Próximos pasos

1. Modificar `SpecifySpecUseCase` para guardar todos los campos de `specified`
2. Crear 4 casos de uso nuevos: `ApproveSpecUseCase`, `StartImplementationUseCase`, `StartCodeReviewUseCase`, `CompleteSpecUseCase`
3. Agregar handler `transform_spec` a `SideEffectEngine`
4. Modificar `OrchestrationService` para inyectar EDM y pasar Spec en el contexto
5. Modificar `implement.node.json` y `sprint.node.json` para agregar side effects `transform_spec`
6. Implementar orquestador de memorias por fase
7. Agregar vinculación Spec ↔ ADR en `TASK_SPEC`
6. Implementar cierre con commit en `REPORT_GATE`
