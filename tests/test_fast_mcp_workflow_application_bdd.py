"""tests/test_fast_mcp_workflow_application_bdd.py — Sprint 2 application tests.

Tests OrchestrationService, EngineBundle, execute_public_task, and StateManager
with mocked ports (no IO, no filesystem).
"""
import pytest
from dataclasses import dataclass, field
from typing import Any

from fast_mcp.features.workflow.application.bundle import EngineBundle
from fast_mcp.features.workflow.application.orchestrator import OrchestrationService
from fast_mcp.features.workflow.application.task_dispatcher import execute_public_task
from fast_mcp.features.workflow.application.state_manager import (
    ensure_state,
    store_submit_data,
)
from fast_mcp.features.workflow.domain.entities import (
    Gate,
    SubState,
    WorkflowGraph,
    WorkflowNode,
    WorkflowState,
)
from fast_mcp.features.workflow.domain.enums import ExecutionType
from fast_mcp.features.workflow.domain.execution import ExecutionContext
from fast_mcp.features.workflow.domain.ports import (
    ITracer,
    IWorkflowIO,
    IWorkflowStore,
    ISideEffectEngine,
)


# ── Mock Ports ───────────────────────────────────────────────────────────

@dataclass
class MockWorkflowStore(IWorkflowStore):
    """In-memory mock of IWorkflowStore."""
    _graph: WorkflowGraph | None = None
    _state: WorkflowState | None = None
    _saved_states: list[tuple[str, WorkflowState]] = field(default_factory=list)

    def load_graph(self) -> Any:
        if self._graph is None:
            raise FileNotFoundError("No graph loaded")
        return self._graph

    def load_state(self, chain_dir: str) -> Any:
        if self._state is None:
            return WorkflowState()
        return self._state

    def save_state(self, chain_dir: str, state: Any) -> Any:
        self._saved_states.append((chain_dir, state))
        self._state = state
        return state


@dataclass
class MockTracer(ITracer):
    """In-memory mock of ITracer."""
    events: list = field(default_factory=list)

    def trace(self, chain_dir, event_type, data=None, task_id=None, session_id=None):
        self.events.append(("trace", event_type))

    def trace_event(self, event):
        self.events.append(("event", event.event_type))

    def trace_submit(self, chain_dir, sub_state, decision, evidence, task_id=None):
        self.events.append(("submit", sub_state))

    def trace_transition(self, chain_dir, gate, from_node, to_node, task_id=None):
        self.events.append(("transition", gate, from_node, to_node))

    def trace_error(self, chain_dir, error_type, message, task_id=None):
        self.events.append(("error", error_type))

    def has_kg_query(self, chain_dir, task_id="", session_id="", layers=None):
        return True  # gate passes by default


@dataclass
class MockWorkflowIO(IWorkflowIO):
    def run_script(self, script, chain_dir=None):
        return {"ok": True, "stdout": "", "stderr": "", "exit_code": 0}

    def eprint(self, *args, **kwargs):
        pass


@dataclass
class MockSideEffects(ISideEffectEngine):
    def run_compensation_handler(self, handler_name, ctx):
        return {"ok": True, "skipped": True}


@dataclass
class MockRegistry:
    """Mock task registry for execute_public_task tests."""
    _tasks: dict = field(default_factory=dict)

    def execute(self, name, ctx, data):
        from fast_mcp.features.workflow.domain.execution import TaskResult
        if name in self._tasks:
            return TaskResult.success(self._tasks[name](data))
        return TaskResult.failure(f"Unknown task: {name}")


# ── Helpers ──────────────────────────────────────────────────────────────

