"""tests/test_fast_mcp_workflow_handlers_bdd.py — Sprint 4 handler + composition tests.

Tests WorkflowContext, handlers, dispatch, and workflow_tool_runner
with mocked OrchestrationService (no real filesystem needed for most tests).
"""
import json
import os
import pytest
from dataclasses import dataclass, field
from typing import Any

from fast_mcp.features.workflow.context import WorkflowContext, _find_workflow_path
from fast_mcp.features.workflow.handlers import dispatch, get_handler, HANDLER_NAMES
from fast_mcp.features.workflow.handlers.workflow import (
    handle_submit,
    handle_transition,
    handle_status,
    handle_help,
    handle_list_projects,
    _extract_fields,
)
from fast_mcp.features.workflow.application.orchestrator import OrchestrationService
from fast_mcp.features.workflow.application.bundle import EngineBundle
from fast_mcp.features.workflow.domain.entities import (
    Gate, SubState, WorkflowGraph, WorkflowNode, WorkflowState,
)
from fast_mcp.features.workflow.domain.execution import ExecutionContext
from fast_mcp.features.workflow.domain.ports import (
    ITracer, IWorkflowIO, IWorkflowStore, ISideEffectEngine,
)


# ── Mocks ────────────────────────────────────────────────────────────────

@dataclass
class MockWorkflowStore(IWorkflowStore):
    _graph: Any = None
    _state: Any = None
    _saved: list = field(default_factory=list)

    def load_graph(self):
        return self._graph or WorkflowGraph()
    def load_state(self, chain_dir):
        return self._state or WorkflowState()
    def save_state(self, chain_dir, state):
        self._saved.append(state)
        self._state = state
        return state

@dataclass
class MockTracer(ITracer):
    def trace(self, *a, **kw): pass
    def trace_event(self, e): pass
    def trace_submit(self, *a, **kw): pass
    def trace_transition(self, *a, **kw): pass
    def trace_error(self, *a, **kw): pass
    def has_kg_query(self, *a, **kw): return False

@dataclass
class MockIO(IWorkflowIO):
    def run_script(self, script, chain_dir=None):
        return {"exit_code": 0, "stdout": "", "stderr": ""}
    def eprint(self, *a, **kw): pass

@dataclass
class MockSideEffects(ISideEffectEngine):
    def run_compensation_handler(self, name, ctx):
        from returns.result import Success
        return Success({"ok": True, "skipped": True})


def _make_graph():
    return WorkflowGraph(
        nodes={
            "START": WorkflowNode(
                node_id="START",
                sub_states=[SubState(key="INIT", label="Init")],
            ),
            "WORK": WorkflowNode(
                node_id="WORK",
                sub_states=[
                    SubState(key="EXEC", label="Exec", required_fields=["data"]),
                    SubState(key="DONE", label="Done"),
                ],
            ),
        },
        edges=[Gate(name="go", source_node="START", target_node="WORK")],
        edges_by_source={
            "START": [Gate(name="go", source_node="START", target_node="WORK")],
        },
        initial_state="START",
    )


def _make_orchestrator(graph=None, state=None):
    store = MockWorkflowStore(_graph=graph or _make_graph(), _state=state)
    bundle = EngineBundle(
        workflow_store=store,
        workflow_io=MockIO(),
        tracer=MockTracer(),
        side_effects=MockSideEffects(),
        chain_dir="/tmp/test",
    )
    return OrchestrationService(bundle=bundle), store


def _make_ctx(graph=None, state=None):
    g = graph or _make_graph()
    s = state or WorkflowState(current_node="START", current_sub_state="INIT", sub_state_index=0)
    orch, store = _make_orchestrator(g, s)
    return WorkflowContext(
        chain_dir="/tmp/test",
        orchestrator=orch,
        bundle=orch._bundle,
    )


# ── WorkflowContext tests ────────────────────────────────────────────────

class TestWorkflowContext:

    def test_resolve_with_workflow_json(self, tmp_path):
        wf_dir = tmp_path / "mcp" / "data" / "workflow"
        wf_dir.mkdir(parents=True)
        wf_file = wf_dir / "workflow.json"
        wf_file.write_text(json.dumps({
            "initial_state": "S",
            "nodes": {"S": {"sub_states": [{"key": "X"}]}},
            "edges": [],
        }))

        ctx = WorkflowContext.resolve(str(tmp_path))
        assert ctx is not None
        assert ctx.chain_dir == str(tmp_path)
        assert ctx.orchestrator is not None

    def test_resolve_missing_workflow(self, tmp_path):
        ctx = WorkflowContext.resolve(str(tmp_path))
        assert ctx is None

    def test_resolve_empty_chain_dir(self):
        assert WorkflowContext.resolve("") is None
        assert WorkflowContext.resolve(None) is None  # type: ignore


# ── _find_workflow_path tests ────────────────────────────────────────────

class TestFindWorkflowPath:

    def test_finds_standard_layout(self, tmp_path):
        wf_dir = tmp_path / "mcp" / "data" / "workflow"
        wf_dir.mkdir(parents=True)
        (wf_dir / "workflow.json").write_text("{}")
        assert _find_workflow_path(str(tmp_path)) == str(wf_dir / "workflow.json")

    def test_finds_flat_layout(self, tmp_path):
        (tmp_path / "workflow.json").write_text("{}")
        assert _find_workflow_path(str(tmp_path)) == str(tmp_path / "workflow.json")

    def test_returns_none_if_missing(self, tmp_path):
        assert _find_workflow_path(str(tmp_path)) is None


# ── Handler registry tests ───────────────────────────────────────────────

