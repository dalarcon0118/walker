# Design Sprint 2 — Application Layer

**Status:** DRAFT — pending approval before coding
**Date:** 2026-09-01
**ADR reference:** DESIGN-PHASE3-workflow-engine.md §10 Sprint 2
**Depends on:** Sprint 1 (domain layer) — DONE ✅

---

## 1. Objective

Port the application layer from `mcp/shared/core/engine/application/` (~3.5k LOC across 18 files) to `fast_mcp/features/workflow/application/` (~600 LOC across 4 files).

**Exit criteria:**
- `OrchestrationService` works with mocked ports (submit/transition/status)
- `EngineBundle` is project-scoped (per chain_dir)
- `execute_public_task` dispatches via injected registry
- StateManager persists WorkflowState via IWorkflowStore
- 10+ application-layer unit tests with mocked ports
- Zero imports from `mcp/`

---

## 2. What gets ported vs deferred

### Ported (needed for submit/transition/status/help)

| Original file | LOC | fast_mcp/ target | LOC | Simplification |
|---------------|-----|-------------------|-----|----------------|
| `bundle.py` | 53 | `application/bundle.py` | ~40 | Remove `to_orchestrator_kwargs()`, simplify fields |
| `orchestrator.py` | 645 | `application/orchestrator.py` | ~250 | Collapse submit/transition/status/help into methods. Remove 15 private helper methods. Remove PromptService, EventDispatchService, ContextComposer, SignalBus, ErrorHandler, ContractValidator. |
| `task_dispatcher.py` | 77 | `application/task_dispatcher.py` | ~40 | Remove `Any` type, inject registry properly |
| `state_manager.py` | 127 | `application/state_manager.py` | ~30 | Remove `walker.contract_data`/`decision_log` (domain-specific), remove `load_workflow` import |

**Total: ~360 LOC** (was ~900 LOC in originals)

### Deferred (not needed for MCP tools)

| Original file | LOC | Why deferred |
|---------------|-----|-------------|
| `submit_orchestrator.py` | 471 | Collapsed into orchestrator.submit() method |
| `transition_orchestrator.py` | 175 | Collapsed into orchestrator.transition() method |
| `status_orchestrator.py` | 180 | Collapsed into orchestrator.status() method |
| `step_orchestrator.py` | 427 | MCP doesn't auto-execute steps; LLM decides |
| `workflow_api.py` | 405 | Session management not needed for MCP |
| `error_handler.py` | 375 | Fail-open at dispatch boundary (convention-001) |
| `side_effect_saga.py` | 341 | No-op stub; saga deferred to Sprint 5 |
| `context_resolver.py` | 317 | Context validation deferred |
| `prompt_service.py` | 151 | No prompts in fast_mcp |
| `event_dispatch_service.py` | 136 | Event dispatch deferred |
| `workflow_loader.py` | 127 | Merged into IWorkflowStore adapter |
| `workflow_map.py` | 102 | CLI-only tool map |
| `tool_catalog.py` | 89 | Replaced by registration.py |
| `use_cases/` (6 files) | 1,400 | Collapsed into orchestrator methods |

**Total deferred: ~4,896 LOC**

---

## 3. What gets deleted (post-Sprint 2)

| From | What | Why |
|------|------|-----|
| `fast_mcp/phases/phase3_tasks.py` | `help` and `list_projects` stubs | Replaced by orchestrator-based handlers |
| Current `help`/`list_projects` implementations | Inline code in phase3_tasks.py | Moved to handlers/workflow.py |

---

## 4. Architecture

### 4.1 EngineBundle (project-scoped VO)

```python
# fast_mcp/features/workflow/application/bundle.py

@dataclass(frozen=True)
class EngineBundle:
    """Project-scoped VO with all engine dependencies.
    
    Constructed per chain_dir (NOT per server boot).
    Adapters (workflow_store, tracer) are shared singletons.
    """
    workflow_store: IWorkflowStore
    workflow_io: IWorkflowIO
    tracer: ITracer
    side_effects: ISideEffectEngine  # no-op stub initially
    chain_dir: str
```

**Simplifications vs original:**
- No `ActionCatalog` (tasks not ported yet; use `None` until Sprint 5)
- No `context_registry`, `contract_validator`, `event_dispatcher`, `event_log` (all deferred)
- `chain_dir` is part of the bundle (project-scoped)
- `to_orchestrator_kwargs()` removed (composition.py wires directly)

