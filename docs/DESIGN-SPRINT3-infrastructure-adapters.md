# Design Sprint 3 — Infrastructure Adapters

**Status:** DRAFT — pending approval before coding
**Date:** 2026-09-01
**ADR reference:** DESIGN-PHASE3-workflow-engine.md §10 Sprint 3
**Depends on:** Sprint 1 (domain) ✅ + Sprint 2 (application) ✅

---

## 1. Objective

Port 4 infrastructure adapters from `mcp/shared/core/engine/infrastructure/` to `fast_mcp/features/workflow/infrastructure/`. These adapters implement the 8 domain ports and connect the engine to real I/O (filesystem, subprocess, CognitiveGraph).

**Exit criteria:**
- `NoopTracer` and `NoOpSideEffectEngine` work (stubs for ports not yet connected)
- `DefaultWorkflowIO` executes gate validation scripts
- `WorkflowStoreAdapter` persists WorkflowState to `.workflow-state.json`
- `WorkflowGraphRepository` loads `workflow.json` → `WorkflowGraph`
- Integration tests with real filesystem (temp dirs)
- Zero imports from `mcp/`

---

## 2. What gets ported vs deferred

### Ported (needed for submit/transition/status)

| Original file | LOC | fast_mcp/ target | LOC | Simplification |
|---------------|-----|-------------------|-----|----------------|
| `trace_log.py` (NoopTracer) | ~30 | `infrastructure/noop_tracer.py` | ~30 | As-is (no-op) |
| `side_effects.py` (NoOpSideEffectEngine) | ~5 | `infrastructure/noop_side_effects.py` | ~10 | Simpler stub |
| `adapters/default_workflow_io.py` | 65 | `infrastructure/default_workflow_io.py` | ~55 | Remove `get_sub_state` (domain handles it) |
| `workflow_store.py` (adapter) | 40 | `infrastructure/workflow_store_adapter.py` | ~30 | Thin delegation |
| `repositories.py` (WorkflowStateRepository) | ~120 | `infrastructure/state_repository.py` | ~80 | Simplified: no reconcile (orchestrator handles), no CAS (orchestrator bumps) |
| `repositories.py` (WorkflowRepository) | ~200 | `infrastructure/graph_repository.py` | ~150 | Simplified: no ContextResolver, no ValidationService.verify_graph, no legacy import format |

**Total: ~355 LOC** (was ~460 in originals)

### Deferred (not needed for MCP tools)

| Original file | LOC | Why deferred |
|---------------|-----|-------------|
| `trace_log.py` (TraceLogger) | ~220 | Requires CognitiveGraph + PathResolver; use NoopTracer until event_tracing is ported (Sprint 5) |
| `side_effects.py` (SideEffectEngine) | ~500 | Saga pattern + compensation; tasks not ported yet (Sprint 5) |
| `repositories.py` (ScriptRunner) | ~60 | Merged into DefaultWorkflowIO |
| `config.py` | ~40 | Deferred; paths resolved by composition.py |
| `task_registry.py` | ~100 | Deferred; tasks not ported (Sprint 5) |
| `system_task_registry.py` | ~100 | Deferred |
| `domain_task_registry.py` | ~80 | Deferred |
| `session_store.py` | ~80 | Deferred; MCP is stateless |
| `engine_metrics.py` | ~50 | Deferred; observability only |
| `side_effect_outbox.py` | ~60 | Deferred; saga outbox deferred |

**Total deferred: ~720 LOC**

---

## 3. Architecture

### 3.1 NoopTracer (~30 LOC)

