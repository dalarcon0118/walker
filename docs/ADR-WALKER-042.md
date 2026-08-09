# ADR-WALKER-042: Workflow Block Resolution — Wait States, Timeouts, and Resolution Strategies

**Status:** Implemented  
**Date:** 2026-08-07  
**Updated:** 2026-08-08  
**Authors:** Walker Architecture Team  
**Related:** ADR-WALKER-041 (conflict resolution), ADR-WALKER-040 (engine design), ADR-WALKER-039 (domain purity), ADR-WALKER-036 (delta_tracking SSOT/SRP)

---

## Context

The walker workflow engine can enter blocked states where the current sub-state cannot advance because:
- A required field is missing (`required_fields` not satisfied)
- A gate validation script fails
- An external tool/layer was not consulted (`required_tools` gate)
- A side-effect failed without compensation
- The workflow state was reconstructed or corrupted
- A timer expired without receiving an expected event
- A signal from another node was missed

Currently these situations either:
- **Block indefinitely** until manual intervention
- **Require external files** like `.delta-conflicts.json` (anti-pattern: parallel source of truth)
- **Force the LLM to skip the workflow** via `resolve --apply`, which breaks the workflow contract

The user experience problem is: a sprint can get stuck at a node for reasons unrelated to the sprint's business logic. The delta module defines **what** must be done (specs, contracts), but the engine decides **when** and **how** to execute. When the engine cannot proceed, it should have **first-class mechanisms** to recover, not external files.

### Inspiration: BPMN wait states, events, and data flow

BPMN engines model execution as **tokens** that traverse a graph. A token can be in:
- **Active**: executing a task
- **Wait state**: suspended, waiting for an external event
- **End state**: process completed
- **Terminated**: killed

BPMN provides patterns relevant to walker:
- **Event-Based Gateway**: waits for whichever event arrives first (message, timer, signal)
- **Timer Boundary Event**: interrupts a wait state after a timeout
- **Error Events**: structured error handling
- **Compensation**: undo completed work
- **Escalation**: bubble up to a higher scope
- **Signal Events**: decoupled communication between nodes
- **Data Objects/Store**: typed process data

Walker's engine is conceptually similar to a BPMN engine. We should adopt these patterns where they solve real problems without over-engineering.

---

## Decision

We will implement a **wait-state model with timeouts, signals, compensation, and typed data** inside the engine, eliminating `.delta-conflicts.json` as a parallel source of truth.

### 1. Timer / Boundary Events (P0)

A sub-state can declare a timeout that automatically triggers a resolution strategy:

```python
# node.json v4 example
{
  "key": "USER_GATE",
  "label": "User Review",
  "execution": "user_input",
  "timeout_ms": 3600000,  # 1 hour
  "interrupting": true,  # true = boundary event (interrumpe), false = non-interrupting
  "on_timeout": "escalate",  # skip | force | escalate | compensate
  "required_tools": [
    {"id": "ckg", "command_hint": "walker kg_search ..."}
  ]
}
```

**Behavior:**
- `interrupting: true` → when timeout expires, the sub-state is interrupted and `on_timeout` is applied
- `interrupting: false` → the sub-state continues, but `on_timeout` fires as a side-effect (warning log, escalation)
- `timeout_ms: 0` → no timeout (default, backward compatible)

### 2. BlockResolver service (P0)

New service in `engine/domain/services/block_resolver.py`:

```python
class BlockResolver:
    @staticmethod
    def is_blocked(state: WorkflowState) -> bool:
        """Check if the workflow is currently blocked."""
        
    @staticmethod
    def can_unblock(state: WorkflowState, event: str) -> bool:
        """Check if an event can unblock the current wait state."""
        
    @staticmethod
    def resolve(
        state: WorkflowState,
        strategy: str,
        payload: dict[str, Any],
        graph: WorkflowGraph | None = None,
    ) -> Result[WorkflowState, str]:
        """Apply resolution strategy and return Result monad.
        
        Strategies:
        - skip: mark sub-state as completed without data
        - force: allow submit with warning
        - wait: keep blocked (default)
        - escalate: transition to escalation node
        - compensate: rollback completed side-effects
        
        Returns:
            Success(new_state) if resolution applied
            Failure(reason) if resolution cannot proceed
        """
```