### 4.2 OrchestrationService (collapsed, ~250 LOC)

The original had 4 separate orchestrators (645 + 471 + 175 + 180 = 1,471 LOC) + a 645 LOC god class. We collapse all 4 into methods on a single service.

```python
# fast_mcp/features/workflow/application/orchestrator.py

class OrchestrationService:
    """Unified entry point for workflow operations.
    
    Methods:
        submit(ctx, fields)     → dict  (validate → persist → trace)
        transition(ctx, gate)   → dict  (validate gate → apply → persist → trace)
        status(ctx)             → dict  (read-only snapshot)
        help(ctx)               → dict  (list available tools + current state)
    
    Composition root OBLIGATORIO: all dependencies injected via constructor.
    """
    
    def __init__(
        self,
        bundle: EngineBundle,
        sprint_service: ISprintService | None = None,
        conflict_resolver: IConflictResolver | None = None,
    ):
        self._bundle = bundle
        self._sprint = sprint_service
        self._conflict = conflict_resolver
```

**What each method does:**

#### submit(ctx, fields)

```
1. Load state + graph from IWorkflowStore
2. Reconcile index (INV-P3-01)
3. Get current sub-state definition
4. Check required_tools gate (if sub_state.required_tools)
5. Validate required_fields (ValidationService.check_transition_preconditions)
6. Record submit data (state.record_submit)
7. Record NodeExecution (state.record_node_execution)
8. Increment revision (optimistic concurrency)
9. Save state via IWorkflowStore
10. Trace submit via ITracer
11. Return {ok: True, data: {...}}
```

**Deferred from original submit:**
- TaskRegistry execution (no tasks ported yet)
- Side-effect saga (no-op stub)
- Error handler / retry strategies (fail-open)
- XOR auto-transition (deferred)
- Signal bus (deferred)
- Event dispatch on:{} (deferred)

#### transition(ctx, gate)

```
1. Load state + graph from IWorkflowStore
2. Check if blocked (BlockResolver.is_blocked)
3. Find gate (graph.find_gate)
4. Validate gate exists + source matches current node
5. Check transition preconditions (ValidationService)
6. Apply transition (TransitionService.apply)
7. Record NodeExecution
8. Save state via IWorkflowStore
9. Trace transition via ITracer
10. Return {ok: True, to_node, to_sub_state, ...}
```

**Deferred from original transition:**
- Pre/post contract validation
- Edge transforms
- Event dispatch on:{}
- Conflict resolution (IConflictResolver returns stub)

#### status(ctx)

```
1. Load state + graph from IWorkflowStore
2. Get current node + sub-state definition
3. Validate invariants (ValidationService)
4. Get available gates (ValidationService.get_available_gates)
5. Format last 10 NodeExecutions
6. Return {node, sub_state, execution_type, available_gates, history, ...}
```

**Deferred from original status:**
- Prompt resolution (no prompts in fast_mcp)
- Graph progress map (GraphAnalyzer)
- RequiredFieldResolver (simplified: use sub_state.required_fields directly)

#### help(ctx)

```
1. Load state + graph from IWorkflowStore
2. Return current state summary + list of registered tools
3. Return available gates + required fields
```

### 4.3 execute_public_task (~40 LOC)

```python
def execute_public_task(
    name: str,
    project_id: str,
    chain_dir: str,
    task_data: dict,
    session_id: str = "mcp",
    channel: str = "mcp",
    registry: Any = None,
) -> dict:
```

**Simplifications vs original:**
- Remove `Any` type annotation for registry (use proper type)
- Remove ScopeGuard (tasks not ported yet; registry=None → skip guard)
- Keep the same pipeline: guard → build ctx → merge payload → execute → translate errors

### 4.4 StateManager (~30 LOC)

```python
def ensure_state(chain_dir: str, store: IWorkflowStore) -> dict:
    """Load state as dict. Raises if chain_dir empty."""
    
def store_submit_data(chain_dir: str, sub_key: str, fields: dict, store: IWorkflowStore) -> None:
    """Persist submit data to WorkflowState."""
```

