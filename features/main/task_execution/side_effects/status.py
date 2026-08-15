"""Side-effect: update_sprint_status — cambia el estado del sprint.
    
SRP: Unica responsabilidad = actualizar el estado del sprint (planning/active/reviewing/closed).
Se invoca desde los side_effects de los nodos del workflow.

ADR-WALKER-048: los side-effects consumen Application Services, nunca Use
Cases directos (INV-013).

Result pattern (convention-001): ante fallo se degrada con fallback explícito
+ log; nunca except silencioso.
"""
from __future__ import annotations
import logging
from typing import Any
from core.engine.domain.models import ExecutionContext
from core.engine.domain.side_effect_contract import as_side_effect, SCOPE_TRACKING
from features.delta_tracking.application.services.sprint_service import (
    SprintApplicationService,
)

logger = logging.getLogger(__name__)


@as_side_effect(scope=SCOPE_TRACKING)
def sprint_status(ctx: ExecutionContext, params: dict[str, Any]) -> dict[str, Any]:
    """Side-effect handler: updates sprint status.
    
    Params:
        status: nuevo estado (planning, active, reviewing, closed)
    """
    try:
        result = SprintApplicationService.update_sprint_status(ctx, params)
        return {"ok": True, **result}
    except (ValueError, FileNotFoundError) as e:
        logger.warning(
            "status: sprint_status fallo (%s: %s)", type(e).__name__, e,
        )
        return {"ok": False, "error": str(e)}


def register(engine: Any) -> None:
    engine.register("sprint_status", sprint_status)