def _make_graph() -> WorkflowGraph:
    """Build a simple 3-node graph for testing."""
    return WorkflowGraph(
        nodes={
            "START": WorkflowNode(
                node_id="START",
                sub_states=[SubState(key="INIT", label="Initialize")],
                label="Start",
            ),
            "WORK": WorkflowNode(
                node_id="WORK",
                sub_states=[
                    SubState(key="EXEC", label="Execute", required_fields=["data"]),
                    SubState(key="VERIFY", label="Verify"),
                ],
                label="Work",
            ),
            "END": WorkflowNode(
                node_id="END",
                sub_states=[SubState(key="DONE", label="Done")],
                label="End",
            ),
        },
        edges=[
            Gate(name="start_work", source_node="START", target_node="WORK"),
            Gate(name="work_end", source_node="WORK", target_node="END"),
        ],
        edges_by_source={
            "START": [Gate(name="start_work", source_node="START", target_node="WORK")],
            "WORK": [Gate(name="work_end", source_node="WORK", target_node="END")],
        },
        initial_state="START",
    )


def _make_bundle(
    graph: WorkflowGraph | None = None,
    state: WorkflowState | None = None,
) -> tuple[EngineBundle, MockWorkflowStore, MockTracer]:
    """Create a bundle with mocked ports."""
    store = MockWorkflowStore(_graph=graph, _state=state)
    tracer = MockTracer()
    bundle = EngineBundle(
        workflow_store=store,
        workflow_io=MockWorkflowIO(),
        tracer=tracer,
        side_effects=MockSideEffects(),
        chain_dir="/tmp/test-project",
    )
    return bundle, store, tracer


def _ctx(task_id: str = "T1", session_id: str = "S1") -> ExecutionContext:
    return ExecutionContext(
        session_id=session_id,
        trace_id="trace-1",
        span_id="span-1",
        chain_dir="/tmp/test-project",
        task_id=task_id,
    )


# ── EngineBundle tests ──────────────────────────────────────────────────

class TestEngineBundle:

    def test_creation(self):
        bundle, _, _ = _make_bundle()
        assert bundle.chain_dir == "/tmp/test-project"
        assert bundle.workflow_store is not None
        assert bundle.tracer is not None

    def test_frozen(self):
        bundle, _, _ = _make_bundle()
        with pytest.raises(AttributeError):
            bundle.chain_dir = "/other"


# ── OrchestrationService.submit tests ───────────────────────────────────

class TestOrchestratorSubmit:

    def test_submit_happy_path(self):
        g = _make_graph()
        s = WorkflowState(current_node="START", current_sub_state="INIT", sub_state_index=0)
        bundle, store, tracer = _make_bundle(graph=g, state=s)
        orch = OrchestrationService(bundle=bundle)

        result = orch.submit(_ctx(), fields={"decision": "approve"})

        assert result["ok"] is True
        assert result["node"] == "START"
        assert result["sub_state"] == "INIT"
        assert len(store._saved_states) == 1

    def test_submit_saves_state(self):
        """submit persists state via IWorkflowStore (revision bump is repository responsibility)."""
        g = _make_graph()
        s = WorkflowState(current_node="START", current_sub_state="INIT", revision=5)
        bundle, store, _ = _make_bundle(graph=g, state=s)
        orch = OrchestrationService(bundle=bundle)

        result = orch.submit(_ctx())

        assert result["ok"] is True
        assert len(store._saved_states) == 1
        # Revision stays 5 — the repository handles CAS + bump
        assert store._state.revision == 5

    def test_submit_records_node_execution(self):
        g = _make_graph()
        s = WorkflowState(current_node="START", current_sub_state="INIT")
        bundle, store, _ = _make_bundle(graph=g, state=s)
        orch = OrchestrationService(bundle=bundle)

        orch.submit(_ctx(), fields={"key": "val"})

        saved_state = store._state
        assert len(saved_state.node_executions) == 1
        ne = saved_state.node_executions[0]
        assert ne.node == "START"
        assert ne.sub_state == "INIT"
        assert ne.submitted_fields == {"key": "val"}

    def test_submit_traces(self):
        g = _make_graph()
        s = WorkflowState(current_node="START", current_sub_state="INIT")
        bundle, _, tracer = _make_bundle(graph=g, state=s)
        orch = OrchestrationService(bundle=bundle)

        orch.submit(_ctx())

        assert any(e[0] == "submit" for e in tracer.events)

    def test_submit_missing_required_fields(self):
        g = _make_graph()
        s = WorkflowState(current_node="WORK", current_sub_state="EXEC", sub_state_index=0)
        bundle, store, _ = _make_bundle(graph=g, state=s)
        orch = OrchestrationService(bundle=bundle)

        result = orch.submit(_ctx())  # no fields → 'data' is missing

        assert result["ok"] is False
        assert "Missing required fields" in result["error"]

    def test_submit_nonexistent_node(self):
        g = _make_graph()
        s = WorkflowState(current_node="NOPE", current_sub_state="X")
        bundle, _, _ = _make_bundle(graph=g, state=s)
        orch = OrchestrationService(bundle=bundle)

        result = orch.submit(_ctx())

        assert result["ok"] is False
        assert "not in graph" in result["error"]

    def test_submit_persist_failure(self):
        g = _make_graph()
        s = WorkflowState(current_node="START", current_sub_state="INIT")

        class FailingStore(IWorkflowStore):
            def load_graph(self): return g
            def load_state(self, chain_dir): return s
            def save_state(self, chain_dir, state):
                raise OSError("Disk full")

        bundle = EngineBundle(
            workflow_store=FailingStore(),
            workflow_io=MockWorkflowIO(),
            tracer=MockTracer(),
            side_effects=MockSideEffects(),
            chain_dir="/tmp/test",
        )
        orch = OrchestrationService(bundle=bundle)

        result = orch.submit(_ctx())

        assert result["ok"] is False
        assert "persist" in result["error"].lower()

    def test_submit_preserves_immutable_state(self):
        g = _make_graph()
        s = WorkflowState(current_node="START", current_sub_state="INIT")
        bundle, store, _ = _make_bundle(graph=g, state=s)
        orch = OrchestrationService(bundle=bundle)

        orch.submit(_ctx(), fields={"x": 1})

        # Original state object should be unchanged
        assert s.current_node == "START"
        assert s.revision == 0


