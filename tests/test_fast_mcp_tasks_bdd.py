"""test_fast_mcp_tasks_bdd.py — BDD tests for tasks feature (Sprint 5.4).

Tests: TaskRegistry, SideEffectRegistry, task handlers, side effect handlers.
"""
from __future__ import annotations

import pytest


# ── Domain: TaskRegistry ──


class TestTaskRegistry:
    """TaskRegistry unit tests."""

    def test_register_and_execute(self):
        from fast_mcp.features.tasks.domain.task_registry import TaskRegistry, ExecutionContext, TaskResult

        registry = TaskRegistry()
        handler = lambda ctx, data: TaskResult.success(data={"echo": data.get("msg", "")})
        registry.register("echo_task", handler)

        ctx = ExecutionContext(chain_dir="/tmp/test")
        result = registry.execute("echo_task", ctx, {"msg": "hello"})
        assert result.ok is True
        assert result.data["echo"] == "hello"

    def test_execute_unknown_task(self):
        from fast_mcp.features.tasks.domain.task_registry import TaskRegistry, ExecutionContext

        registry = TaskRegistry()
        ctx = ExecutionContext()
        result = registry.execute("nonexistent", ctx, {})
        assert result.ok is False
        assert "unknown task" in result.error

    def test_execute_exception_safety(self):
        from fast_mcp.features.tasks.domain.task_registry import TaskRegistry, ExecutionContext

        registry = TaskRegistry()
        registry.register("boom", lambda ctx, data: 1 / 0)
        ctx = ExecutionContext()
        result = registry.execute("boom", ctx, {})
        assert result.ok is False
        assert "ZeroDivisionError" in result.error

    def test_has(self):
        from fast_mcp.features.tasks.domain.task_registry import TaskRegistry
        registry = TaskRegistry()
        registry.register("x", lambda ctx, data: None)
        assert registry.has("x") is True
        assert registry.has("y") is False

    def test_list_tasks(self):
        from fast_mcp.features.tasks.domain.task_registry import TaskRegistry
        registry = TaskRegistry()
        registry.register("a", lambda ctx, data: None, metadata={"tags": ["test"]})
        meta = registry.list_tasks()
        assert "a" in meta
        assert meta["a"].tags == ["test"]

    def test_metadata(self):
        from fast_mcp.features.tasks.domain.task_registry import TaskRegistry
        registry = TaskRegistry()
        registry.register("t", lambda ctx, data: None, metadata={
            "description": "desc",
            "tags": ["tag1"],
            "scope": "public",
        })
        meta = registry.list_tasks()["t"]
        assert meta.description == "desc"
        assert meta.scope == "public"


# ── Domain: SideEffectRegistry ──


class TestSideEffectRegistry:
    """SideEffectRegistry unit tests."""

    def test_register_and_execute(self):
        from fast_mcp.features.tasks.domain.side_effect_registry import SideEffectRegistry

        registry = SideEffectRegistry()
        registry.register("log_event", lambda chain_dir, params, **kw: {"ok": True, "logged": True})
        result = registry.execute("log_event", "/tmp", {"msg": "hi"})
        assert result["ok"] is True

    def test_execute_unknown(self):
        from fast_mcp.features.tasks.domain.side_effect_registry import SideEffectRegistry
        registry = SideEffectRegistry()
        result = registry.execute("nonexistent", "/tmp")
        assert result["ok"] is False
        assert "unknown side effect" in result["error"]

    def test_execute_exception_safety(self):
        from fast_mcp.features.tasks.domain.side_effect_registry import SideEffectRegistry
        registry = SideEffectRegistry()
        registry.register("boom", lambda chain_dir, params, **kw: 1 / 0)
        result = registry.execute("boom", "/tmp")
        assert result["ok"] is False
        assert "ZeroDivisionError" in result["error"]

    def test_has_and_list(self):
        from fast_mcp.features.tasks.domain.side_effect_registry import SideEffectRegistry
        registry = SideEffectRegistry()
        registry.register("x", lambda **kw: {})
        assert registry.has("x") is True
        assert "x" in registry.list_effects()


# ── Task Handlers ──


class TestImplementTask:
    """implement_task handler tests."""

    def test_basic(self):
        from fast_mcp.features.tasks.handlers.implement import implement_task
        from fast_mcp.features.tasks.domain.task_registry import ExecutionContext

        ctx = ExecutionContext(chain_dir="/tmp", task_id="t-1")
        result = implement_task(ctx, {"objective": "fix bug", "files": ["a.py"]})
        assert result.ok is True
        assert result.data["result_state"] == "fix bug"
        assert result.data["files_modified"] == ["a.py"]

    def test_empty(self):
        from fast_mcp.features.tasks.handlers.implement import implement_task
        from fast_mcp.features.tasks.domain.task_registry import ExecutionContext

        ctx = ExecutionContext()
        result = implement_task(ctx, {})
        assert result.ok is True
        assert result.data["files_modified"] == []


