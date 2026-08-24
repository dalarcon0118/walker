"""update_incremental_code task — incremental CPG sync via watcher.

Thin wrapper over inspect_codebase with fixed action="update".
Only re-indexes changed files; does NOT rebuild from scratch.
"""
from __future__ import annotations

import logging
from typing import Any

from shared.common.shared_domain import ExecutionContext, TaskResult

logger = logging.getLogger(__name__)


def update_incremental_code(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Incremental CPG update: only changed files since last scan.

    Args:
        ctx: execution context with chain_dir.
        data: optional project_id override.

    Returns:
        TaskResult with update summary (files_updated, nodes_added, etc.).
    """
    chain_dir = ctx.chain_dir or ""
    if chain_dir:
        from features.project import ProjectService
        paths = ProjectService.resolve_paths(chain_dir)
        project_root = paths.repo_path or paths.project_root
    elif data.get("project_id"):
        from shared.core.path_resolver import get_project_dir
        project_root = str(get_project_dir(data["project_id"]))
    else:
        return TaskResult.success(data={"built": False, "error": "No chain_dir or project_id"})

    from features.main.task_execution.tasks.inspect_codebase import inspect_codebase

    payload: dict[str, Any] = {"scope": project_root, "action": "update"}
    result = inspect_codebase(ctx, payload)
    if not result.is_success():
        error = result.error or "unknown error"
        logger.warning("update_incremental_code failed (%s)", error)
        return TaskResult.success(
            data={"updated": False, "error": str(error), "next_step": "Check watcher configuration"}
        )

    update_data = result.data or {}
    return TaskResult.success(
        data={
            "updated": True,
            "project_root": project_root,
            "result": update_data,
        }
    )


def register(registry: Any) -> None:
    registry.register(
        "update_incremental_code",
        update_incremental_code,
        metadata={
            "description": (
                "Incremental CPG update via watcher: re-indexes only changed files. "
                "Use this for routine maintenance. For full rebuild, use build_code_index."
            ),
            "usage": 'walker <project>/update_incremental_code',
            "example": 'walker <project>/update_incremental_code',
            "query_instructions": (
                "When to use: "
                "(1) After code changes to refresh the CPG without full rebuild; "
                "(2) Routine maintenance; "
                "(3) Do NOT use for initial setup or corrupted index — use build_code_index instead."
            ),
            "tags": ["code", "cpg", "incremental", "watcher"],
            "scope": "public",
            "input_schema": {
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "string",
                        "description": "Project ID override (optional if context resolves it).",
                    }
                },
            },
            "output_schema": {
                "type": "object",
                "properties": {
                    "updated": {"type": "boolean"},
                    "project_root": {"type": "string"},
                    "result": {
                        "type": "object",
                        "properties": {
                            "mode": {"type": "string"},
                            "files_updated": {"type": "integer"},
                            "nodes": {"type": "integer"},
                            "edges": {"type": "integer"},
                        },
                    },
                },
                "required": ["updated", "project_root", "result"],
            },
        },
    )