**Simplifications vs original:**
- Remove `walker.contract_data` and `decision_log` (domain-specific, not needed for MCP)
- Remove `apply_state_transition` (merged into orchestrator.transition)
- Remove `log_decision` (merged into NodeExecution log)
- Inject `IWorkflowStore` instead of importing `load_state`/`save_state` from engine

---

## 5. Directory Structure

```
fast_mcp/features/workflow/application/
├── __init__.py              # exports OrchestrationService, EngineBundle
├── bundle.py                # EngineBundle (project-scoped VO)
├── orchestrator.py          # OrchestrationService (collapsed: submit/transition/status/help)
├── task_dispatcher.py       # execute_public_task (bridge for MCP tools)
└── state_manager.py         # ensure_state, store_submit_data (thin helpers)
```

**LOC estimate:** ~360 (was ~900 in originals, ~4,900 total in mcp/)

---

## 6. Invariants

| # | Invariant | Violation |
|---|-----------|-----------|
| INV-S2-01 | `EngineBundle` is constructed per `chain_dir`, not per boot | Single-project lock-in |
| INV-S2-02 | `OrchestrationService.__init__` receives all deps; no `resolve_port()` calls | Breaks DIP, hides dependencies |
| INV-S2-03 | Every method returns `dict`, never raises (caught by `dispatch()`) | MCP protocol violation |
| INV-S2-04 | `submit` increments `revision` on every save (optimistic concurrency) | Lost updates |
| INV-S2-05 | `transition` validates gate exists + source matches current_node before applying | Invalid state jump |
| INV-S2-06 | `status` is read-only: never mutates state | State corruption |

---

## 7. Dependencies

```
Sprint 2 depends on:
  Sprint 1 (domain layer) ✅
    ├── entities.py (WorkflowGraph, WorkflowState, SubState, Gate)
    ├── ports.py (IWorkflowStore, ITracer, ISprintService, IWorkflowIO, etc.)
    ├── values.py (SubStateData, SideEffectDef, etc.)
    ├── execution.py (ExecutionContext, TaskResult)
    ├── events.py (WorkflowEvent)
    ├── errors.py (WorkflowEngineError hierarchy)
    └── services/ (AdvanceService, TransitionService, ValidationService, BlockResolver)

Sprint 2 is consumed by:
  Sprint 3 (infrastructure adapters) ← IWorkflowStore, ITracer, IWorkflowIO implementations
  Sprint 4 (handlers + composition) ← OrchestrationService wired into WorkflowContext
```

---

## 8. Composition Root Wiring

```python
# fast_mcp/composition.py — WorkflowContext (Sprint 2 additions)

@dataclass(frozen=True)
class WorkflowContext:
    """Pre-wired workflow engine dependencies (project-scoped)."""
    bundle: EngineBundle
    orchestrator: OrchestrationService

def build_workflow_context(chain_dir: str) -> WorkflowContext:
    """Wire the workflow engine for a given project."""
    # Shared adapters (singletons)
    workflow_store = WorkflowStoreAdapter()  # Sprint 3
    tracer = TraceLogger()                   # Sprint 3
    workflow_io = DefaultWorkflowIO()        # Sprint 3
    side_effects = NoOpSideEffectEngine()    # Sprint 3

    # Project-scoped bundle
    bundle = EngineBundle(
        workflow_store=workflow_store,
        tracer=tracer,
        workflow_io=workflow_io,
        side_effects=side_effects,
        chain_dir=chain_dir,
    )

    # Orchestrator
    orch = OrchestrationService(bundle=bundle)

    return WorkflowContext(bundle=bundle, orchestrator=orch)
```

---

## 9. What Sprint 2 does NOT do

| Concern | Why not | When |
|---------|---------|------|
| Implement adapters (WorkflowStoreAdapter, etc.) | Sprint 3 | Sprint 3 |
| Wire composition.py with WorkflowContext | Sprint 4 | Sprint 4 |
| Register tools in phase3_tasks.py | Sprint 4 | Sprint 4 |
| Implement task execution (TaskRegistry) | Sprint 5 (delta_tracking, tasks) | Sprint 5 |
| Implement side-effect saga | Sprint 5 | Sprint 5 |
| Implement error handler / retry | Deferred (fail-open) | If needed |
| Implement prompt resolution | Not needed for MCP | Never |

---

## 10. Test Plan

