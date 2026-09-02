"""handlers/capture.py — capture handler.

Records evidence/artifacts for a task.
"""
from __future__ import annotations

from typing import Any

from fast_mcp.features.tasks.domain.task_registry import ExecutionContext, TaskResult


def capture(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Capture evidence/artifacts for a task."""
    evidence = data.get("evidence", "")
    artifacts = data.get("artifacts", [])
    task_id = data.get("task_id") or ctx.task_id

    return TaskResult.success(data={
        "task_id": task_id,
        "captured": True,
        "evidence": evidence,
        "artifacts": artifacts,
        "chain_dir": ctx.chain_dir,
    })


def register(registry) -> None:
    """Register capture."""
    registry.register("capture", capture, metadata={
        "description": "Capture task evidence/artifacts",
        "tags": ["evidence"],
        "scope": "internal",
    })
