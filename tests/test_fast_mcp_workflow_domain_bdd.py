"""tests/test_fast_mcp_workflow_domain_bdd.py — Workflow domain unit tests.

Sprint 1: domain layer validation (no infrastructure).
"""
import pytest

from fast_mcp.features.workflow.domain.entities import (
    WorkflowGraph,
    WorkflowState,
    WorkflowNode,
    SubState,
    Gate,
    NodeExecution,
)
from fast_mcp.features.workflow.domain.values import (
    SubStateData,
    SideEffectDef,
    RetryPolicy,
    SelectOption,
)
from fast_mcp.features.workflow.domain.execution import ExecutionContext, TaskResult
from fast_mcp.features.workflow.domain.events import WorkflowEvent
from fast_mcp.features.workflow.domain.errors import (
    WorkflowEngineError,
    NodeNotFoundError,
    GateNotFoundError,
    TransitionBlockedError,
    StateConflictError,
)
from fast_mcp.features.workflow.domain.enums import ExecutionType
from fast_mcp.features.workflow.domain.services.advance import AdvanceService
from fast_mcp.features.workflow.domain.services.transition import TransitionService
from fast_mcp.features.workflow.domain.services.validation import ValidationService
from fast_mcp.features.workflow.domain.services.block_resolver import BlockResolver


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


def _make_state(node: str = "START", sub: str = "INIT", idx: int = 0) -> WorkflowState:
    return WorkflowState(current_node=node, current_sub_state=sub, sub_state_index=idx)


# ── WorkflowState tests ──────────────────────────────────────────────────

class TestWorkflowState:

    def test_record_submit_immutability(self):
        s1 = _make_state()
        s2 = s1.record_submit("INIT", {"key": "value"})
        assert s1.get_sub_state_data().fields == {}
        assert s2.get_sub_state_data().fields == {"key": "value"}

    def test_add_history(self):
        s1 = _make_state()
        s2 = s1.add_history("STEP_1")
        assert "STEP_1" in s2.history
        assert s1.history == []

    def test_with_node(self):
        s1 = _make_state("START", "INIT", 0)
        s2 = s1.with_node("WORK", "EXEC", 0)
        assert s2.current_node == "WORK"
        assert s2.current_sub_state == "EXEC"
        assert s1.current_node == "START"

    def test_reconcile_index_correct(self):
        g = _make_graph()
        s = _make_state("WORK", "EXEC", 0)
        reconciled, warnings = s.reconcile_index(g)
        assert len(warnings) == 0
        assert reconciled.sub_state_index == 0

    def test_reconcile_index_fixes(self):
        g = _make_graph()
        s = _make_state("WORK", "VERIFY", 0)  # wrong index
        reconciled, warnings = s.reconcile_index(g)
        assert len(warnings) == 1
        assert reconciled.sub_state_index == 1

    def test_to_dict_roundtrip(self):
        s1 = _make_state("WORK", "EXEC", 0)
        s1 = s1.record_submit("EXEC", {"data": "test"})
        d = s1.to_dict()
        s2 = WorkflowState.from_dict(d)
        assert s2.current_node == "WORK"
        assert s2.get_sub_state_data("EXEC").fields == {"data": "test"}

    def test_revision_starts_zero(self):
        s = _make_state()
        assert s.revision == 0

    def test_process_data(self):
        s1 = _make_state()
        s2 = s1.with_context("project_id", "walker")
        assert s2.context["project_id"] == "walker"
        assert s1.context == {}

    def test_record_node_execution_explicit_kwargs(self):
        s1 = _make_state("WORK", "EXEC", 0)
        s2 = s1.record_node_execution(
            node="WORK", sub_state="EXEC", execution_type="manual",
            action="test_action", task_ok=True, duration_ms=42,
        )
        assert len(s2.node_executions) == 1
        ne = s2.node_executions[0]
        assert ne.node == "WORK"
        assert ne.sub_state == "EXEC"
        assert ne.action == "test_action"
        assert ne.duration_ms == 42
        assert ne.completed_at != ""

    def test_record_node_execution_defaults(self):
        """Defaults to current_node/current_sub_state when not specified."""
        s1 = _make_state("WORK", "EXEC", 0)
        s2 = s1.record_node_execution()
        ne = s2.node_executions[0]
        assert ne.node == "WORK"
        assert ne.sub_state == "EXEC"

    def test_from_dict_legacy_format(self):
        """from_dict with missing new fields (v4 compat)."""
        legacy = {
            "version": "4.0",
            "current_node": "SPRINT",
            "current_sub_state": "PLANNING",
            "sub_state_index": 0,
            "sub_state_data": {"PLANNING": {"fields": {"specs": []}, "completed_at": None}},
            "history": ["START.INIT"],
            "context": {},
            # Missing: blocked_at, process_data, conflicts
        }
        s = WorkflowState.from_dict(legacy)
        assert s.version == "4.0"
        assert s.current_node == "SPRINT"
        assert s.blocked_at == ""
        assert s.process_data == {}
        assert s.conflicts == []

    def test_to_dict_from_dict_roundtrip_preserves_all_fields(self):
        s1 = _make_state("WORK", "EXEC", 0)
        s1 = s1.record_submit("EXEC", {"data": "x"})
        s1 = s1.record_node_execution(node="WORK", sub_state="EXEC", action="a")
        s1 = s1.add_history("WORK.EXEC")
        s1 = s1.with_context("key", "val")
        d = s1.to_dict()
        s2 = WorkflowState.from_dict(d)
        assert s2.current_node == s1.current_node
        assert s2.revision == s1.revision
        assert len(s2.node_executions) == 1
        assert len(s2.history) == 1
        assert s2.context == {"key": "val"}