**Rationale for Result monad:**
- Eliminates exceptions as control flow (see ADR-039)
- Callers handle `Success`/`Failure` explicitly via `is_successful()` + `value_or()`
- Keeps engine pure: no hidden raises, all failures are typed

**Integration points:**
- `StepOrchestrator._check_timeout()` → checks `blocked_at` + `block_timeout_ms`
- `StepOrchestrator._check_pending_signals()` → checks `pending_signals` + `waits_for`
- `SubmitOrchestrator._check_required_tools_gate()` → checks if a `force` strategy applies
- `TransitionOrchestrator` → handles `escalate` strategy via `BlockResolver.resolve()`

### 2b. EscalationNodeRegistry (P1)

New registry in `engine/domain/services/escalation_node_registry.py`:

```python
class EscalationNodeRegistry:
    @classmethod
    def get_escalation_node(cls, error_type: ErrorType) -> str:
        """Return escalation node for error type (default: REFINE)."""
        
    @classmethod
    def register(cls, error_type: ErrorType, node_id: str) -> None:
        """Override default escalation node for an error type."""
```

**Behavior:**
- Maps `ErrorType` → escalation node (e.g., `VALIDATION` → `REFINE`)
- `BlockResolver._resolve_escalate` uses registry when `escalate_node` not provided in payload
- Falls back to `REFINE` if mapped node does not exist in workflow graph

### 3. WorkflowState tracks blocking context (P0)

```python
@dataclass(frozen=True)
class WorkflowState:
    current_node: str = ""
    current_sub_state: str = ""
    sub_state_index: int = 0
    sub_state_data: dict[str, SubStateData] = field(default_factory=dict)
    history: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    
    # NEW: wait state / blocking context (P0)
    blocked_at: str = ""  # "SPRINT.USER_GATE"
    block_reason: str = ""  # human-readable reason
    block_since: str = ""  # ISO timestamp
    pending_events: list[str] = field(default_factory=list)  # events that can unblock
    block_timeout_ms: int = 0
    conflicts: list[dict] = field(default_factory=list)  # derived, not stored externally
```

### 4. Signal Events (P1)

Sub-states can emit and listen for signals, enabling decoupled coordination:

```python
# node.json v4
{
  "key": "KNOWLEDGE_CAPTURE",
  "sub_states": [
    {
      "key": "CAPTURE",
      "execution": "auto",
      "emits": ["kg_updated"]  # signal emitted on completion
    }
  ]
}

# Another node
{
  "key": "REVIEW",
  "sub_states": [
    {
      "key": "REVIEW_GATE",
      "execution": "user_input",
      "waits_for": ["kg_updated"],  # signal that unblocks
      "on_signal": "auto_advance"  # action when signal arrives
    }
  ]
}
```

**Implementation:**
- `SignalBus` in `engine/domain/services/signal_bus.py` — simple pub/sub
- `StepOrchestrator` emits signals when sub-state completes
- `SubmitOrchestrator` checks for pending signals before blocking

**Benefit:** Nodes can react to events without tight coupling. Example: `KNOWLEDGE_CAPTURE` emits `kg_updated`, `REVIEW` advances automatically.

### 5. Compensation Handlers (P1)

Side-effects can declare compensation actions that execute in reverse order if the workflow fails:

```python
# node.json v4
{
  "key": "IMPLEMENT",
  "sub_states": [
    {
      "key": "CODE",
      "execution": "auto",
      "side_effects": [
        {
          "type": "update_cpg",
          "params": {"scope": "src/auth"},
          "compensation": "restore_cpg_snapshot"  # undo action
        }
      ]
    }
  ]
}
```