# ── Engine anémico: sub_state.action execution (Sprint 5) ────────────────

class TestActionExecution:
    """Verify submit executes sub_state.action via TaskRegistry (engine anémico).

    Previously submit recorded task_ok=True hardcoded without running the
    declared action (orchestrator.py line ~196). Now it dispatches the action
    through bundle.task_registry and blocks (fail-loud) if it fails.
    """

    def _make_action_graph(self, action: str) -> WorkflowGraph:
        return WorkflowGraph(
            nodes={
                "START": WorkflowNode(
                    node_id="START",
                    sub_states=[
                        SubState(key="INIT", label="Init", action=action)
                    ],
                    label="Start",
                ),
            },
            edges=[],
            edges_by_source={},
            initial_state="START",
        )

    def _make_action_registry(self, ok: bool, error: str = ""):
        from fast_mcp.features.tasks.domain.task_registry import TaskRegistry, TaskResult

        reg = TaskRegistry()

        def _handler(ctx, data):
            if not ok:
                return TaskResult.failure(error or "action failed")
            return TaskResult.success(data={"executed": True, "action": data.get("task_id")})

        reg.register("test_action", _handler)
        return reg

    def test_submit_executes_action_and_succeeds(self):
        g = self._make_action_graph(action="test_action")
        s = WorkflowState(current_node="START", current_sub_state="INIT")
        bundle, store, _ = _make_bundle(graph=g, state=s)
        # Inject real task registry via the bundle (engine anémico fix).
        bundle = EngineBundle(
            workflow_store=bundle.workflow_store,
            workflow_io=bundle.workflow_io,
            tracer=bundle.tracer,
            side_effects=bundle.side_effects,
            chain_dir=bundle.chain_dir,
            task_registry=self._make_action_registry(ok=True),
        )
        orch = OrchestrationService(bundle=bundle)

        result = orch.submit(_ctx(), fields={"decision": "approve"})

        assert result["ok"] is True
        # The recorded NodeExecution must carry the real task_ok + action result.
        last_exec = store._saved_states[-1][-1].node_executions[-1]
        assert last_exec.action == "test_action"
        assert last_exec.task_ok is True
        assert last_exec.task_result == {
            "action": "test_action",
            "data": {"executed": True, "action": "T1"},
        }
        assert last_exec.task_result["data"]["executed"] is True

    def test_submit_executes_action_and_fail_loud_on_failure(self):
        g = self._make_action_graph(action="test_action")
        s = WorkflowState(current_node="START", current_sub_state="INIT")
        bundle, store, _ = _make_bundle(graph=g, state=s)
        bundle = EngineBundle(
            workflow_store=bundle.workflow_store,
            workflow_io=bundle.workflow_io,
            tracer=bundle.tracer,
            side_effects=bundle.side_effects,
            chain_dir=bundle.chain_dir,
            task_registry=self._make_action_registry(ok=False, error="DoD no verificada"),
        )
        orch = OrchestrationService(bundle=bundle)

        result = orch.submit(_ctx(), fields={"decision": "approve"})

        # Fail-loud: action returned ok=False → submit must NOT succeed.
        assert result["ok"] is False
        assert result["action"] == "task_failed"
        assert "DoD no verificada" in result["error"]
        last_exec = store._saved_states[-1][-1].node_executions[-1]
        assert last_exec.task_ok is False

    def test_submit_action_unknown_fails_loud(self):
        g = self._make_action_graph(action="does_not_exist")
        s = WorkflowState(current_node="START", current_sub_state="INIT")
        bundle, store, _ = _make_bundle(graph=g, state=s)
        bundle = EngineBundle(
            workflow_store=bundle.workflow_store,
            workflow_io=bundle.workflow_io,
            tracer=bundle.tracer,
            side_effects=bundle.side_effects,
            chain_dir=bundle.chain_dir,
            task_registry=self._make_action_registry(ok=True),
        )
        orch = OrchestrationService(bundle=bundle)

        result = orch.submit(_ctx())

        assert result["ok"] is False
        assert "does_not_exist" in result["error"]

    def test_submit_no_action_still_succeeds_without_registry(self):
        """Backward compat: sub-state without action needs no registry."""
        g = _make_graph()
        s = WorkflowState(current_node="START", current_sub_state="INIT")
        bundle, store, _ = _make_bundle(graph=g, state=s)  # no task_registry
        orch = OrchestrationService(bundle=bundle)

        result = orch.submit(_ctx(), fields={"decision": "approve"})

        assert result["ok"] is True
        assert store._saved_states[-1][-1].node_executions[-1].task_ok is True


