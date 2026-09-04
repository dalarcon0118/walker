# Design Sprint 4 — Handlers + Composition Wiring

**Status:** DRAFT — pending approval before coding
**Date:** 2026-09-01
**ADR reference:** DESIGN-PHASE3-workflow-engine.md §10 Sprint 4
**Depends on:** Sprint 1 (domain) ✅ + Sprint 2 (application) ✅ + Sprint 3 (infrastructure) ✅

---

## 1. Objective

Wire the engine to MCP: create workflow tool handlers, compose all dependencies,
and register tools so the agent can call `submit`, `transition`, `status`, `help`.

**Exit criteria:**
- `submit` tool works end-to-end: prepare → submit → state persisted
- `transition` tool works: submit → transition → new node
- `status` tool returns workflow snapshot
- `help` tool returns available tools + current state
- `list_projects` tool works via workflow context
- E2E flow: `prepare` → `submit` → `transition` → `status` → `help`
- 12+ handler + composition tests
- Server starts with 21+ tools (3 Phase 1 + 14 Phase 2 + 4 Phase 3)

---

## 2. What gets created/modified

### New files

| File | LOC | Purpose |
|------|-----|---------|
| `features/workflow/context.py` | ~60 | WorkflowContext (frozen dataclass, resolves all deps) |
| `features/workflow/handlers/__init__.py` | ~50 | Registry + dispatch (same pattern as memories) |
| `features/workflow/handlers/workflow.py` | ~120 | handle_submit, handle_transition, handle_status, handle_help |

### Modified files

| File | Change |
|------|--------|
| `composition.py` | Add `workflow_tool_runner()` + `WorkflowContext` wiring |
| `phases/phase3_tasks.py` | Register submit/transition/status/help + list_projects (engine-backed) |
| `phases/__init__.py` | No change (Phase 3 already enabled) |

### Total new LOC: ~230

---

## 3. Architecture

### 3.1 WorkflowContext (~60 LOC)

Follows the same pattern as `MemoryContext`: frozen dataclass, resolves once, injects everywhere.

```python
# fast_mcp/features/workflow/context.py

@dataclass(frozen=True)
class WorkflowContext:
    """Resolved context for workflow tool handlers.

    Constructed per chain_dir by composition.py.
    All dependencies pre-wired; handlers never import features directly.
    """
    chain_dir: str
    orchestrator: OrchestrationService
    bundle: EngineBundle

    @classmethod
    def resolve(cls, chain_dir: str) -> WorkflowContext | None:
        """Resolve workflow context from chain_dir.

        Builds:
          1. GraphRepository (from workflow.json path)
          2. StateRepository (with graph for reconciliation)
          3. WorkflowStoreAdapter (unified port)
          4. NoopTracer, NoOpSideEffectEngine, DefaultWorkflowIO
          5. EngineBundle (project-scoped)
          6. OrchestrationService (injected bundle)

        Returns None if workflow.json not found.
        """
        # 1. Resolve workflow path from chain_dir
        workflow_path = _find_workflow_path(chain_dir)
        if not workflow_path:
            return None

        # 2. Build repos
        graph_repo = GraphRepository(workflow_path)
        state_repo = StateRepository(graph_repository=graph_repo)
        store = WorkflowStoreAdapter(graph_repo, state_repo)

        # 3. Build adapters (stubs)
        tracer = NoopTracer()
        side_effects = NoOpSideEffectEngine()
        workflow_io = DefaultWorkflowIO()

        # 4. Build bundle + orchestrator
        bundle = EngineBundle(
            workflow_store=store,
            workflow_io=workflow_io,
            tracer=tracer,
            side_effects=side_effects,
            chain_dir=chain_dir,
        )
        orch = OrchestrationService(bundle=bundle)

        return cls(chain_dir=chain_dir, orchestrator=orch, bundle=bundle)
```

**Key design:** `resolve()` is a classmethod on the context (same as `MemoryContext.resolve()`). The composition.py calls it, not the handlers. Handlers receive a pre-built context.

