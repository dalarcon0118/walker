"""test_fast_mcp_task_dispatch_bdd.py — Integration tests for task dispatch.

Tests that execute_public_task works with TaskRegistry via WorkflowContext.
"""
from __future__ import annotations

import pytest


class TestExecutePublicTask:
    """execute_public_task integration tests."""

    def test_with_explicit_registry(self):
        from fast_mcp.features.workflow.application.task_dispatcher import execute_public_task
        from fast_mcp.features.tasks.domain.task_registry import TaskRegistry, TaskResult

        registry = TaskRegistry()
        registry.register("echo", lambda ctx, data: TaskResult.success(data={"msg": data.get("msg", "")}))

        result = execute_public_task(
            name="echo",
            project_id="p-1",
            chain_dir="/tmp",
            task_data={"msg": "hello"},
            registry=registry,
        )
        assert result["ok"] is True
        assert result["data"]["msg"] == "hello"

    def test_with_workflow_context(self):
        from fast_mcp.features.workflow.application.task_dispatcher import execute_public_task
        from fast_mcp.features.tasks.domain.task_registry import TaskRegistry, TaskResult
        from dataclasses import dataclass

        @dataclass
        class FakeContext:
            chain_dir: str = "/tmp"
            task_registry: TaskRegistry = None

        registry = TaskRegistry()
        registry.register("echo", lambda ctx, data: TaskResult.success(data={"echo": True}))
        ctx = FakeContext(task_registry=registry)

        result = execute_public_task(
            name="echo",
            project_id="p-1",
            chain_dir="/tmp",
            task_data={},
            workflow_context=ctx,
        )
        assert result["ok"] is True

    def test_no_registry(self):
        from fast_mcp.features.workflow.application.task_dispatcher import execute_public_task

        result = execute_public_task(
            name="echo",
            project_id="p-1",
            chain_dir="/tmp",
            task_data={},
        )
        assert result["ok"] is False
        assert "not available" in result["error"]

    def test_unknown_task(self):
        from fast_mcp.features.workflow.application.task_dispatcher import execute_public_task
        from fast_mcp.features.tasks.domain.task_registry import TaskRegistry

        result = execute_public_task(
            name="nonexistent",
            project_id="p-1",
            chain_dir="/tmp",
            task_data={},
            registry=TaskRegistry(),
        )
        assert result["ok"] is False
        assert "unknown task" in result["error"]

    def test_payload_merge(self):
        from fast_mcp.features.workflow.application.task_dispatcher import execute_public_task
        from fast_mcp.features.tasks.domain.task_registry import TaskRegistry, TaskResult

        registry = TaskRegistry()
        registry.register("check", lambda ctx, data: TaskResult.success(data={
            "has_files": "files" in data,
            "has_decision": "decision" in data,
        }))

        result = execute_public_task(
            name="check",
            project_id="p-1",
            chain_dir="/tmp",
            task_data={"_payload": {"files": ["a.py"], "decision": "approve"}},
            registry=registry,
        )
        assert result["ok"] is True
        assert result["data"]["has_files"] is True
        assert result["data"]["has_decision"] is True


class TestWorkflowHandlerExecuteTask:
    """handle_execute_task handler tests."""

    def test_basic(self):
        from fast_mcp.features.workflow.handlers.workflow import handle_execute_task
        from fast_mcp.features.tasks.domain.task_registry import TaskRegistry, TaskResult
        from dataclasses import dataclass

        @dataclass
        class FakeCtx:
            chain_dir: str = "/tmp"
            task_registry: TaskRegistry = None

        registry = TaskRegistry()
        registry.register("echo", lambda ctx, data: TaskResult.success(data={"ok": True}))
        ctx = FakeCtx(task_registry=registry)

        result = handle_execute_task(ctx, task_name="echo", msg="hi")
        assert result["ok"] is True

    def test_missing_task_name(self):
        from fast_mcp.features.workflow.handlers.workflow import handle_execute_task
        from dataclasses import dataclass

        @dataclass
        class FakeCtx:
            chain_dir: str = "/tmp"
            task_registry: object = None

        result = handle_execute_task(FakeCtx())
        assert result["ok"] is False
        assert "Missing" in result["error"]


class TestWorkflowContextTaskRegistry:
    """WorkflowContext integration with TaskRegistry."""

    def test_context_has_task_registry(self):
        """Verify WorkflowContext.resolve() builds TaskRegistry."""
        from fast_mcp.features.workflow.context import WorkflowContext
        from fast_mcp.features.tasks.domain.task_registry import TaskRegistry

        # Can't test resolve() without a real project, but we can test
        # that the TaskRegistry builder works
        from fast_mcp.composition import _build_task_registry
        registry = _build_task_registry()
        assert isinstance(registry, TaskRegistry)
        assert registry.has("implement_task")
        assert registry.has("run_tests")
        assert registry.has("verify_predicates")
        assert registry.has("capture")

    def test_task_registry_execute_end_to_end(self):
        """End-to-end: TaskRegistry → handler → result."""
        from fast_mcp.features.tasks.domain.task_registry import TaskRegistry, ExecutionContext
        from fast_mcp.features.tasks.handlers import register_all_tasks

        registry = TaskRegistry()
        register_all_tasks(registry)

        ctx = ExecutionContext(chain_dir="/tmp", task_id="t-1")

        # implement_task
        result = registry.execute("implement_task", ctx, {"objective": "fix bug", "files": ["a.py"]})
        assert result.ok is True
        assert result.data["result_state"] == "fix bug"

        # capture
        result = registry.execute("capture", ctx, {"evidence": "patch applied"})
        assert result.ok is True
        assert result.data["captured"] is True

        # verify_predicates
        result = registry.execute("verify_predicates", ctx, {"predicates": []})
        assert result.ok is True
        assert result.data["all_satisfied"] is True
