"""Task: update_project — update project.json with schema validation."""
from __future__ import annotations
from typing import Any
from libs.shared_domain import ExecutionContext, TaskResult
from features.project import ProjectService


def update_project(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    chain_dir = ctx.chain_dir or ""
    if not chain_dir:
        return TaskResult.failure("chain_dir not available")
    try:
        # Build nested update data from flat flags
        update_data: dict[str, dict] = {}
        for key, val in data.items():
            if "." in key:
                section, field = key.split(".", 1)
                update_data.setdefault(section, {})[field] = val
            else:
                update_data.setdefault("identity", {})[key] = val
        result = ProjectService.update_project(chain_dir, update_data)
        return TaskResult.success(data=result)
    except (FileNotFoundError, ValueError) as e:
        return TaskResult.failure(str(e))


def register(registry: Any) -> None:
    registry.register("update_project", update_project,
                      metadata={"description": "Update project.json with schema validation",
                                "tags": ["project", "config"],
                                "purpose": ["project_update", "config_update"]})