**`_find_workflow_path()` helper:**
- _Nota (actual): con la política GLOBAL del motor (project.md § 'Configuración
  del workflow: GLOBAL del motor'), esta descripción primitiva quedó obsoleta._
  La resolución actual delega en `PathManager.resolve_workflow_manifest()`
  (override `WALKER_WORKFLOW_MANIFEST`/`workflow.manifest` → canónico
  `<walker_root>/data/workflow/workflow.json` → None), con
  `chain_dir` reservado SOLO para estado/memoria y un workflow.json dentro del
  chain_dir del proyecto IGNORADO (ADR-047).

### 3.2 Handler Registry (~50 LOC)

Same pattern as `fast_mcp/features/memories/handlers/__init__.py`:

```python
# fast_mcp/features/workflow/handlers/__init__.py

_HANDLER_IMPORTS = {
    "submit": ("fast_mcp.features.workflow.handlers.workflow", "handle_submit"),
    "transition": ("fast_mcp.features.workflow.handlers.workflow", "handle_transition"),
    "status": ("fast_mcp.features.workflow.handlers.workflow", "handle_status"),
    "help": ("fast_mcp.features.workflow.handlers.workflow", "handle_help"),
    "list_projects": ("fast_mcp.features.workflow.handlers.workflow", "handle_list_projects"),
}

HANDLER_NAMES: frozenset[str] = frozenset(_HANDLER_IMPORTS.keys())

def dispatch(name: str, ctx: WorkflowContext, **kwargs) -> dict:
    """Dispatch workflow tool call with consistent error handling."""
    handler = get_handler(name)
    if handler is None:
        return {"error": f"Unknown workflow tool: {name}"}
    try:
        return handler(ctx, **kwargs)
    except Exception as e:
        return {"error": str(e), "tool": name}
```

### 3.3 Workflow Handlers (~120 LOC)

Each handler is a thin function that delegates to `OrchestrationService`:

```python
# fast_mcp/features/workflow/handlers/workflow.py

def handle_submit(ctx: WorkflowContext, **kwargs) -> dict:
    """Submit data to the current sub-state."""
    from ..domain.execution import ExecutionContext
    exec_ctx = ExecutionContext.create(
        chain_dir=ctx.chain_dir,
        task_id=kwargs.get("task_id", ""),
        session_id=kwargs.get("session_id", ""),
    )
    # Merge _payload into fields
    fields = dict(kwargs.get("_payload", {}))
    for k, v in kwargs.items():
        if not k.startswith("_"):
            fields[k] = v
    return ctx.orchestrator.submit(exec_ctx, fields=fields or None)


def handle_transition(ctx: WorkflowContext, **kwargs) -> dict:
    """Transition to the next node via a gate."""
    exec_ctx = ExecutionContext.create(
        chain_dir=ctx.chain_dir,
        task_id=kwargs.get("task_id", ""),
        session_id=kwargs.get("session_id", ""),
    )
    gate = kwargs.get("gate", "")
    if not gate:
        return {"ok": False, "error": "Missing required parameter: gate"}
    reason = kwargs.get("reason", "")
    return ctx.orchestrator.transition(exec_ctx, gate=gate, reason=reason)


def handle_status(ctx: WorkflowContext, **kwargs) -> dict:
    """Get current workflow status."""
    exec_ctx = ExecutionContext.create(chain_dir=ctx.chain_dir)
    return ctx.orchestrator.status(exec_ctx)


def handle_help(ctx: WorkflowContext, **kwargs) -> dict:
    """Get help info about the workflow and available tools."""
    exec_ctx = ExecutionContext.create(chain_dir=ctx.chain_dir)
    return ctx.orchestrator.help(exec_ctx)


def handle_list_projects(ctx: WorkflowContext, **kwargs) -> dict:
    """List available projects."""
    try:
        from fast_mcp.features.project.infrastructure.registry_store import RegistryStore
        store = RegistryStore()
        projects = store.list_projects()
        return {"count": len(projects), "projects": projects}
    except Exception as e:
        return {"error": str(e)}
```

### 3.4 Composition Wiring (~40 LOC additions)

```python
# fast_mcp/composition.py — additions

@dataclass(frozen=True)
class WorkflowCtx:
    """Pre-wired workflow engine dependencies (project-scoped)."""
    context: WorkflowContext  # from features/workflow/context.py


def workflow_tool_runner(name: str, root: Path | None = None):
    """Runner for workflow tools. Resolves WorkflowContext per call."""
    from fast_mcp.features.workflow.handlers import dispatch
    from fast_mcp.features.workflow.context import WorkflowContext

    def runner(**kwargs) -> dict:
        chain_dir = _resolve_chain_dir(kwargs)
        if not chain_dir:
            return {"error": "No project context. Run 'walker prepare <path>' first."}
        ctx = WorkflowContext.resolve(chain_dir)
        if ctx is None:
            return {"error": f"Could not resolve workflow for '{chain_dir}'"}
        return dispatch(name, ctx, **kwargs)

    return runner
```

### 3.5 Phase 3 Registration (~30 LOC changes)

```python
# fast_mcp/phases/phase3_tasks.py — updated

_FASE3_TOOLS = (
    "submit", "transition", "status", "help", "list_projects",
)

def install(server: FastMCP) -> None:
    for name in _FASE3_TOOLS:
        runner = _build_runner(name)
        register_domain_tool(
            server, name=name, runner=runner,
            description=_DESCRIPTIONS[name],
            input_schema=_INPUT_SCHEMAS[name],
        )

def _build_runner(name: str):
    from fast_mcp.composition import workflow_tool_runner
    return workflow_tool_runner(name)
```

---

## 4. Tool schemas (inputSchema SSOT)

```python
_INPUT_SCHEMAS = {
    "submit": {
        "type": "object",
        "properties": {
            "decision": {"type": "string", "description": "Decision to record"},
            "evidence": {"type": "string", "description": "Evidence for the decision"},
            "_payload": {"type": "object", "description": "Additional fields to submit"},
        },
    },
    "transition": {
        "type": "object",
        "properties": {
            "gate": {"type": "string", "description": "Gate name to transition via"},
            "reason": {"type": "string", "description": "Reason for transition"},
        },
        "required": ["gate"],
    },
    "status": {
        "type": "object",
        "properties": {},
    },
    "help": {
        "type": "object",
        "properties": {
            "tool": {"type": "string", "description": "Tool name to get help for (optional)"},
        },
    },
    "list_projects": {
        "type": "object",
        "properties": {},
    },
}
```

---

## 5. Invariants

| # | Invariant | Violation |
|---|-----------|-----------|
| INV-S4-01 | `WorkflowContext.resolve()` returns None if workflow.json not found | Silent empty graph |
| INV-S4-02 | Handlers never raise (caught by `dispatch()`) | MCP protocol violation |
| INV-S4-03 | `workflow_tool_runner` resolves context per call (not cached) | Stale project state |
| INV-S4-04 | `composition.py` is the ONLY place `WorkflowContext.resolve()` is called | Violates §3bis |
| INV-S4-05 | Phase 3 tools registered via `register_domain_tool()` with declarative schemas | Schema drift |

---

## 6. Test Plan

| Test | What it covers | Type |
|------|---------------|------|
| `test_workflow_context_resolve` | Context resolves from chain_dir with workflow.json | Unit |
| `test_workflow_context_resolve_missing` | Returns None if no workflow.json | Unit |
| `test_handler_submit_happy` | Submit with valid fields → ok | Unit (mocked) |
| `test_handler_submit_missing_gate` | Submit without fields → validation error | Unit |
| `test_handler_transition_happy` | Transition via valid gate → new node | Unit (mocked) |
| `test_handler_transition_missing_gate` | Transition without gate → error | Unit |
| `test_handler_status_returns_snapshot` | Status returns all fields | Unit (mocked) |
| `test_handler_help_returns_tools` | Help returns current state | Unit (mocked) |
| `test_handler_list_projects` | List projects from registry | Unit |
| `test_dispatch_unknown_tool` | Unknown tool → error dict | Unit |
| `test_dispatch_exception_handling` | Handler exception → error dict (not raised) | Unit |
| `test_workflow_tool_runner_resolves_context` | Runner creates context and dispatches | Unit |
| `test_no_mcp_imports` | Zero mcp/ imports in handlers/context | Architecture |

**LOC estimate:** ~150 (tests with mocked ports)

---

## 7. failure_scenarios

```json
[
  {
    "fallo": "WorkflowContext.resolve() no encuentra workflow.json",
    "causa_raiz": "Proyecto sin workflow.json o path incorrecto",
    "mitigacion": "resolve() retorna None; handler retorna {error: '...'}",
    "deteccion": "test: chain_dir sin workflow.json → error dict"
  },
  {
    "fallo": "submit recibe campos vacíos y validación falla",
    "causa_raiz": "Agente envía submit sin required_fields",
    "mitigacion": "OrchestrationService retorna {ok: false, error: 'Missing required fields'}",
    "deteccion": "test: submit sin campos → validation_failed"
  },
  {
    "fallo": "transition recibe gate inexistente",
    "causa_raiz": "Agente envía gate name incorrecto",
    "mitigacion": "OrchestrationService retorna {ok: false, error: 'Gate not found'}",
    "deteccion": "test: transition con gate fake → gate_not_found"
  },
  {
    "fallo": "workflow_tool_runner falla al construir EngineBundle",
    "causa_raiz": "Filesystem error al leer workflow.json",
    "mitigacion": "runner captura exception; retorna {error: str(e)}",
    "deteccion": "test: runner con store que lanza → error dict"
  },
  {
    "fallo": "Handler lanza excepción no capturada",
    "causa_raiz": "Bug en handler code",
    "mitigacion": "dispatch() envuelve en try/except; retorna {error: str(e)}",
    "deteccion": "test: handler que lanza → error dict, no exception"
  },
  {
    "fallo": "Composition.py resuelve WorkflowContext una vez y cachea",
    "causa_raiz": "Cadena de proyecto cambia entre calls",
    "mitigacion": "workflow_tool_runner llama resolve() por invocation, no cachea",
    "deteccion": "test: dos calls con chain_dirs distintos → contexts distintos"
  }
]
```

---

## 8. Success Criteria

- [ ] `WorkflowContext.resolve()` builds full dependency chain from chain_dir
- [ ] `submit` handler delegates to OrchestrationService.submit
- [ ] `transition` handler delegates to OrchestrationService.transition
- [ ] `status` handler returns workflow snapshot
- [ ] `help` handler returns tools + current state
- [ ] `list_projects` handler lists from registry
- [ ] `dispatch()` catches all exceptions → error dict
- [ ] `workflow_tool_runner` resolves context per call
- [ ] `composition.py` wires WorkflowContext
- [ ] `phase3_tasks.py` registers 5 tools with declarative schemas
- [ ] 13+ handler + composition tests pass
- [ ] Server starts with 21+ tools
- [ ] Zero imports from `mcp/` in handlers/context