class TestVerifyPredicates:
    """verify_predicates handler tests."""

    def test_file_exists(self, tmp_path):
        from fast_mcp.features.tasks.handlers.verify import verify_predicates
        from fast_mcp.features.tasks.domain.task_registry import ExecutionContext

        f = tmp_path / "test.py"
        f.write_text("# test")

        ctx = ExecutionContext(chain_dir=str(tmp_path))
        result = verify_predicates(ctx, {
            "predicates": [{"type": "file_exists", "path": "test.py"}]
        })
        assert result.ok is True
        assert result.data["all_satisfied"] is True

    def test_file_missing(self, tmp_path):
        from fast_mcp.features.tasks.handlers.verify import verify_predicates
        from fast_mcp.features.tasks.domain.task_registry import ExecutionContext

        ctx = ExecutionContext(chain_dir=str(tmp_path))
        result = verify_predicates(ctx, {
            "predicates": [{"type": "file_exists", "path": "nonexistent.py"}]
        })
        assert result.ok is True
        assert result.data["all_satisfied"] is False

    def test_empty_predicates(self):
        """G-W6 anti-hallucination: empty predicates → failure."""
        from fast_mcp.features.tasks.handlers.verify import verify_predicates
        from fast_mcp.features.tasks.domain.task_registry import ExecutionContext

        ctx = ExecutionContext(chain_dir="/tmp")
        result = verify_predicates(ctx, {})
        assert result.ok is False
        assert "predicat" in result.error.lower() or "vacío" in result.error


class TestCapture:
    """capture handler tests."""

    def test_basic(self):
        from fast_mcp.features.tasks.handlers.capture import capture
        from fast_mcp.features.tasks.domain.task_registry import ExecutionContext

        ctx = ExecutionContext(chain_dir="/tmp", task_id="t-1")
        result = capture(ctx, {"evidence": "patch applied", "artifacts": ["diff.patch"]})
        assert result.ok is True
        assert result.data["captured"] is True
        assert result.data["evidence"] == "patch applied"


# ── Sprint Planning ──


class TestSprintPlanning:
    """sprint_planning handler tests.

    NOTE (Sprint 5 / R1): sprint_planning handlers are closures bound to an
    injected sprint_service (composition root). They are no longer importable
    as bare module-level callables. Tests build them via _build_handlers().
    """

    @staticmethod
    def _svc(project_root: str = "/tmp"):
        from fast_mcp.features.delta_tracking.application.services.sprint_service import (
            SprintService,
        )

        return SprintService(project_root=project_root)

    @staticmethod
    def _handlers(sprint_service=None):
        from fast_mcp.features.tasks.handlers.sprint_planning import _build_handlers

        plan_sprint, validate_plan, spec_task, close_sprint = _build_handlers(sprint_service)
        return plan_sprint, validate_plan, spec_task, close_sprint

    def test_plan_sprint_requires_goal(self):
        from fast_mcp.features.tasks.domain.task_registry import ExecutionContext

        plan_sprint, _, _, _ = self._handlers(self._svc())
        ctx = ExecutionContext(chain_dir="/tmp")
        result = plan_sprint(ctx, {"tasks": []})
        assert result.ok is False
        assert "goal" in result.error

    def test_plan_sprint_requires_context(self):
        from fast_mcp.features.tasks.domain.task_registry import ExecutionContext

        # No sprint_service injected → fail-loud at composition (R1).
        plan_sprint, _, _, _ = self._handlers(None)
        ctx = ExecutionContext(chain_dir="/tmp/x")
        result = plan_sprint(ctx, {"goal": "build x"})
        assert result.ok is False
        assert "sprint_service" in result.error

    def test_plan_sprint_success_with_project_root(self):
        from fast_mcp.features.tasks.domain.task_registry import ExecutionContext

        plan_sprint, _, _, _ = self._handlers(self._svc())
        ctx = ExecutionContext(chain_dir="/tmp/x")
        result = plan_sprint(ctx, {
            "goal": "Port sprint planning",
            "project_root": "/tmp",
            "tasks": [{"objective": "task 1"}, {"objective": "task 2"}],
        })
        assert result.ok is True
        assert result.data["goal"] == "Port sprint planning"
        assert result.data["spec_count"] == 2

    def test_spec_task_requires_fields(self):
        from fast_mcp.features.tasks.domain.task_registry import ExecutionContext

        _, _, spec_task, _ = self._handlers(self._svc())
        ctx = ExecutionContext(chain_dir="/tmp")
        result = spec_task(ctx, {"spring_id": ""})
        assert result.ok is False
        assert "incompleta" in result.error

    def test_spec_task_success(self):
        from fast_mcp.features.tasks.domain.task_registry import ExecutionContext

        _, _, spec_task, _ = self._handlers(self._svc())
        ctx = ExecutionContext(chain_dir="/tmp")
        result = spec_task(ctx, {
            "project_root": "/tmp",
            "spring_id": "sprint-1",
            "objective": "Implement feature",
        })
        assert result.ok is True
        assert result.data["valid"] is True
        assert result.data["objective"] == "Implement feature"

    def test_validate_plan_requires_context(self):
        from fast_mcp.features.tasks.domain.task_registry import ExecutionContext

        _, validate_plan, _, _ = self._handlers(None)
        ctx = ExecutionContext()
        result = validate_plan(ctx, {})
        assert result.ok is False
        assert "sprint_service" in result.error

    def test_close_sprint_requires_context(self):
        from fast_mcp.features.tasks.domain.task_registry import ExecutionContext

        _, _, _, close_sprint = self._handlers(None)
        ctx = ExecutionContext()
        result = close_sprint(ctx, {})
        assert result.ok is False

    def test_registered_in_registry(self):
        from fast_mcp.features.tasks.domain.task_registry import TaskRegistry
        from fast_mcp.features.tasks.handlers import register_all_tasks

        registry = TaskRegistry()
        register_all_tasks(registry)
        tasks = registry.list_tasks()
        assert "plan_sprint" in tasks
        assert "spec_task" in tasks
        assert "validate_plan" in tasks
        assert "close_sprint" in tasks