```python
# fast_mcp/features/workflow/infrastructure/noop_tracer.py

class NoopTracer(ITracer):
    """No-op tracer: all trace methods are silent. Used until event_tracing is ported."""
    def trace(self, chain_dir, event_type, data=None, task_id=None, session_id=None): pass
    def trace_event(self, event): pass
    def trace_submit(self, chain_dir, sub_state, decision, evidence, task_id=None): pass
    def trace_transition(self, chain_dir, gate, from_node, to_node, task_id=None): pass
    def trace_error(self, chain_dir, error_type, message, task_id=None): pass
    def has_kg_query(self, chain_dir, task_id="", session_id="", layers=None): return False
```

**No simplification needed** — the original NoopTracer is already minimal.

### 3.2 NoOpSideEffectEngine (~10 LOC)

```python
# fast_mcp/features/workflow/infrastructure/noop_side_effects.py

class NoOpSideEffectEngine(ISideEffectEngine):
    """Stub: logs compensation attempts, executes nothing."""
    def run_compensation_handler(self, handler_name, ctx):
        return {"ok": True, "skipped": True, "handler": handler_name}
```

**Simplification vs original:** The original `SideEffectEngine` (500 LOC) has Saga pattern, idempotency, outbox, handler registry. We replace with a no-op stub. The real engine will be ported when tasks are ported (Sprint 5).

### 3.3 DefaultWorkflowIO (~55 LOC)

```python
# fast_mcp/features/workflow/infrastructure/default_workflow_io.py

class DefaultWorkflowIO(IWorkflowIO):
    """Execute gate validation scripts via subprocess."""
    def run_script(self, script, chain_dir=None):
        # subprocess.run with timeout, interpolation, env vars
    def eprint(self, *args, **kwargs):
        print(*args, file=sys.stderr, **kwargs)
```

**Simplification vs original:** Remove `get_sub_state` method (domain handles graph navigation). Remove `task_id` parameter from `run_script` (simplify). Keep subprocess execution + timeout + env vars.

### 3.4 WorkflowStoreAdapter (~30 LOC)

```python
# fast_mcp/features/workflow/infrastructure/workflow_store_adapter.py

class WorkflowStoreAdapter(IWorkflowStore):
    """Unified adapter: delegates to GraphRepository + StateRepository."""
    def __init__(self, graph_repo, state_repo):
        self._graph_repo = graph_repo
        self._state_repo = state_repo
    def load_graph(self): return self._graph_repo.load()
    def load_state(self, chain_dir): return self._state_repo.load(chain_dir)
    def save_state(self, chain_dir, state): return self._state_repo.save(chain_dir, state)
```

**Simplification vs original:** Identical structure but receives repos via constructor (no defaults that import legacy config).

### 3.5 StateRepository (~80 LOC)

```python
# fast_mcp/features/workflow/infrastructure/state_repository.py

class StateRepository:
    """Load/save WorkflowState from .workflow-state.json."""
    STATE_FILE = ".workflow-state.json"
    def load(self, chain_dir: str) -> WorkflowState: ...
    def save(self, chain_dir: str, state: WorkflowState) -> WorkflowState: ...
```

**Simplification vs original:**
- Reconcile on load (Decision 3 — always consistent)
- CAS on save (Decision 1 — check + increment)
- No `returns.result.safe` wrapping (exceptions propagate to orchestrator)
- `save` returns the saved state (with incremented revision)
- Requires GraphRepository reference for reconciliation

**Key design decision (Decision 1 — CAS):** The repository does CAS: checks `state.revision` matches stored revision, then increments. The orchestrator does NOT touch revision. If mismatch → `StateConflictError`. This prevents lost updates on concurrent submits.

### 3.6 GraphRepository (~150 LOC)

```python
# fast_mcp/features/workflow/infrastructure/graph_repository.py

class GraphRepository:
    """Load WorkflowGraph from workflow.json."""
    def __init__(self, workflow_path: str): ...
    def load(self) -> WorkflowGraph: ...
```

