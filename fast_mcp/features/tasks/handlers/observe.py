"""handlers/observe.py — OBSERVE (Gollwitzer, psicología de la acción).

Sub-state de primera fase de acción: recopilación de evidencia del mundo
 antes de actuar. No decide, no planifica — solo observa el repositorio.
"""
from __future__ import annotations

from typing import Any

from fast_mcp.features.tasks.domain.task_registry import ExecutionContext, TaskResult


def observe(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Recopila evidencia de observación del repositorio (info, no decisión).

    Espera en data: paths (archivos tocados), evidence, task_id.
    """
    paths = data.get("paths", [])
    evidence = data.get("evidence", "")
    task_id = data.get("task_id") or ctx.task_id

    return TaskResult.success(data={
        "task_id": task_id,
        "phase": "observe",
        "paths": paths,
        "evidence": evidence,
        "chain_dir": ctx.chain_dir,
        "observed": True,
    })


def register(registry) -> None:
    """Register observe (Gollwitzer phase)."""
    registry.register("observe", observe, metadata={
        "description": "OBSERVE phase: repository evidence before acting (Gollwitzer)",
        "tags": ["gollwitzer", "action-phase"],
        "scope": "internal",
    })