class TestHandlerRegistry:

    def test_all_handlers_registered(self):
        assert HANDLER_NAMES == {"submit", "transition", "status", "help", "list_projects"}

    def test_get_handler_returns_callable(self):
        for name in HANDLER_NAMES:
            h = get_handler(name)
            assert h is not None, f"Handler '{name}' not found"
            assert callable(h)

    def test_get_handler_unknown_returns_none(self):
        assert get_handler("nonexistent") is None


# ── dispatch tests ───────────────────────────────────────────────────────

class TestDispatch:

    def test_dispatch_submit(self):
        ctx = _make_ctx()
        result = dispatch("submit", ctx, decision="approve")
        assert result["ok"] is True

    def test_dispatch_transition(self):
        ctx = _make_ctx()
        result = dispatch("transition", ctx, gate="go")
        assert result["ok"] is True

    def test_dispatch_status(self):
        ctx = _make_ctx()
        result = dispatch("status", ctx)
        assert result["ok"] is True

    def test_dispatch_help(self):
        ctx = _make_ctx()
        result = dispatch("help", ctx)
        assert result["ok"] is True

    def test_dispatch_unknown_tool(self):
        ctx = _make_ctx()
        result = dispatch("nonexistent", ctx)
        assert "error" in result

    def test_dispatch_exception_handling(self):
        """dispatch never raises — catches handler exceptions."""
        ctx = _make_ctx()
        # dispatch with a handler that raises
        from fast_mcp.features.workflow import handlers as h_mod
        original = h_mod._HANDLER_IMPORTS.copy()
        h_mod._HANDLER_IMPORTS["boom"] = (
            "tests.test_fast_mcp_workflow_handlers_bdd",
            "_broken_handler",
        )
        try:
            result = dispatch("boom", ctx)
            assert "error" in result
        finally:
            h_mod._HANDLER_IMPORTS.clear()
            h_mod._HANDLER_IMPORTS.update(original)


def _broken_handler(ctx, **kw):
    raise RuntimeError("intentional crash")


# ── handle_submit tests ──────────────────────────────────────────────────

class TestHandleSubmit:

    def test_happy_path(self):
        ctx = _make_ctx()
        result = handle_submit(ctx, decision="yes")
        assert result["ok"] is True
        assert result["node"] == "START"

    def test_missing_required_fields(self):
        g = _make_graph()
        state = WorkflowState(current_node="WORK", current_sub_state="EXEC", sub_state_index=0)
        ctx = _make_ctx(graph=g, state=state)
        result = handle_submit(ctx)
        assert result["ok"] is False
        assert "Missing required" in result["error"]

    def test_extracts_payload(self):
        ctx = _make_ctx()
        result = handle_submit(ctx, _payload={"extra": "val"}, decision="x")
        assert result["ok"] is True
        assert result["data"]["extra"] == "val"
        assert result["data"]["decision"] == "x"


# ── handle_transition tests ──────────────────────────────────────────────

class TestHandleTransition:

    def test_happy_path(self):
        ctx = _make_ctx()
        result = handle_transition(ctx, gate="go")
        assert result["ok"] is True
        assert result["to_node"] == "WORK"

    def test_missing_gate(self):
        ctx = _make_ctx()
        result = handle_transition(ctx)
        assert result["ok"] is False
        assert "Missing required parameter: gate" in result["error"]

    def test_invalid_gate(self):
        ctx = _make_ctx()
        result = handle_transition(ctx, gate="nonexistent")
        assert result["ok"] is False


# ── handle_status tests ──────────────────────────────────────────────────

class TestHandleStatus:

    def test_returns_snapshot(self):
        ctx = _make_ctx()
        result = handle_status(ctx)
        assert result["ok"] is True
        assert result["node"] == "START"
        assert "available_gates" in result


# ── handle_help tests ────────────────────────────────────────────────────

class TestHandleHelp:

    def test_returns_position(self):
        ctx = _make_ctx()
        result = handle_help(ctx)
        assert result["ok"] is True
        assert "current_position" in result


# ── _extract_fields tests ────────────────────────────────────────────────

class TestExtractFields:

    def test_merges_payload(self):
        fields = _extract_fields({"_payload": {"a": 1}, "b": 2})
        assert fields == {"a": 1, "b": 2}

    def test_excludes_internal_keys(self):
        fields = _extract_fields({"decision": "yes", "task_id": "T1", "session_id": "S1"})
        assert "task_id" not in fields
        assert "session_id" not in fields
        assert fields["decision"] == "yes"


# ── Import autonomy test ─────────────────────────────────────────────────

class TestHandlersAutonomy:

    def test_no_mcp_imports(self):
        """Handlers/context must not import from mcp/."""
        import ast
        dirs = [
            os.path.join(os.path.dirname(__file__), "..", "fast_mcp", "features", "workflow", "handlers"),
            os.path.join(os.path.dirname(__file__), "..", "fast_mcp", "features", "workflow", "context.py"),
        ]
        forbidden = {"mcp", "protocols", "shared.core", "features.main"}
        violations = []

        for item in dirs:
            if os.path.isfile(item):
                files = [item]
            else:
                files = [
                    os.path.join(item, f)
                    for f in os.listdir(item)
                    if f.endswith(".py") and f != "__pycache__"
                ]
            for filepath in files:
                with open(filepath) as fh:
                    try:
                        tree = ast.parse(fh.read())
                    except SyntaxError:
                        continue
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            if any(alias.name.startswith(fw) for fw in forbidden):
                                violations.append(f"{os.path.basename(filepath)}: import {alias.name}")
                    elif isinstance(node, ast.ImportFrom):
                        if node.module and any(node.module.startswith(fw) for fw in forbidden):
                            violations.append(f"{os.path.basename(filepath)}: from {node.module}")

        assert not violations, f"Handlers import from mcp/: {violations}"