# ── WorkflowGraph tests ──────────────────────────────────────────────────

class TestWorkflowGraph:

    def test_get_node(self):
        g = _make_graph()
        assert g.get_node("WORK") is not None
        assert g.get_node("NONEXISTENT") is None

    def test_find_gate(self):
        g = _make_graph()
        gate = g.find_gate("START", "start_work")
        assert gate is not None
        assert gate.target_node == "WORK"

    def test_validate_invariants_valid(self):
        g = _make_graph()
        assert g.validate_invariants() == []

    def test_validate_invariants_missing_target(self):
        g = _make_graph()
        g.edges.append(Gate(name="bad", source_node="START", target_node="NOPE"))
        g.edges_by_source["START"].append(Gate(name="bad", source_node="START", target_node="NOPE"))
        errors = g.validate_invariants()
        assert any("NOPE" in e for e in errors)

    def test_validate_invariants_duplicate_gate(self):
        g = _make_graph()
        dup = Gate(name="start_work", source_node="START", target_node="END")
        g.edges.append(dup)
        g.edges_by_source["START"].append(dup)
        errors = g.validate_invariants()
        assert any("Duplicate" in e for e in errors)


# ── SubState tests ───────────────────────────────────────────────────────

class TestSubState:

    def test_from_dict(self):
        ss = SubState.from_dict({"key": "TEST", "execution": "auto", "required_fields": ["x"]})
        assert ss.key == "TEST"
        assert ss.execution == ExecutionType.AUTO
        assert ss.required_fields == ["x"]

    def test_is_auto(self):
        ss = SubState(key="A", execution=ExecutionType.AUTO)
        assert ss.is_auto()
        ss2 = SubState(key="B", execution=ExecutionType.MANUAL)
        assert not ss2.is_auto()

    def test_is_llm_task(self):
        ss = SubState(key="A", execution=ExecutionType.LLM_TASK)
        assert ss.is_llm_task()

    def test_side_effects(self):
        se = SideEffectDef(type="index_code", params={"path": "/src"})
        ss = SubState(key="A", side_effects=[se])
        assert len(ss.side_effects) == 1
        assert ss.side_effects[0].type == "index_code"

    def test_from_dict_minimal(self):
        """from_dict with only 'key' — all optional fields use defaults."""
        ss = SubState.from_dict({"key": "X"})
        assert ss.key == "X"
        assert ss.execution == ExecutionType.MANUAL
        assert ss.required_fields == []
        assert ss.on_entry_message == ""
        assert ss.context is None
        assert ss.options == []
        assert ss.required_tools == []

    def test_from_dict_with_new_fields(self):
        """from_dict correctly parses on_entry_message and context."""
        ss = SubState.from_dict({
            "key": "Y",
            "on_entry_message": "Hello",
            "context": {"sources": ["ckg"], "focus": {"type": "convention"}},
        })
        assert ss.on_entry_message == "Hello"
        assert ss.context == {"sources": ["ckg"], "focus": {"type": "convention"}}

    def test_sub_state_data_with_field(self):
        """SubStateData.with_field returns new instance."""
        sd = SubStateData(fields={"a": 1})
        sd2 = sd.with_field("b", 2)
        assert sd.fields == {"a": 1}  # original unchanged
        assert sd2.fields == {"a": 1, "b": 2}


# ── TaskResult tests ─────────────────────────────────────────────────────

class TestTaskResult:

    def test_success(self):
        r = TaskResult.success({"ok": True})
        assert r.is_success()
        assert r.data == {"ok": True}

    def test_failure(self):
        r = TaskResult.failure("something broke")
        assert not r.is_success()
        assert r.error == "something broke"

    def test_map_success(self):
        r = TaskResult.success(10).map(lambda x: x * 2)
        assert r.data == 20

    def test_map_failure_propagates(self):
        r = TaskResult.failure("err").map(lambda x: x * 2)
        assert not r.is_success()

    def test_and_then(self):
        r = TaskResult.success(5).and_then(lambda x: TaskResult.success(x + 1))
        assert r.data == 6


# ── AdvanceService tests ─────────────────────────────────────────────────