| Test | What it covers | Mock |
|------|---------------|------|
| `test_bundle_creation` | EngineBundle fields are correct | None (VO) |
| `test_orchestrator_submit_happy` | submit with valid fields → ok | IWorkflowStore, ITracer |
| `test_orchestrator_submit_missing_fields` | submit with missing required_fields → error | IWorkflowStore |
| `test_orchestrator_submit_increments_revision` | submit saves state with revision+1 | IWorkflowStore |
| `test_orchestrator_transition_happy` | transition via valid gate → new node | IWorkflowStore, ITracer |
| `test_orchestrator_transition_invalid_gate` | transition via non-existent gate → error | IWorkflowStore |
| `test_orchestrator_transition_wrong_source` | transition from wrong node → error | IWorkflowStore |
| `test_orchestrator_status_returns_snapshot` | status returns all expected fields | IWorkflowStore |
| `test_orchestrator_help_returns_tools` | help returns current state + tools | IWorkflowStore |
| `test_execute_public_task_happy` | dispatch via registry → ok | TaskRegistry |
| `test_execute_public_task_blocked_scope` | internal task from mcp → blocked | TaskRegistry |
| `test_state_manager_ensure_state` | load state → dict | IWorkflowStore |
| `test_submit_blocked_state` | submit when blocked → returns blocked info | IWorkflowStore |

**LOC estimate:** ~300 (mocked ports, no IO)

---

## 11. failure_scenarios

```json
[
  {
    "fallo": "OrchestrationService recibe IWorkflowStore que lanza al cargar grafo",
    "causa_raiz": "workflow.json del proyecto no existe o está corrupto",
    "mitigacion": "submit/transition/status capturan IOError y retornan {ok: false, error: '...'}",
    "deteccion": "test: orchestrator con store que lanza retorna dict con 'error' key"
  },
  {
    "fallo": "submit incrementa revision pero save_state falla (disco lleno)",
    "causa_raiz": "Filesystem error después de modificar el state in-memory",
    "mitigacion": "state no se modifica hasta que save confirma; si save falla, state original se retorna",
    "deteccion": "test: store.save lanza → orchestrator retorna error, state unchanged"
  },
  {
    "fallo": "transition aplica gate pero store.save_state falla",
    "causa_raiz": "Mismo que submit: filesystem error post-transition",
    "mitigacion": "Transición no se persiste; error retornado al caller",
    "deteccion": "test: transition con save falla → error returned, state unchanged"
  },
  {
    "fallo": "EngineBundle construido una vez para múltiples proyectos",
    "causa_raiz": "composition.py cachea el bundle por server boot, no por chain_dir",
    "mitigacion": "build_workflow_context(chain_dir) se llama por tool invocation; bundle es por-proyecto",
    "deteccion": "test: dos chain_dirs distintos producen bundles con chain_dir correcto"
  },
  {
    "fallo": "OrchestrationService es god object de 600+ LOC como el original",
    "causa_raiz": "Se portaron los helpers sin colapsar",
    "mitigacion": "submit/transition/status son métodos directos; no hay sub-orchestradores ni use cases",
    "deteccion": "metrica: orchestrator.py < 300 LOC"
  },
  {
    "fallo": "execute_public_task recibe registry=None y lanza en ScopeGuard",
    "causa_raiz": "composition.py no inyecta el registry (tasks no portados aún)",
    "mitigacion": "Si registry es None, skip guard y retornar {ok: false, error: 'registry not available'}",
    "deteccion": "test: execute_public_task con registry=None no lanza, retorna error"
  }
]
```

---

## 12. Success Criteria

- [ ] `EngineBundle` is frozen dataclass, constructed per chain_dir
- [ ] `OrchestrationService` < 300 LOC (collapsed from 645 + 471 + 175 + 180)
- [ ] `submit` works with mocked IWorkflowStore (persist + trace)
- [ ] `transition` validates gate + applies transition + persists
- [ ] `status` returns read-only snapshot with all expected fields
- [ ] `help` returns current state summary
- [ ] `execute_public_task` dispatches via injected registry
- [ ] `StateManager` is < 30 LOC (thin helpers only)
- [ ] 13+ application-layer tests pass with mocked ports
- [ ] Zero imports from `mcp/` in any Sprint 2 file
- [ ] `orchestrator.py` has NO lazy imports (all deps via constructor)