**Simplification vs original:**
- Keep `_resolve_imports()` — the real workflow.json uses modular format (Decision 2)
- Keep `_build_graph()` — parses JSON into SubState/Gate/WorkflowNode/WorkflowGraph
- Run `graph.validate_invariants()` on load (Decision 4 — catch errors early)
- No `ContextResolver` validation (deferred)
- No `ValidationService.verify_graph()` (deferred)
- No legacy fallback paths

**What stays:** `_resolve_imports()` + `_build_graph()` + `validate_invariants()`. This is the core value of the repository.

**What's removed:**
- `ContextResolver().validate_graph()` — semantic validation deferred
- `ValidationService.verify_graph()` — structural verification deferred
- Legacy fallback paths

---

## 4. Directory Structure

```
fast_mcp/features/workflow/infrastructure/
├── __init__.py                    # exports
├── noop_tracer.py                 # NoopTracer (ITracer stub)
├── noop_side_effects.py           # NoOpSideEffectEngine (ISideEffectEngine stub)
├── default_workflow_io.py         # DefaultWorkflowIO (IWorkflowIO)
├── workflow_store_adapter.py      # WorkflowStoreAdapter (IWorkflowStore)
├── state_repository.py            # StateRepository (WorkflowState persistence)
└── graph_repository.py            # GraphRepository (WorkflowGraph loading)
```

---

## 5. Invariants

| # | Invariant | Violation |
|---|-----------|-----------|
| INV-S3-01 | `StateRepository.save` does CAS: checks revision match, increments on save | Lost updates on concurrent submit |
| INV-S3-02 | `GraphRepository.load` raises `FileNotFoundError` if workflow.json missing | Silent empty graph |
| INV-S3-03 | `DefaultWorkflowIO.run_script` times out after 120s | Hung gate validation |
| INV-S3-04 | `NoopTracer` methods never raise (fail-open) | Tracing kills engine |
| INV-S3-05 | `NoOpSideEffectEngine.run_compensation_handler` returns Result, not None | Domain expects Result |
| INV-S3-06 | All adapters import from `fast_mcp/` only, never `mcp/` | Autonomy violation |

---

## 6. Composition Root Wiring

```python
# fast_mcp/composition.py — WorkflowContext (Sprint 3 additions)

def build_workflow_context(chain_dir: str) -> WorkflowContext:
    """Wire the workflow engine for a given project."""
    from fast_mcp.features.workflow.infrastructure.graph_repository import GraphRepository
    from fast_mcp.features.workflow.infrastructure.state_repository import StateRepository
    from fast_mcp.features.workflow.infrastructure.workflow_store_adapter import WorkflowStoreAdapter
    from fast_mcp.features.workflow.infrastructure.noop_tracer import NoopTracer
    from fast_mcp.features.workflow.infrastructure.noop_side_effects import NoOpSideEffectEngine
    from fast_mcp.features.workflow.infrastructure.default_workflow_io import DefaultWorkflowIO

    # Resolve workflow_path from chain_dir (composition responsibility)
    workflow_path = _resolve_workflow_path(chain_dir)

    # Repositories (stateless, can be shared)
    graph_repo = GraphRepository(workflow_path)
    state_repo = StateRepository()

    # Unified adapter
    store = WorkflowStoreAdapter(graph_repo, state_repo)

    # Stubs (until event_tracing + tasks are ported)
    tracer = NoopTracer()
    side_effects = NoOpSideEffectEngine()
    workflow_io = DefaultWorkflowIO()

    # Project-scoped bundle
    bundle = EngineBundle(
        workflow_store=store,
        workflow_io=workflow_io,
        tracer=tracer,
        side_effects=side_effects,
        chain_dir=chain_dir,
    )

    orch = OrchestrationService(bundle=bundle)
    return WorkflowContext(bundle=bundle, orchestrator=orch)
```

---

## 7. Test Plan

