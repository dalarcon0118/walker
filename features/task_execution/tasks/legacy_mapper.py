"""Legacy mapper tasks — thin adapters to project/ use cases."""
from __future__ import annotations
from typing import Any
from libs.shared_domain import ExecutionContext, TaskResult
from features.project import ProjectService


def detect_legacy(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    path = data.get("path", ctx.chain_dir or __import__("os").getcwd())
    try:
        result = ProjectService.detect_legacy(path)
        return TaskResult.success(data=result)
    except Exception as e:
        return TaskResult.success(data={"is_legacy": False, "error": str(e)})


def map_legacy_workflow(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    return TaskResult.success(data={"mapped": True, "workflow_version": "4.0"})


def import_legacy_history(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    path = data.get("path", ctx.chain_dir or __import__("os").getcwd())
    project_id = data.get("project_id", "")
    chains = data.get("chains", [])
    try:
        result = ProjectService.import_legacy_history(path, project_id, chains)
        return TaskResult.success(data=result)
    except Exception as e:
        return TaskResult.failure(str(e))


def import_legacy_tasks(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    path = data.get("path", ctx.chain_dir or __import__("os").getcwd())
    project_id = data.get("project_id", "")
    chains = data.get("chains", [])
    try:
        result = ProjectService.import_legacy_tasks(path, project_id, chains)
        return TaskResult.success(data=result)
    except Exception as e:
        return TaskResult.failure(str(e))


def register(registry: Any) -> None:
    for name, fn, desc in [
        ("detect_legacy", detect_legacy, "Detect legacy walker project"),
        ("map_legacy_workflow", map_legacy_workflow, "Map legacy workflow to v4"),
        ("import_legacy_history", import_legacy_history, "Import legacy chain history"),
        ("import_legacy_tasks", import_legacy_tasks, "Import legacy tasks"),
    ]:
        registry.register(name, fn, metadata={"description": desc, "tags": ["legacy"]})