class TestAdvanceService:

    def test_advance(self):
        g = _make_graph()
        s = _make_state("WORK", "EXEC", 0)
        s2 = AdvanceService.advance(s, g)
        assert s2 is not None
        assert s2.current_sub_state == "VERIFY"
        assert s2.sub_state_index == 1

    def test_advance_past_end(self):
        g = _make_graph()
        s = _make_state("WORK", "VERIFY", 1)
        s2 = AdvanceService.advance(s, g)
        assert s2 is None

    def test_advance_nonexistent_node(self):
        g = _make_graph()
        s = _make_state("NOPE", "X", 0)
        assert AdvanceService.advance(s, g) is None

    def test_jump_to_sub_state(self):
        g = _make_graph()
        s = _make_state("WORK", "VERIFY", 1)
        s2 = AdvanceService.jump_to_sub_state(s, g, "EXEC")
        assert s2.current_sub_state == "EXEC"
        assert s2.sub_state_index == 0

    def test_loop_back(self):
        g = _make_graph()
        s = _make_state("WORK", "VERIFY", 1)
        s2 = AdvanceService.loop_back(s, g)
        assert s2 is not None
        assert s2.current_sub_state == "EXEC"
        assert s2.sub_state_index == 0

    def test_loop_back_at_start(self):
        g = _make_graph()
        s = _make_state("WORK", "EXEC", 0)
        assert AdvanceService.loop_back(s, g) is None


# ── TransitionService tests ──────────────────────────────────────────────

class TestTransitionService:

    def test_apply(self):
        g = _make_graph()
        s = _make_state("START", "INIT", 0)
        s2 = TransitionService.apply(s, g, "WORK")
        assert s2.current_node == "WORK"
        assert s2.current_sub_state == "EXEC"

    def test_apply_nonexistent_node(self):
        g = _make_graph()
        s = _make_state()
        with pytest.raises(NodeNotFoundError):
            TransitionService.apply(s, g, "NOPE")


# ── ValidationService tests ──────────────────────────────────────────────

class TestValidationService:

    def test_check_transition_preconditions_ok(self):
        g = _make_graph()
        s = _make_state("WORK", "EXEC", 0)
        s = s.record_submit("EXEC", {"data": "test"})
        errors = ValidationService.check_transition_preconditions(s, g, "work_end")
        assert errors == []

    def test_check_transition_preconditions_missing(self):
        g = _make_graph()
        s = _make_state("WORK", "EXEC", 0)
        errors = ValidationService.check_transition_preconditions(s, g, "work_end")
        assert len(errors) > 0
        assert "data" in errors[0]

    def test_can_advance(self):
        g = _make_graph()
        s = _make_state("WORK", "EXEC", 0)
        assert ValidationService.can_advance(s, g)

    def test_cannot_advance_at_end(self):
        g = _make_graph()
        s = _make_state("WORK", "VERIFY", 1)
        assert not ValidationService.can_advance(s, g)

    def test_get_available_gates(self):
        g = _make_graph()
        s = _make_state("START", "INIT", 0)
        gates = ValidationService.get_available_gates(s, g)
        assert len(gates) == 1
        assert gates[0].name == "start_work"


# ── BlockResolver tests ──────────────────────────────────────────────────

class TestBlockResolver:

    def test_not_blocked(self):
        s = _make_state()
        assert not BlockResolver.is_blocked(s)

    def test_resolve_skip(self):
        s = _make_state()
        result = BlockResolver.resolve(s, "skip", {"reason": "timeout"})
        assert result.value_or(None) is not None

    def test_resolve_force(self):
        s = _make_state()
        result = BlockResolver.resolve(s, "force", {"reason": "blocked"})
        assert result.value_or(None) is not None


# ── WorkflowEvent tests ──────────────────────────────────────────────────

class TestWorkflowEvent:

    def test_of(self):
        e = WorkflowEvent.of("state_transitioned", {"from": "A", "to": "B"})
        assert e.event_type == "state_transitioned"
        assert e.timestamp != ""

    def test_to_dict(self):
        e = WorkflowEvent.of("test", {"x": 1}, task_id="T1")
        d = e.to_dict()
        assert d["event_type"] == "test"
        assert d["task_id"] == "T1"


# ── Value Objects tests ──────────────────────────────────────────────────

class TestValueObjects:

    def test_retry_policy_on_error(self):
        rp = RetryPolicy(max_attempts=5, on_error={"validation": "block+message"})
        assert rp.on_error == {"validation": "block+message"}
        assert rp.max_attempts == 5

    def test_retry_policy_on_error_default_none(self):
        rp = RetryPolicy()
        assert rp.on_error is None

    def test_select_option_requires(self):
        opt = SelectOption(value="yes", requires=["approval"])
        assert opt.requires == ["approval"]

    def test_select_option_requires_default_empty(self):
        opt = SelectOption(value="no")
        assert opt.requires == []


# ── Zero mcp/ imports test ──────────────────────────────────────────────

class TestDomainAutonomy:

    def test_no_mcp_imports(self):
        """Domain layer must not import from mcp/."""
        import ast
        import os

        domain_dir = os.path.join(
            os.path.dirname(__file__), "..", "fast_mcp", "features", "workflow", "domain"
        )
        forbidden = {"mcp", "protocols", "shared.core", "features.main"}
        violations = []

        for root, dirs, files in os.walk(domain_dir):
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

        assert not violations, f"Domain imports from mcp/: {violations}"