**Behavior:**
- When a sub-state fails and has `compensation`, the engine executes compensations for all completed sub-states in reverse order
- Compensation is logged in `TraceGraph` as `compensation` events
- If compensation fails, the error is logged but does not block (best-effort)

**Implementation:**
- `CompensationEngine` in `engine/domain/services/compensation_engine.py`
- `SideEffectDef` already has `compensation` field (from ADR-040)
- `SubmitOrchestrator` and `StepOrchestrator` call `CompensationEngine.execute()` on failure

### 6. Process Data Store (P2)

Replace ad-hoc `WorkflowState.context` with typed process data:

```python
@dataclass(frozen=True)
class WorkflowState:
    # ... existing fields ...
    
    # NEW: typed process data (P2)
    process_data: dict[str, Any] = field(default_factory=dict)
    """Typed data produced/consumed by the workflow.
    
    Examples:
      process_data = {
        "project_id": "walker",  # str
        "specs": [...],  # list[Spec]
        "current_spec_index": 0,  # int
        "kg_consulted_layers": ["cpg", "ckg"]  # list[str]
      }
    """
```

**Behavior:**
- `WorkflowNode.provides` declares what data the node produces
- `SubState.needs` declares what data it consumes
- `ContextResolver` validates at load time that all `needs` have a `provides` upstream
- No runtime type enforcement (too complex), but provides documentation and load-time validation

**Benefit:** Fewer "context key missing" errors. The workflow graph is self-documenting.

---

## Gap Analysis and Prioritization

| Gap | BPMN Concept | Complexity | Benefit | Priority | Status |
|-----|--------------|-------------|---------|----------|--------|
| **Timer/Boundary Events** | Timer Boundary Event | Baja | Alto: elimina bloqueos indefinidos | P0 | Implemented |
| **BlockResolver + timeouts** | Event-Based Gateway | Baja | Alto: resolución automática de bloqueos | P0 | Implemented |
| **EscalationNodeRegistry** | Escalation | Baja | Alto: routing estructurado de errores | P1 | Implemented |
| **Signal Events** | Signal Event | Media | Medio: desacople entre nodos | P1 | Implemented |
| **Compensation Handlers** | Compensation | Media | Alto: rollback estructurado de side-effects | P1 | Implemented |
| **Process Data Store** | Data Objects/Store | Baja | Bajo: menos errores de contexto | P2 | Implemented |

---

## Architecture Boundaries (respetando ADR-041 y ADR-040)

| Concern | Module | Artifact |
|---------|--------|----------|
| **What** must be provided | `delta_tracking` | `spec.required_fields`, `spec.validation.predicates`, `spec.status` |
| **When** to check | `engine` | `SubState.timeout_ms`, `ValidationService`, `BlockResolver` |
| **How** to resolve (workflow) | `engine` | `BlockResolver` strategies (`skip`, `force`, `escalate`, `compensate`) via `Result` monad |
| **How** to resolve (specs) | `delta_tracking` | `ConflictResolver.resolve()` for `edit`/`merge` strategies |
| **Who** decides | `engine` + `TraceGraph` | `SubmitOrchestrator`, `StepOrchestrator`, `WorkflowConflictResolver` |

The delta module declares: "this spec needs field X". The engine decides: "field X is missing → block with timeout 1h → if not provided, skip".

**Separation of concerns (post-implementation):**
- `ConflictDetector` in `delta_tracking` — detects conflicts from data (missing_required, obsolete_spec, spec_overlap, gate_blocked, state_corruption)
- `WorkflowConflictResolver` in `engine` — applies workflow strategies via `BlockResolver` (skip/force/escalate/compensate)
- `ConflictResolver` in `delta_tracking` — legacy facade; delegates workflow strategies to engine, retains edit/merge for specs
- `CognitiveDeltaRepository` receives `graph_factory` via DI — no longer imports from `engine/bootstrap`
- `SpecService` in `delta_tracking/domain/services/spec_service.py` — pure domain operations (`merge`, `refine`) outside the entity
- `SpringService` returns entities for internal callers, dicts for MCP/presentation layer

