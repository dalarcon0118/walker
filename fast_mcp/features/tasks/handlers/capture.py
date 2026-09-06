"""handlers/capture.py — capture handler.

Records evidence/artifacts for a task.
"""
from __future__ import annotations

from typing import Any

from fast_mcp.features.tasks.domain.task_registry import ExecutionContext, TaskResult
from fast_mcp.features.tasks.handlers._inspect_codebase_helper import run_inspect_codebase


def capture(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Capture evidence/artifacts for a task.

    W5 (ADR-076): si ctx.cpg_adapter está disponible y `data` incluye
    `inspect_codebase_action`, ejecuta la action y añade el resultado a
    los artifacts.
    """
    evidence = data.get("evidence", "")
    artifacts = data.get("artifacts", [])
    task_id = data.get("task_id") or ctx.task_id

    inspect_result = run_inspect_codebase(ctx.cpg_adapter, data)
    if inspect_result is not None:
        artifacts.append({"type": "inspect_codebase", "result": inspect_result})

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
