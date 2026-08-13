"""Edge transform: resolves SPRINT.ACTIVE vs SPRINT.PLANNING from project state.

Result pattern (dry-python/returns): los cores con @safe nunca lanzan; los
wrappers degradan con fallback explícito y log (convention-001).
"""
from __future__ import annotations
import logging
from typing import Any
from returns.pipeline import is_successful
from returns.result import safe
from shared.common.shared_domain import ExecutionContext, TaskResult
from core.engine.domain.session import SessionStore
from features.project import ProjectService

logger = logging.getLogger(__name__)


def resolve_project_sprint_handler(
    ctx: ExecutionContext, data: dict[str, Any]
) -> TaskResult:
    fields = data.get("SELECT.fields", {})
    project_id = fields.get("project_id", "")
    if not project_id:
        return TaskResult.failure({"message": "project_id no especificado en SELECT"})

    sprint_chain_dir = ProjectService.find_active_sprint(project_id)
    if sprint_chain_dir and _has_sprint_state(sprint_chain_dir):
        _update_session(ctx, project_id, sprint_chain_dir)
        return TaskResult.success(
            {
                "target_node": "SPRINT",
                "target_sub_state": "ACTIVE",
                "chain_dir": sprint_chain_dir,
                "project_id": project_id,
            }
        )
    else:
        _update_session(ctx, project_id, "")
        return TaskResult.success(
            {
                "target_node": "SPRINT",
                "target_sub_state": "PLANNING",
                "chain_dir": "",
                "project_id": project_id,
            }
        )


@safe
def _has_sprint_state_core(chain_dir: str) -> bool:
    """Core puro: verifica que el sprint tenga estado en el delta (puede lanzar)."""
    from features.delta_tracking import spring_service

    spring_svc = spring_service(chain_dir=chain_dir)
    if not spring_svc:
        return False
    spring_id = spring_svc.get_spring_id(chain_dir)
    spring_svc.ensure_spring_imported(chain_dir, spring_id)
    return spring_svc.get_spring(spring_id) is not None


def _has_sprint_state(chain_dir: str) -> bool:
    """Verifica que el sprint tenga estado en el delta (fuente unica).

    Result pattern (convention-001): ante fallo del core se degrada a False
    con log explícito (nunca silencioso).
    """
    result = _has_sprint_state_core(chain_dir)
    if is_successful(result):
        return result.value_or(False)
    error = result.failure()
    logger.warning(
        "resolve_project_sprint: _has_sprint_state fallo (%s: %s); degrade a False",
        type(error).__name__, error,
    )
    return False


@safe
def _update_session_core(record: Any, project_id: str, chain_dir: str) -> None:
    """Core puro: actualiza la sesión activa (puede lanzar)."""
    record.project_id = project_id
    if chain_dir:
        record.chain_dir = chain_dir
    SessionStore.save(record)
    SessionStore.set_active(record.session_id)


def _update_session(ctx: ExecutionContext, project_id: str, chain_dir: str) -> None:
    """Actualiza la sesión activa con fallback explícito + log (convention-001)."""
    if not ctx.session_id:
        return
    try:
        record = SessionStore.load(ctx.session_id)
    except Exception as e:
        logger.warning(
            "resolve_project_sprint: SessionStore.load fallo (%s: %s)",
            type(e).__name__, e,
        )
        return
    if not record:
        return
    result = _update_session_core(record, project_id, chain_dir)
    if not is_successful(result):
        error = result.failure()
        logger.warning(
            "resolve_project_sprint: _update_session fallo (%s: %s)",
            type(error).__name__, error,
        )



