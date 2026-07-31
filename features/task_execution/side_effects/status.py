"""Side-effect: update_sprint_status — cambia el estado del sprint.
    
SRP: Unica responsabilidad = actualizar el estado del sprint (planning/active/reviewing/closed).
Se invoca desde los side_effects de los nodos del workflow.
"""
from __future__ import annotations
from typing import Any
from features.engine.domain.models import ExecutionContext
from features.spring.application.use_cases import UpdateSprintStatusUseCase


def update_sprint_status(ctx: ExecutionContext, params: dict[str, Any]) -> dict[str, Any]:
    """Side-effect handler: updates sprint status.
    
    Params:
        status: nuevo estado (planning, active, reviewing, closed)
    """
    try:
        result = UpdateSprintStatusUseCase.execute(ctx, params)
        return {"ok": True, **result}
    except (ValueError, FileNotFoundError) as e:
        return {"ok": False, "error": str(e)}


def register(engine: Any) -> None:
    engine.register("update_sprint_status", update_sprint_status)
