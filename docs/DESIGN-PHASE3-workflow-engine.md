# Design Phase 3 — Workflow Engine Migration to fast_mcp/

**Status:** DRAFT v2 — pending approval before coding
**Date:** 2026-09-01
**ADR reference:** ADR-WALKER-071 §3 Fase 3
**Changelog:** v2 applies 10 fixes from design review (see §11)

---

## 1. Objective

Migrate the workflow engine (`shared/core/engine/`, ~14k LOC), delta_tracking
(`features/delta_tracking/`, ~6.4k LOC), tasks (`shared/tasks/`, ~6.6k LOC),
and event_tracing (`features/event_tracing/`, ~1.3k LOC) to `fast_mcp/features/`
following domain-first architecture (ADR-WALKER-067).

**Exit criteria:**
- All Phase 3 tools registered and functional (~15 tools)
- E2E flow via MCP: prepare → memories → submit → transition → help
- Zero imports from `mcp/` in any Phase 3 file
- `mcp/shared/core/engine/` is no longer imported by any fast_mcp/ file
- 32+ existing tests still green

---

## 2. Rules (non-negotiable)

| # | Rule | Rationale |
|---|------|-----------|
| R-3.1 | **Domain-first**: `features/workflow/domain/` has no concrete infrastructure imports. Domain services may depend on **ports** (ABC interfaces), never on implementations. | Clean Architecture (ADR-067) |
| R-3.2 | **Ports as ABC**: engine ports live in `domain/ports.py`. Only **8 ports** are migrated initially (see §7). Infrastructure implements them. | DIP, minimal surface |
| R-3.3 | **EngineBundle is project-scoped**: constructed per `chain_dir`, NOT per server boot. Adapters (stores, tracer) are shared singletons; the bundle is project-scoped. Cached per-project. | Correctness (§1 fix #1) |
| R-3.4 | **`fast_mcp/` NEVER imports `mcp/`**: Phase 3 files import from `fast_mcp/features/`, `fast_mcp/shared_kernel/`, stdlib, and third-party libs only. | Autonomy (ADR-WALKER-071 §0 #5) |
| R-3.5 | **Composition root is the ONLY place contexts cross**: `fast_mcp/composition.py` wires `WorkflowContext`. Phase files receive pre-wired objects. | Coherence rule §3bis |
| R-3.6 | **Phases receive wired runners, never import features**: `phase3_tasks.py` registers tools via `register_domain_tool()` with runners from `composition.py`. | Coherence rule §3bis 2/3 |
| R-3.7 | **Result pattern**: all operations return `Result[T, E]` from `returns`. Exceptions are caught at the boundary (`dispatch()` wrapper). | Convention-001 |
| R-3.8 | **Immutable state**: `WorkflowState` is frozen dataclass. Every operation returns a NEW instance (ELM pattern). No mutation. | ADR-067, thread safety |
| R-3.9 | **inputSchema SSOT**: tool schemas are declarative in `phase3_tasks.py`, not inferred from function signatures. | R-03bis |
| R-3.10 | **Fail-open per phase**: if Phase 3 tools fail to register, Phase 1 and 2 continue working. | ADR-WALKER-071 §4 |
| R-3.11 | **One handler pattern**: tools use flat handler functions in `handlers/` (like Phase 2), NOT use cases + application services. Collapse ceremony during port. | Consistency (§1 fix #6) |

---

## 3. Invariants

| # | Invariant | Violation consequence |
|---|-----------|----------------------|
| INV-P3-01 | `WorkflowState.current_node` + `current_sub_state` are always consistent with `sub_state_index` | Lost state, broken transitions |
| INV-P3-02 | `WorkflowGraph.edges_by_source` is always consistent with `edges` | Gate lookup fails silently |
| INV-P3-03 | `submit` only succeeds when `required_tools` gate is satisfied (TraceGraph has recent kg_query) | Knowledge not consulted before action |
| INV-P3-04 | `transition` only crosses valid gates (source_node matches current_node, gate exists) | Invalid state jump |
| INV-P3-05 | Side effects execute atomically via saga: enqueue → execute → mark completed/failed → compensate on failure | Partial side effects on crash |
| INV-P3-06 | `ExecutionContext.session_id` is valid for the duration of one submit/transition/step cycle | Stale session references |
| INV-P3-07 | `revision` token increments on every `save_state` (optimistic concurrency) | Lost updates on concurrent submit |
| INV-P3-08 | `fast_mcp/features/workflow/` has zero imports from `mcp/shared/core/engine/` or `mcp/features/` | Violates autonomy |
| INV-P3-09 | Every tool handler returns `dict`, never raises (caught by `dispatch()`) | MCP protocol violation |

---

## 4. Dependency Order (what depends on what)

```
Phase 3 features, ordered by dependency depth:

Layer 0 (no deps):     shared_kernel/ (already ported: 5,440 LOC)
                        project/ (already ported: 481 LOC)
                        code_analysis/ (already ported: 9,184 LOC)
                        ↓
Layer 1 (domain):       features/workflow/domain/ (~5k LOC)
                        ├── entities.py (WorkflowGraph, WorkflowState, SubState, Gate)
                        ├── ports.py (8 ABC interfaces — reduced from 16)
                        ├── values.py (SubStateData, SideEffectDef, RetryPolicy)
                        ├── execution.py (ExecutionContext, TaskResult)
                        ├── events.py (WorkflowEvent)
                        ├── guards.py (ScopeGuard)
                        └── services/ (advance, block_resolver, transition, validation)
                        ↓
Layer 2 (application):  features/workflow/application/ (~3k LOC — simplified)
                        ├── bundle.py (EngineBundle — project-scoped VO)
                        ├── orchestrator.py (OrchestrationService)
                        ├── task_dispatcher.py (execute_public_task)
                        └── state_manager.py (state persistence)
                        ↓
Layer 3 (infra):        features/workflow/infrastructure/ (~2k LOC)
                        ├── workflow_store.py (IWorkflowStore adapter)
                        ├── side_effects.py (ISideEffectEngine — no-op stub)
                        ├── trace_log.py (ITracer adapter)
                        └── adapters/
                            └── default_workflow_io.py (IWorkflowIO)
                        ↓
Layer 4 (handlers):     features/workflow/handlers/ (~500 LOC)
                        ├── __init__.py (registry + dispatch)
                        └── workflow.py (handle_submit, handle_transition, handle_help)
                        ↓
Layer 5 (composition):  composition.py (WorkflowContext wiring)
                        ↓
Layer 6 (surface):      phases/phase3_tasks.py (tool registration)
```

**Critical path:** Layer 1 → Layer 2 → Layer 3 → Layer 4 → Layer 5 → Layer 6

**What's NOT ported in Phase 3:**
- `delta_tracking/` (6,428 LOC) — deferred to Phase 3.1 (uses ISprintService port)
- `event_tracing/` (1,324 LOC) — deferred to Phase 3.1 (uses ITracer port adapter)
- `shared/tasks/` (6,643 LOC) — deferred to Phase 3.1 (task registry + side effects)
- `context/` (1,256 LOC) — DI container, replaced by composition.py wiring

**Rationale:** These are ~14k LOC of feature code that depends on the engine ports. Port them AFTER the engine core works, using the port interfaces as the contract.

---

## 5. Directory Structure

```
fast_mcp/features/workflow/
├── __init__.py                          # Lazy facade (PEP 562)
├── _composition.py                      # WorkflowContext factory
│
├── domain/                              # LAYER 1: Domain (ports + entities + services)
│   ├── __init__.py
│   ├── entities.py                      # WorkflowGraph, WorkflowState, SubState, Gate, NodeExecution
│   ├── ports.py                         # 8 ABC interfaces (reduced from 16)
│   ├── values.py                        # SubStateData, SideEffectDef, RetryPolicy, etc.
│   ├── execution.py                     # ExecutionContext, TaskResult
│   ├── events.py                        # WorkflowEvent
│   ├── guards.py                        # ScopeGuard (visibility)
│   ├── errors.py                        # WorkflowError hierarchy
│   └── services/                        # Domain services (may depend on ports)
│       ├── __init__.py
│       ├── advance.py                   # Sub-state advancement logic
│       ├── block_resolver.py            # Block/unblock resolution strategies
│       ├── transition.py                # Transition validation logic
│       └── validation.py                # Input validation rules
│
├── application/                         # LAYER 2: Simplified application layer
│   ├── __init__.py
│   ├── bundle.py                        # EngineBundle (project-scoped VO)
│   ├── orchestrator.py                  # OrchestrationService (main entry)
│   ├── task_dispatcher.py               # execute_public_task (bridge)
│   └── state_manager.py                 # State persistence orchestration
│
├── infrastructure/                      # LAYER 3: Port adapters (only needed ports)
│   ├── __init__.py
│   ├── workflow_store.py                # IWorkflowStore adapter
│   ├── side_effects.py                  # ISideEffectEngine (no-op stub)
│   ├── trace_log.py                     # ITracer adapter
│   └── adapters/
│       └── default_workflow_io.py       # IWorkflowIO adapter
│
└── handlers/                            # LAYER 4: Tool handlers (flat, like Phase 2)
    ├── __init__.py                      # Registry + dispatch
    └── workflow.py                      # handle_submit, handle_transition, handle_help
```

**Estimated LOC:** ~10k (domain ~5k + application ~3k + infra ~2k)

**What was removed vs original engine (§1 fix #4):**

| Original (mcp/) | Ported to fast_mcp/ | Simplification |
|-----------------|---------------------|----------------|
| `domain/entities.py` (903 LOC) | `domain/entities.py` (~600 LOC) | Remove `generate_flows()` (CLI-only), simplify `SubState.from_dict()` |
| `domain/ports.py` (525 LOC, 16 ports) | `domain/ports.py` (~200 LOC, 8 ports) | Defer 8 ports (see §7) |
| `domain/services/` (9 files, ~1.2k LOC) | `domain/services/` (4 files, ~500 LOC) | Defer compensation_engine, conflict_resolver, escalation_registry, signal_bus, field_resolver |
| `application/` (26 files, 6.3k LOC) | `application/` (4 files, ~1k LOC) | Collapse orchestrators into OrchestrationService; remove prompt_service, event_dispatch_service, workflow_map, workflow_loader, error_handler, context_resolver |
| `infrastructure/` (13 files, 2.9k LOC) | `infrastructure/` (4 files, ~500 LOC) | Only needed adapters; defer session_store, config, engine_metrics, task registries |
| `context/` (7 files, 1.3k LOC) | **Deleted** | Replaced by composition.py wiring |
| `bootstrap.py` (140 LOC) | **Deleted** | Replaced by composition.py |

---

## 6. What Gets Deleted (§1 fix #5)

After Phase 3 is green, these become redundant:

| From | What | Why |
|------|------|-----|
| `fast_mcp/phases/phase3_tasks.py` (old stub) | `help` and `list_projects` implementations | Replaced by new handlers that use the engine |
| `mcp/shared/core/engine/` | No longer imported by fast_mcp/ | All engine logic lives in fast_mcp/features/workflow/ |
| `mcp/shared/tasks/actions/workflow_tools.py` | Task registration for submit/transition/help | Replaced by phase3_tasks.py registration |

**What stays in mcp/ (Phase 4):** Nothing. The goal is to eliminate `mcp/` entirely.

---

## 7. Port Mapping (§1 fix #2: reduced from 16 to 8)

### Ports ported in Phase 3 (needed by submit/transition/status)

| Port | Purpose | fast_mcp/ Adapter | LOC |
|------|---------|-------------------|-----|
| `IWorkflowStore` | Load/save graph + state | `workflow/infrastructure/workflow_store.py` | ~80 |
| `ITracer` | Trace events | `workflow/infrastructure/trace_log.py` | ~60 |
| `ISprintService` | Load tasks, get spring | Stub (returns empty) | ~30 |
| `IConflictResolver` | Resolve transition conflicts | Stub (returns no conflict) | ~20 |
| `IWorkflowIO` | Run gate validation scripts | `workflow/infrastructure/adapters/default_workflow_io.py` | ~50 |
| `ISideEffectEngine` | Execute compensation handlers | `workflow/infrastructure/side_effects.py` (no-op) | ~20 |
| `IProjectContext` | Resolve project paths | Reuse `project/` feature (already ported) | 0 |
| `IStateStore` | Load/save WorkflowState | Merge into `IWorkflowStore` (same adapter) | 0 |

**Total: 8 ports, ~260 LOC of adapters.**

### Ports deferred (Phase 3.1 or never)

| Port | Why deferred | When needed |
|------|-------------|-------------|
| `ISessionStore` | MCP is stateless; session lifecycle not needed | Phase 3.1 (if session-aware features added) |
| `IPromptCollector` | No prompt system in fast_mcp yet | Phase 4 (if prompts added) |
| `IPresenter` | MCP returns dicts, not formatted output | Never (MCP protocol replaces CLI formatting) |
| `IConfig` | Only for session_dir path; can hardcode | Phase 3.1 if needed |
| `IContextValidator` | Graph validation is optional; fail-open | Phase 3.1 if needed |
| `IEngineMetrics` | Observability only; no functional impact | Phase 3.1 if needed |
| `ICpgAnalysisPort` | Already exists in `code_analysis/` | Reuse existing |
| `ISideEffectOutbox` | Part of saga internals; not used by tools | Phase 3.1 if saga needed |

---

## 8. Simplification During Port (§1 fix #4)

### Domain Layer

| Original | Ported as | Change |
|----------|-----------|--------|
| `entities.py` (903 LOC) | `entities.py` (~600 LOC) | Remove `generate_flows()` (CLI-only), simplify `SubState.from_dict()` |
| `ports.py` (525 LOC, 16 ports) | `ports.py` (~200 LOC, 8 ports) | 8 ports deferred |
| `services/compensation_engine.py` (169 LOC) | **Deleted** | No-op stub in infrastructure |
| `services/conflict_resolver.py` (60 LOC) | **Deleted** | Stub returns no conflict |
| `services/escalation_registry.py` (43 LOC) | **Deleted** | Not needed for MCP tools |
| `services/signal_bus.py` (77 LOC) | **Deleted** | Not needed for MCP tools |
| `services/field_resolver.py` (63 LOC) | **Deleted** | Inlined into advance.py |
| `services/block_resolver.py` (286 LOC) | ~100 LOC | Simplify: only "force" strategy for MCP |
| `contract_validator.py` (443 LOC) | **Deleted** | Pre/post contracts deferred |
| `graph_analyzer.py` (157 LOC) | **Deleted** | Flow generation is CLI-only |
| `session.py` (91 LOC) | **Deleted** | MCP is stateless |
| `workflow_action.py` (141 LOC) | ~50 LOC | Simplify: ActionCatalog as thin registry |

### Application Layer

| Original | Ported as | Change |
|----------|-----------|--------|
| `orchestrator.py` (645 LOC) | ~300 LOC | Collapse 4 sub-orchestrators into methods |
| `submit_orchestrator.py` (471 LOC) | Merged into orchestrator | Method, not separate class |
| `transition_orchestrator.py` (175 LOC) | Merged into orchestrator | Method, not separate class |
| `step_orchestrator.py` (427 LOC) | **Deleted** | Auto/llm_task execution deferred |
| `status_orchestrator.py` (180 LOC) | Merged into orchestrator | Method, not separate class |
| `workflow_api.py` (405 LOC) | **Deleted** | Session management not needed for MCP |
| `task_dispatcher.py` (77 LOC) | ~40 LOC | Simplify: direct dispatch, no ScopeGuard |
| `use_cases/` (6 files, ~1.4k LOC) | **Deleted** | Collapsed into orchestrator methods |
| `prompt_service.py` (151 LOC) | **Deleted** | No prompts in fast_mcp |
| `error_handler.py` (375 LOC) | **Deleted** | Fail-open at dispatch boundary |
| `side_effect_saga.py` (341 LOC) | **Deleted** | No-op stub; saga deferred |
| `context_resolver.py` (317 LOC) | **Deleted** | Context validation deferred |

---

## 9. Integration Mechanisms

### 9.1 Composition Root Wiring

```python
# fast_mcp/composition.py — WorkflowContext

@dataclass(frozen=True)
class WorkflowContext:
    """Pre-wired workflow engine dependencies (project-scoped)."""
    bundle: EngineBundle
    orchestrator: OrchestrationService
    task_registry: Any  # ActionCatalog

def build_workflow_context(chain_dir: str) -> WorkflowContext:
    """Wire the workflow engine for a given project.
    
    Adapters are shared singletons; the bundle is project-scoped.
    Cached per-chain_dir to avoid re-construction on every tool call.
    """
    # Shared adapters (singletons)
    workflow_store = WorkflowStoreAdapter()
    tracer = TraceLogger()
    workflow_io = DefaultWorkflowIO()
    side_effects = NoOpSideEffectEngine()

    # Project-scoped bundle
    bundle = EngineBundle(
        workflow_store=workflow_store,
        tracer=tracer,
        workflow_io=workflow_io,
        side_effects=side_effects,
        chain_dir=chain_dir,
    )

    # Orchestrator (thin, delegates to domain services)
    orch = OrchestrationService(bundle=bundle)

    return WorkflowContext(bundle=bundle, orchestrator=orch, task_registry=None)
```

### 9.2 Tool Registration (Phase 3)

```python
# fast_mcp/phases/phase3_tasks.py

_FASE3_TOOLS = (
    "submit", "transition", "help",
    "sprint_planning", "resolve_project_sprint",
    "implement_task", "run_tests", "verify_predicates",
    "review_code", "capture", "refine", "research_topic",
    "trace_push", "intelligence",
    "list_projects", "context_gathering", "update_project",
)
```

### 9.3 Handler Pattern (flat, like Phase 2)

```python
# fast_mcp/features/workflow/handlers/__init__.py

_HANDLER_IMPORTS = {
    "submit": ("fast_mcp.features.workflow.handlers.workflow", "handle_submit"),
    "transition": ("fast_mcp.features.workflow.handlers.workflow", "handle_transition"),
    "help": ("fast_mcp.features.workflow.handlers.workflow", "handle_help"),
    # ...
}

def dispatch(name: str, ctx: WorkflowContext, **kwargs) -> dict:
    handler = get_handler(name)
    if handler is None:
        return {"error": f"Unknown workflow tool: {name}"}
    try:
        return handler(ctx, **kwargs)
    except Exception as e:
        return {"error": str(e), "tool": name}
```

### 9.4 Coupling Map (§1 fix #9: cross-feature deps explicit)

```
┌─────────────────────────────────────────────────────────┐
│  phases/phase3_tasks.py                                 │
│  (registers tools, no feature imports)                  │
└──────────────────────┬──────────────────────────────────┘
                       │ receives wired runners
┌──────────────────────▼──────────────────────────────────┐
│  composition.py                                         │
│  (ONLY place contexts cross)                            │
│  build_workflow_context() → WorkflowContext              │
└──────┬───────────────┬──────────────────┬───────────────┘
       │               │                  │
┌──────▼──────┐ ┌──────▼──────┐ ┌─────────▼────────┐
│  workflow/  │ │  project/   │ │  code_analysis/  │
│  domain +   │ │  (already   │ │  (already        │
│  application│ │   ported)   │ │   ported)        │
│  + infra    │ │             │ │                  │
└──────┬──────┘ └─────────────┘ └──────────────────┘
       │
       │ ports (IProjectContext, ICpgAnalysisPort)
       │
┌──────▼──────────────────────────────────────────────┐
│  shared_kernel/ (already ported: 5,440 LOC)         │
│  ├── cognitive_graph/ (CognitiveGraph, Index, ODM)  │
│  ├── project_support/ (ProjectService, paths)       │
│  └── paths.py, config.py                           │
└────────────────────────────────────────────────────┘
```

**Cross-feature dependencies (explicit):**
- `workflow/` → `project/` via `IProjectContext` port (resolve paths, list projects)
- `workflow/` → `code_analysis/` via `ICpgAnalysisPort` port (CPG queries)
- `workflow/` → `shared_kernel/` for CognitiveGraph access
- `workflow/` → `delta_tracking/` ❌ (deferred; ISprintService returns stub)
- `workflow/` → `event_tracing/` ❌ (deferred; ITracer uses local adapter)

---

## 10. Migration Order (§1 fix #7: corrected sprint order)

### Sprint 1: Domain Layer (no IO)
1. Port `domain/entities.py` (simplified: ~600 LOC)
2. Port `domain/ports.py` (8 ports: ~200 LOC)
3. Port `domain/values.py`, `domain/execution.py`, `domain/events.py`
4. Port `domain/guards.py`, `domain/errors.py`
5. Port `domain/services/` (4 files: advance, block_resolver, transition, validation)
6. **Test:** domain unit tests (no infrastructure)
7. **LOC:** ~2,000

### Sprint 2: Application Layer (uses ports, no infra)
1. Port `application/bundle.py` (EngineBundle — project-scoped)
2. Port `application/orchestrator.py` (collapsed: ~300 LOC)
3. Port `application/task_dispatcher.py` (simplified: ~40 LOC)
4. Port `application/state_manager.py` (~50 LOC)
5. **Test:** orchestrator unit tests with mocked ports
6. **LOC:** ~500

### Sprint 3: Infrastructure Adapters (implements ports)
1. Port `infrastructure/workflow_store.py` (IWorkflowStore adapter)
2. Port `infrastructure/side_effects.py` (no-op stub)
3. Port `infrastructure/trace_log.py` (ITracer adapter)
4. Port `infrastructure/adapters/default_workflow_io.py` (IWorkflowIO)
5. **Test:** integration tests with real filesystem
6. **LOC:** ~500

### Sprint 4: Handlers + Composition
1. Create `handlers/__init__.py` (registry + dispatch)
2. Create `handlers/workflow.py` (handle_submit, handle_transition, handle_help)
3. Wire `WorkflowContext` in `composition.py`
4. Register all tools in `phase3_tasks.py`
5. **Test:** E2E MCP tests (prepare → submit → transition → help)
6. **LOC:** ~500

### Sprint 5: Feature Ports (deferred)
1. Port `delta_tracking/` → `fast_mcp/features/delta_tracking/` (ISprintService adapter)
2. Port `event_tracing/` → `fast_mcp/features/event_tracing/` (ITracer adapter)
3. Port `shared/tasks/` → `fast_mcp/shared/tasks/` (task registry + side effects)
4. **Test:** feature unit tests
5. **LOC:** ~5k (after simplification)

---

## 11. Design Review — 10 Fixes Applied (§1)

| # | Issue | Fix Applied |
|---|-------|-------------|
| 1 | INV-P3-10 wrong: bundle is not boot-scoped | Fixed: bundle is project-scoped, cached per chain_dir (R-3.3) |
| 2 | 16 ports is enterprise ceremony | Reduced to 8 ports; 8 deferred (§7) |
| 3 | Domain services are not "pure IO-free" | Clarified: domain services may depend on ports, not implementations (R-3.1) |
| 4 | Port-as-is contradicts cleanup goals | Added §8: simplification table for every layer |
| 5 | Missing "what gets deleted" | Added §6: deletion list + Phase 4 scope |
| 6 | Handler pattern inconsistent | Fixed R-3.11: one pattern (flat handlers), not use cases + handlers |
| 7 | Sprint order wrong (DIP violation) | Fixed: domain → application → infrastructure (§10) |
| 8 | LOC estimate wrong (12k vs 28k) | Fixed: ~10k for Phase 3 core (§5); ~28k total including deferred features |
| 9 | Coupling map incomplete | Added cross-feature deps: project/, code_analysis/, shared_kernel/ (§9.4) |
| 10 | Missing failure_scenarios | Added §12 below |

---

## 12. failure_scenarios

```json
[
  {
    "fallo": "EngineBundle no puede construirse porque IWorkflowStore no está registrado",
    "causa_raiz": "composition.py no cablea el adapter antes de construir el bundle",
    "mitigacion": "build_workflow_context() construye los adapters inline; no depende de registration previa",
    "deteccion": "test de composition: build_workflow_context(chain_dir) no lanza"
  },
  {
    "fallo": "submit falla porque ISprintService retorna tasks vacías (stub)",
    "causa_raiz": "delta_tracking no está portado aún; el stub no tiene tasks reales",
    "mitigacion": "submit acepta payload sin tasks; el gate required_tools se omite cuando no hay tasks",
    "deteccion": "test_e2e: submit con payload vacío no lanza, retorna ok con warning"
  },
  {
    "fallo": "WorkflowState.from_dict falla al cargar estado del disco (formato legacy)",
    "causa_raiz": "archivos .workflow-state.json del mcp/ usan formato v4, no v5",
    "mitigacion": "from_dict acepta campos faltantes con defaults; reconcile_index corrige inconsistencias",
    "deteccion": "test de roundtrip: to_dict → from_dict → to_dict produces same output"
  },
  {
    "fallo": "transition falla porque la gate no existe en el grafo cargado",
    "causa_raiz": "workflow.json del proyecto no tiene la gate declarada",
    "mitigacion": "OrchestrationService valida la gate antes de aplicar; retorna error descriptivo",
    "deteccion": "test: transition con gate inexistente retorna {error: 'Gate not found'}"
  },
  {
    "fallo": "Handler lanza excepción y FastMCP la propaga al agente como error crudo",
    "causa_raiz": "dispatch() no captura la excepción",
    "mitigacion": "dispatch() envuelve cada handler en try/except; retorna {error: str(e)}",
    "deteccion": "test: handler que lanza retorna dict con 'error' key, no exception"
  },
  {
    "fallo": "ISideEffectEngine no-op stub permite side effects a ejecutarse en producción",
    "causa_raiz": "el stub no compensa; side effects reales se ejecutan sin compensación",
    "mitigacion": "el stub registra el side effect como 'skipped' en el log; no ejecuta nada real",
    "deteccion": "test: side_effects.run() retorna Result.success con skipped=True"
  },
  {
    "fallo": "Circular import entre workflow/ y project/ al resolver IProjectContext",
    "causa_raiz": "workflow/__init__.py importa project/ al construir el bundle",
    "mitigacion": "IProjectContext se inyecta via composition.py; workflow/ solo conoce el puerto, no la implementación",
    "deteccion": "test de autonomía: workflow/ no importa project/ directamente"
  },
  {
    "fallo": "Phase 3 tools no se registran porque composition.py falla al construir WorkflowContext",
    "causa_raiz": "algun adapter lanza en __init__ (filesystem no disponible)",
    "mitigacion": "fail-open: phase3_tasks.py envuelve install() en try/except; Phase 1+2 siguen funcionando",
    "deteccion": "test: server arranca con 17+ tools aunque Phase 3 falle"
  },
  {
    "fallo": "OrchestrationService es un god object (645 LOC en mcp/) que se portó sin simplificar",
    "causa_raiz": "port as-is sin colapsar sub-orchestrators",
    "mitigacion": "OrchestrationService colapsado a ~300 LOC; submit/transition/status son métodos, no clases separadas",
    "deteccion": "metrica: OrchestrationService < 350 LOC"
  },
  {
    "fallo": "domain/services/dependen de puertos pero el test de domain no puede inyectarlos",
    "causa_raiz": "domain services llaman resolve_port() que necesita infrastructure registrada",
    "mitigacion": "tests de domain inyectan mocks directamente en los puertos; no usan resolve_port()",
    "deteccion": "test: domain services aceptan ports por constructor, no por resolve_port()"
  }
]
```

---

## 13. Success Criteria

- [ ] `fast_mcp/features/workflow/` has zero imports from `mcp/`
- [ ] 8 engine ports have fast_mcp adapters (not 16)
- [ ] `EngineBundle` is project-scoped, constructs per chain_dir
- [ ] `OrchestrationService` is < 350 LOC (collapsed from 645)
- [ ] `submit` tool works end-to-end (prepare → submit → state persisted)
- [ ] `transition` tool works end-to-end (submit → transition → new node)
- [ ] `help` tool returns all registered tools
- [ ] `list_projects` tool returns projects from registry
- [ ] 32+ existing tests still green
- [ ] New Phase 3 tests: 10+ (domain, integration, E2E)
- [ ] Server starts with 19+ tools (3 Phase 1 + 14 Phase 2 + Phase 3)
- [ ] LOC of workflow/ feature < 10k (not 16k as-is)