# ── OrchestrationService.transition tests ────────────────────────────────

class TestOrchestratorTransition:

    def test_transition_happy_path(self):
        g = _make_graph()
        s = WorkflowState(current_node="START", current_sub_state="INIT")
        bundle, store, tracer = _make_bundle(graph=g, state=s)
        orch = OrchestrationService(bundle=bundle)

        result = orch.transition(_ctx(), gate="start_work")

        assert result["ok"] is True
        assert result["to_node"] == "WORK"
        assert result["gate"] == "start_work"
        assert len(store._saved_states) == 1

    def test_transition_invalid_gate(self):
        g = _make_graph()
        s = WorkflowState(current_node="START", current_sub_state="INIT")
        bundle, _, _ = _make_bundle(graph=g, state=s)
        orch = OrchestrationService(bundle=bundle)

        result = orch.transition(_ctx(), gate="nonexistent")

        assert result["ok"] is False
        assert "not found" in result["error"]

    def test_transition_wrong_source(self):
        """Gate 'work_end' exists on WORK but source_node is WORK, not START.

        We test by adding a gate from START to END, then trying to use it from WORK.
        """
        g = _make_graph()
        # Add a gate that exists on WORK but with a different source_node
        # Actually: work_end has source_node=WORK, so using it from START is wrong
        s = WorkflowState(current_node="START", current_sub_state="INIT")
        bundle, _, _ = _make_bundle(graph=g, state=s)
        orch = OrchestrationService(bundle=bundle)

        # work_end gate exists but source_node=WORK, current_node=START
        result = orch.transition(_ctx(), gate="work_end")

        assert result["ok"] is False
        assert "not found" in result["error"].lower() or "source" in result["error"].lower()

    def test_transition_traces(self):
        g = _make_graph()
        s = WorkflowState(current_node="START", current_sub_state="INIT")
        bundle, _, tracer = _make_bundle(graph=g, state=s)
        orch = OrchestrationService(bundle=bundle)

        orch.transition(_ctx(), gate="start_work")

        assert any(e[0] == "transition" for e in tracer.events)

    def test_transition_records_node_execution(self):
        g = _make_graph()
        s = WorkflowState(current_node="START", current_sub_state="INIT")
        bundle, store, _ = _make_bundle(graph=g, state=s)
        orch = OrchestrationService(bundle=bundle)

        orch.transition(_ctx(), gate="start_work", reason="proceeding")

        saved = store._state
        assert len(saved.node_executions) == 1
        ne = saved.node_executions[0]
        assert ne.execution_type == "transition"
        assert ne.gate == "start_work"

    def test_transition_preserves_immutable_state(self):
        g = _make_graph()
        s = WorkflowState(current_node="START", current_sub_state="INIT")
        bundle, _, _ = _make_bundle(graph=g, state=s)
        orch = OrchestrationService(bundle=bundle)

        orch.transition(_ctx(), gate="start_work")

        assert s.current_node == "START"  # original unchanged

    def test_transition_unblocks_state(self):
        g = _make_graph()
        s = WorkflowState(
            current_node="START",
            current_sub_state="INIT",
            blocked_at="START.INIT",
            block_reason="waiting",
            block_since="2026-01-01",
        )
        bundle, store, _ = _make_bundle(graph=g, state=s)
        orch = OrchestrationService(bundle=bundle)

        result = orch.transition(_ctx(), gate="start_work")

        assert result["ok"] is True
        saved = store._state
        assert saved.blocked_at == ""  # unblocked


