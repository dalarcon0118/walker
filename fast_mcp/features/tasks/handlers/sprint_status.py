"""handlers/sprint_status.py — transición de estado de un sprint.

ADR-WALKER-078: `paused` es reversible (`active <-> paused`). El rechazo no es
silencioso: devuelve el motivo y las transiciones permitidas desde el estado
actual. Acepta `spring_id` o `session_id` (resuelve el sprint de la sesión).
"""

from __future__ import annotations

from typing import Any

from fast_mcp.features.tasks.domain.task_registry import ExecutionContext, TaskResult
from fast_mcp.features.tasks.handlers import _di


def sprint_status(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    status = str(data.get("status") or "").strip()
    if not status:
        return TaskResult.failure("sprint_status: status requerido (planning|active|paused|reviewing|closed)")
    spring_id = str(data.get("spring_id") or "").strip()
    session_id = str(data.get("session_id") or "").strip()
    service = _di.sprint_service(ctx.chain_dir)
    if service is None:
        return TaskResult.failure("sprint_status: sprint service no disponible")
    if not spring_id and session_id:
        record = service.get_session(session_id) or {}
        spring_id = str(record.get("spring_id") or "")
        if not spring_id:
            return TaskResult.failure(
                "sprint_status: la sesión no tiene sprint ligado; usá session_bind"
            )
    if not spring_id:
        return TaskResult.failure("sprint_status: spring_id o session_id requerido")
    result = service.transition_spring(spring_id, status)
    if not result.get("ok"):
        return TaskResult.failure(f"sprint_status: {result.get('error')}")
    return TaskResult.success(result)


def register(registry) -> None:
    registry.register(
        "sprint_status",
        sprint_status,
        metadata={"description": "Transition a sprint status", "tags": ["sprint"], "scope": "internal"},
    )