| Test | What it covers | Type |
|------|---------------|------|
| `test_noop_tracer_never_raises` | All methods are silent | Unit |
| `test_noop_side_effects_returns_skipped` | run_compensation_handler returns Result | Unit |
| `test_default_workflow_io_run_script` | Executes echo, captures output | Integration (subprocess) |
| `test_default_workflow_io_timeout` | Sleep > timeout returns error | Integration |
| `test_default_workflow_io_eprint` | Prints to stderr | Unit |
| `test_state_repository_load_empty` | Missing file → empty state | Integration (tempdir) |
| `test_state_repository_roundtrip` | save → load → same state | Integration (tempdir) |
| `test_graph_repository_load_real` | Load walker workflow.json | Integration |
| `test_graph_repository_missing_file` | FileNotFoundError raised | Integration |
| `test_graph_repository_builds_entities` | SubStates, Gates, Nodes correct | Integration |
| `test_store_adapter_delegates` | load_graph/load_state/save_state delegate | Unit (mocks) |
| `test_no_mcp_imports` | Zero mcp/ imports in infrastructure/ | Architecture |

**LOC estimate:** ~200 (tests with temp dirs, no mocks needed for repos)

---

## 8. failure_scenarios

```json
[
  {
    "fallo": "StateRepository.save escribe archivo corrupto (ctrl+C a mitad)",
    "causa_raiz": "Escritura no atómica: open('w') truncado + exception",
    "mitigacion": "Escribir a .tmp y rename atómico (os.replace) al final",
    "deteccion": "test: kill process mid-write → archivo original intacto"
  },
  {
    "fallo": "GraphRepository.load recibe workflow.json con formato inválido",
    "causa_raiz": "JSON malformado o campos requeridos faltantes",
    "mitigacion": " raises JSONDecodeError / KeyError con mensaje descriptivo",
    "deteccion": "test: JSON incompleto → exception con path al archivo"
  },
  {
    "fallo": "DefaultWorkflowIO.run_script ejecuta script infinito",
    "causa_raiz": "Script no retorna (loop infinito, deadlock)",
    "mitigacion": "timeout=120s en subprocess.run; mata el proceso",
    "deteccion": "test: 'sleep 999' → timeout error after 2s (mocked)"
  },
  {
    "fallo": "NoOpSideEffectEngine.run_compensation_handler retorna None en vez de Result",
    "causa_raiz": "Dominio espera Result[T, E], stub retorna None",
    "mitigacion": "Stub retorna Success({skipped: True}) —始终 Result",
    "deteccion": "test: isinstance(result, Result)"
  },
  {
    "fallo": "WorkflowStoreAdapter no crea directorio al guardar state",
    "causa_raiz": "chain_dir no existe en disco",
    "mitigacion": "StateRepository.save usa os.makedirs(exist_ok=True)",
    "deteccion": "test: save a directorio nuevo → archivo creado"
  },
  {
    "fallo": "GraphRepository carga workflow.json de un proyecto que no tiene workflow",
    "causa_raiz": "Proyecto sin workflow.json (proyecto vacío)",
    "mitigacion": "FileNotFoundError propagado; orchestrator retorna {ok: false}",
    "deteccion": "test: path inexistente → FileNotFoundError"
  }
]
```

---

## 9. Success Criteria

- [ ] `NoopTracer` implements all 6 ITracer methods (silent, no-op)
- [ ] `NoOpSideEffectEngine` implements `run_compensation_handler` → Result
- [ ] `DefaultWorkflowIO.run_script` executes scripts with timeout
- [ ] `WorkflowStoreAdapter` delegates to repos correctly
- [ ] `StateRepository` roundtrips WorkflowState via `.workflow-state.json`
- [ ] `GraphRepository` loads workflow.json → WorkflowGraph with entities
- [ ] `StateRepository.save` does CAS (check revision + increment) + atomic write
- [ ] 12+ integration tests pass with real filesystem
- [ ] Zero imports from `mcp/` in any Sprint 3 file
- [ ] `orchestrator.py` updated: remove `_bump_revision()` (CAS moved to repository)
