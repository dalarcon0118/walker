"""Edge transform: resolves SPRINT.ACTIVE vs SPRINT.PLANNING from project state."""
from __future__ import annotations
import os
from typing import Any
from libs.shared_domain import ExecutionContext, TaskResult
from features.engine.domain.session import SessionStore
from features.project import ProjectService


def resolve_project_sprint_handler(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    fields = data.get("SELECT.fields", {})
    project_id = fields.get("project_id", "")
    if not project_id:
        return TaskResult.failure({"message": "project_id no especificado en SELECT"})

    sprint_chain_dir = ProjectService.find_active_sprint(project_id)
    if sprint_chain_dir and _has_sprint_state(sprint_chain_dir):
        _update_session(ctx, project_id, sprint_chain_dir)
        return TaskResult.success({
            "target_node": "SPRINT", "target_sub_state": "ACTIVE",
            "chain_dir": sprint_chain_dir, "project_id": project_id,
        })
    else:
        _update_session(ctx, project_id, "")
        return TaskResult.success({
            "target_node": "SPRINT", "target_sub_state": "PLANNING",
            "chain_dir": "", "project_id": project_id,
        })


def _has_sprint_state(chain_dir: str) -> bool:
    """Check if sprint-state.json exists via SprintStateRepository (SSOT)."""
    from features.spring.infrastructure.repositories import SprintStateRepository
    return bool(SprintStateRepository().load_dict(chain_dir))


def _update_session(ctx: ExecutionContext, project_id: str, chain_dir: str) -> None:
    try:
        if ctx.session_id:
            record = SessionStore.load(ctx.session_id)
            if record:
                record.project_id = project_id
                if chain_dir:
                    record.chain_dir = chain_dir
                SessionStore.save(record)
                SessionStore.set_active(ctx.session_id)
    except Exception:
        pass


def register(registry: Any) -> None:
    registry.register("resolve_project_sprint", resolve_project_sprint_handler,
                      metadata={"description": "Resolve target sub-state from project's active sprint", "tags": ["transform", "project"]})