---

## Consequences

### Positive
- **No parallel sources of truth**: eliminates `.delta-conflicts.json`
- **Workflow never blocks indefinitely**: every wait state has a timeout or resolution path
- **BPMN-aligned**: uses established patterns (wait states, timeouts, compensation, escalation, signals, data store)
- **LLM-friendly**: the agent sees strategies (`skip`, `force`, `escalate`) instead of dead ends
- **Traceable**: all resolutions are logged in `WorkflowState.conflicts` and `TraceGraph` with typed events
- **Decoupled**: Signal Events allow nodes to react without tight coupling
- **Safe rollback**: Compensation Handlers provide structured undo for side-effects
- **Typed context**: Process Data Store validates provides/needs at graph load time, reducing runtime context errors
- **Pure domain**: `Spec` entity no longer has mutation logic (`merge` moved to `SpecService`)
- **DI-compliant**: `CognitiveDeltaRepository` receives `graph_factory` from composition root, not from `engine/bootstrap`

### Negative
- **More complex SubState schema**: adds `timeout_ms`, `interrupting`, `on_timeout`, `emits`, `waits_for`, `on_signal`
- **Engine owns workflow resolution**: delta_tracking no longer controls workflow resolution (by design)
- **Migration needed**: existing workflow.json files need default values for new fields
- **SignalBus adds state**: introduces a lightweight pub/sub mechanism in the engine
- **Dual return types**: `SpringService` maintains both entity and dict APIs during migration

### Neutral
- `ConflictDetector` lives in `delta_tracking` (detects conflicts from data)
- `WorkflowConflictResolver` lives in `engine` (applies workflow resolution strategies)
- `BlockResolver` lives in `engine` (applies resolution strategies to workflow state)
- `CompensationEngine` lives in `engine` (executes side-effect compensations)
- `SignalBus` lives in `engine` (pub/sub between nodes)
- `SpecService` lives in `delta_tracking/domain/services` (pure domain operations on Spec)
- This separation respects the domain boundaries: delta describes business rules, engine executes them

---

## Alternatives Considered

| Alternative | Reason for rejection |
|-------------|----------------------|
| **Keep `.delta-conflicts.json`** | Parallel source of truth; can desync from workflow state |
| **Let LLM decide all resolutions** | No guardrails; LLM can skip critical validations |
| **Workflow auto-resolves everything** | Removes human oversight; dangerous for business processes |
| **delta_tracking owns resolution** | Mixes business rules with execution policy; violates SRP |
| **No timeouts, only manual resolution** | Sprint stays blocked forever if nobody acts |
| **External event bus** | Over-engineering; SignalBus in-engine is sufficient for walker's scope |

---

## Implementation Plan

### Phase 1 (P0): Timer/Boundary Events + BlockResolver ✅ Completed
1. Add `timeout_ms`, `interrupting`, `on_timeout` to `SubState` entities
2. Add `blocked_at`, `block_reason`, `block_since`, `pending_events`, `block_timeout_ms` to `WorkflowState`
3. Create `BlockResolver` in `engine/domain/services/block_resolver.py` returning `Result[WorkflowState, str]`
4. Integrate timeout checking into `StepOrchestrator`
5. Integrate `BlockResolver` into `SubmitOrchestrator` and `TransitionOrchestrator`
6. Update `WorkflowStateRepository` to persist new fields