# ── Side Effect Handlers ──


class TestCaptureDecision:
    """capture_decision side effect tests."""

    def test_basic(self):
        from fast_mcp.features.tasks.side_effects.decision import capture_decision
        result = capture_decision("/tmp", {"decision": "approve", "sub_state": "DIAGNOSE"})
        assert result["ok"] is True
        assert result["decision"] == "approve"

    def test_no_decision(self):
        from fast_mcp.features.tasks.side_effects.decision import capture_decision
        result = capture_decision("/tmp", {})
        assert result["ok"] is False


class TestValidatePlan:
    """validate_plan side effect tests."""

    def test_valid(self):
        from fast_mcp.features.tasks.side_effects.validate import validate_plan
        result = validate_plan("/tmp", {"specs": [{"name": "s1", "objective": "do thing"}]})
        assert result["ok"] is True
        assert result["valid"] is True

    def test_invalid(self):
        from fast_mcp.features.tasks.side_effects.validate import validate_plan
        result = validate_plan("/tmp", {"specs": [{"name": "s1"}]})
        assert result["ok"] is False
        assert any("missing objective" in e for e in result["errors"])

    def test_empty(self):
        from fast_mcp.features.tasks.side_effects.validate import validate_plan
        result = validate_plan("/tmp", {})
        assert result["ok"] is True


class TestDetectDecision:
    """detect_decision side effect tests."""

    def test_has_decision(self):
        from fast_mcp.features.tasks.side_effects.decision_detector import detect_decision
        result = detect_decision("/tmp", {"decision": "fix"})
        assert result["has_decision"] is True

    def test_no_decision(self):
        from fast_mcp.features.tasks.side_effects.decision_detector import detect_decision
        result = detect_decision("/tmp", {})
        assert result["has_decision"] is False


class TestUpdateStatus:
    """update_status side effect tests."""

    def test_basic(self):
        from fast_mcp.features.tasks.side_effects.status import update_status
        result = update_status("/tmp", {"node": "IMPLEMENT", "sub_state": "code"})
        assert result["ok"] is True
        assert result["node"] == "IMPLEMENT"


# ── Auto-registration ──


class TestAutoRegistration:
    """Test that register_all_tasks and register_all_side_effects work."""

    def test_register_all_tasks(self):
        from fast_mcp.features.tasks.domain.task_registry import TaskRegistry
        from fast_mcp.features.tasks.handlers import register_all_tasks

        registry = TaskRegistry()
        register_all_tasks(registry)
        tasks = registry.list_tasks()
        assert "implement_task" in tasks
        assert "run_tests" in tasks
        assert "verify_predicates" in tasks
        assert "capture" in tasks

    def test_register_all_side_effects(self):
        from fast_mcp.features.tasks.domain.side_effect_registry import SideEffectRegistry
        from fast_mcp.features.tasks.side_effects import register_all_side_effects

        registry = SideEffectRegistry()
        register_all_side_effects(registry)
        effects = registry.list_effects()
        assert "capture_decision" in effects
        assert "validate_plan" in effects
        assert "detect_decision" in effects
        assert "update_status" in effects


# ── Composition ──


class TestComposition:
    """Test that composition builds registries correctly."""

    def test_build_task_registry(self):
        from fast_mcp.composition import _build_task_registry
        registry = _build_task_registry()
        assert registry.has("implement_task")
        assert registry.has("run_tests")

    def test_build_side_effect_registry(self):
        from fast_mcp.composition import _build_side_effect_registry
        registry = _build_side_effect_registry()
        assert registry.has("capture_decision")
        assert registry.has("validate_plan")