# ── OrchestrationService.status tests ────────────────────────────────────

class TestOrchestratorStatus:

    def test_status_returns_snapshot(self):
        g = _make_graph()
        s = WorkflowState(current_node="WORK", current_sub_state="EXEC", sub_state_index=0)
        bundle, _, _ = _make_bundle(graph=g, state=s)
        orch = OrchestrationService(bundle=bundle)

        result = orch.status(_ctx())

        assert result["ok"] is True
        assert result["node"] == "WORK"
        assert result["sub_state"] == "EXEC"
        assert result["execution_type"] == "manual"
        assert "data" in result["required_fields"]

    def test_status_available_gates(self):
        g = _make_graph()
        s = WorkflowState(current_node="WORK", current_sub_state="EXEC", sub_state_index=0)
        bundle, _, _ = _make_bundle(graph=g, state=s)
        orch = OrchestrationService(bundle=bundle)

        result = orch.status(_ctx())

        assert len(result["available_gates"]) == 1
        assert result["available_gates"][0]["name"] == "work_end"

    def test_status_read_only(self):
        g = _make_graph()
        s = WorkflowState(current_node="START", current_sub_state="INIT")
        bundle, store, _ = _make_bundle(graph=g, state=s)
        orch = OrchestrationService(bundle=bundle)

        orch.status(_ctx())

        assert len(store._saved_states) == 0  # no writes


# ── OrchestrationService.help tests ──────────────────────────────────────

class TestOrchestratorHelp:

    def test_help_returns_position(self):
        g = _make_graph()
        s = WorkflowState(current_node="WORK", current_sub_state="EXEC", sub_state_index=0)
        bundle, _, _ = _make_bundle(graph=g, state=s)
        orch = OrchestrationService(bundle=bundle)

        result = orch.help(_ctx())

        assert result["ok"] is True
        assert result["current_position"] == "WORK.EXEC"
        assert result["execution_type"] == "manual"
        assert "work_end" in result["available_gates"]

    def test_help_read_only(self):
        g = _make_graph()
        s = WorkflowState(current_node="START", current_sub_state="INIT")
        bundle, store, _ = _make_bundle(graph=g, state=s)
        orch = OrchestrationService(bundle=bundle)

        orch.help(_ctx())

        assert len(store._saved_states) == 0