### Phase 2 (P1): Signal Events + Compensation + Escalation ✅ Completed
7. Add `emits` and `waits_for` to `SubState`
8. Create `SignalBus` in `engine/domain/services/signal_bus.py`
9. Create `CompensationEngine` in `engine/domain/services/compensation_engine.py`
10. Create `EscalationNodeRegistry` in `engine/domain/services/escalation_node_registry.py`
11. Integrate signal emission in `SubmitOrchestrator`
12. Integrate signal checking in `StepOrchestrator`
13. Integrate compensation execution in `SubmitOrchestrator` and `StepOrchestrator`
14. Integrate escalation in `ErrorHandler` and `TransitionOrchestrator`
15. Log `signal` and `compensation` events to `TraceGraph`

### Phase 3 (P2): Process Data Store ✅ Completed
16. Add `process_data` to `WorkflowState`
17. Validate `WorkflowNode.provides` and `SubState.needs` at graph load time via `ContextResolver.validate_graph()`
18. Update `ContextResolver` to use `process_data` as primary store, `context` as backward-compat fallback
19. Add `SubState.from_dict()` factory for clean construction in tests and adapters

### Phase 4: Observability ✅ Completed
20. Log all resolutions to `TraceGraph` (event types: `conflict_resolved`, `timeout`, `signal`, `compensation`, `escalate`)
21. Add `conflict_resolution` metrics to `agent_context`

**Implementation status:**
- `timeout_resolved` / `timeout_resolution_failed` logged from `StepOrchestrator._check_timeout()`
- `signal_resolved` / `signal_resolution_failed` logged from `StepOrchestrator._check_pending_signals()`
- `force_resolved` logged from `SubmitOrchestrator._check_required_tools_gate()`
- `escalate_resolved` logged from `TransitionOrchestrator.transition()`
- `escalate_failed` logged from `ErrorHandler._escalate()`
- `compensation` events logged from `CompensationEngine.execute()` via `NodeExecution` record

**Delta tracking architectural improvements (post-implementation cleanup):**
- `CognitiveDeltaRepository` no longer imports from `engine/bootstrap`; receives `graph_factory` via DI
- `ConflictDetector` / `ConflictResolver` split: detection in `delta_tracking`, workflow resolution in `engine`
- `Spec.merge()` moved to `SpecService` in `domain/services/spec_service.py` (pure domain)
- `SpringService` maintains dict API for MCP/presentation, exposes entity variants for internal use
- `upsert_spec`/`upsert_specs_batch` normalize results with `{"ok": ..., "error": ...}`

---

## References

- BPMN 2.0 specification: Event-Based Gateways, Timer Boundary Events, Compensation, Escalation, Signal Events, Data Objects
- Camunda docs: "Optimistic Locking", "Dealing with problems and exceptions", "Event-Based Gateway"
- Rhize docs: "BPMN execution recovery"
- ADR-WALKER-041: Conflict detection and resolution (delta_tracking)
- ADR-WALKER-040: Engine design improvements (ports, aggregate root)
- ADR-WALKER-039: Domain purity corrections (frozen VOs, enums)
- ADR-WALKER-036: delta_tracking as SSOT/SRP for spring

---

## Open Questions (resolved during implementation)

1. **Should `on_timeout` be a string strategy or a transition target?** → String strategy implemented (simpler, more flexible).
2. **Should timeout be per-sub-state or per-node?** → Per-sub-state (more granular, matches BPMN boundary events).
3. **Should BlockResolver be in engine or delta_tracking?** → Engine is correct (execution policy), delta_tracking detects conflicts only.
4. **Should conflicts be part of WorkflowState or a separate aggregate?** → WorkflowState (simpler, proven at scale).
5. **Should the LLM be able to request a strategy?** → Yes, if pre-declared in `allowed_strategies` on the sub-state (not yet implemented in UI, but supported by model).
6. **Should SignalBus be global or per-workflow?** → Per-workflow instance, created by `OrchestrationService` and injected into orchestrators.
7. **Should compensation be optional per side-effect?** → Yes, via `optional: true` in `SideEffectDef` (already exists from ADR-040).

---

## Approval

- [x] Architecture review
- [x] Engine team
- [x] delta_tracking team
- [x] Product owner