# ── execute_public_task tests ────────────────────────────────────────────

class TestExecutePublicTask:

    def test_happy_path(self):
        registry = MockRegistry(_tasks={"greet": lambda d: {"msg": f"Hello {d.get('name', '?')}"}})
        result = execute_public_task(
            name="greet",
            project_id="walker",
            chain_dir="/tmp/test",
            task_data={"name": "World"},
            registry=registry,
        )
        assert result["ok"] is True
        assert result["data"]["msg"] == "Hello World"

    def test_registry_none(self):
        result = execute_public_task(
            name="greet", project_id="p", chain_dir="/tmp", task_data={}, registry=None
        )
        assert result["ok"] is False
        assert "not available" in result["error"]

    def test_unknown_task(self):
        registry = MockRegistry()
        result = execute_public_task(
            name="nonexistent", project_id="p", chain_dir="/tmp", task_data={}, registry=registry
        )
        assert result["ok"] is False
        assert "Unknown task" in result["error"]

    def test_registry_exception(self):
        class ExplodingRegistry:
            def execute(self, name, ctx, data):
                raise RuntimeError("boom")

        result = execute_public_task(
            name="x", project_id="p", chain_dir="/tmp", task_data={}, registry=ExplodingRegistry()
        )
        assert result["ok"] is False
        assert "boom" in result["error"]

    def test_merges_payload(self):
        captured = {}
        def capture(d):
            captured.update(d)
            return {"ok": True}

        registry = MockRegistry(_tasks={"test": capture})
        execute_public_task(
            name="test", project_id="p", chain_dir="/tmp",
            task_data={"_payload": {"extra": "val"}},
            registry=registry,
        )
        assert captured["extra"] == "val"
        assert captured["_project_id"] == "p"


# ── StateManager tests ──────────────────────────────────────────────────

class TestStateManager:

    def test_ensure_state(self):
        store = MockWorkflowStore(_state=WorkflowState(current_node="START"))
        result = ensure_state("/tmp/test", store)
        assert result["current_node"] == "START"

    def test_ensure_state_empty_chain_dir(self):
        store = MockWorkflowStore()
        with pytest.raises(ValueError):
            ensure_state("", store)

    def test_store_submit_data(self):
        store = MockWorkflowStore(_state=WorkflowState(current_node="WORK", current_sub_state="EXEC"))
        store_submit_data("/tmp", "EXEC", {"data": "test"}, store)
        assert store._state.get_sub_state_data("EXEC").fields == {"data": "test"}

    def test_store_submit_data_noop_on_empty(self):
        store = MockWorkflowStore(_state=WorkflowState())
        store_submit_data("", "X", {}, store)
        assert len(store._saved_states) == 0


# ── Import autonomy test ─────────────────────────────────────────────────

class TestApplicationAutonomy:

    def test_no_mcp_imports(self):
        """Application layer must not import from mcp/."""
        import ast
        import os

        app_dir = os.path.join(
            os.path.dirname(__file__), "..", "fast_mcp", "features", "workflow", "application"
        )
        forbidden = {"mcp", "protocols", "shared.core", "features.main"}
        violations = []

        for root, dirs, files in os.walk(app_dir):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for f in files:
                if not f.endswith(".py"):
                    continue
                filepath = os.path.join(root, f)
                with open(filepath) as fh:
                    try:
                        tree = ast.parse(fh.read())
                    except SyntaxError:
                        continue
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            if any(alias.name.startswith(fw) for fw in forbidden):
                                violations.append(f"{f}: import {alias.name}")
                    elif isinstance(node, ast.ImportFrom):
                        if node.module and any(node.module.startswith(fw) for fw in forbidden):
                            violations.append(f"{f}: from {node.module}")

        assert not violations, f"Application imports from mcp/: {violations}"
