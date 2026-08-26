"""Side-effect: update_sprint_status — cambia el estado del sprint.

Thin adapter: delega en SprintStatusService (orchestration/).
"""
from __future__ import annotations
import logging
from typing import Any
from shared.core.engine.domain.models import ExecutionContext
from shared.core.engine.domain.side_effect_contract import as_side_effect, SCOPE_TRACKING

logger = logging.getLogger(__name__)


@as_side_effect(scope=SCOPE_TRACKING)
def sprint_status(ctx: ExecutionContext, params: dict[str, Any]) -> dict[str, Any]:
    """Thin adapter: delega en SprintStatusService."""
    from orchestration.sprint_status_service import SprintStatusService

    return SprintStatusService.update_sprint_status(ctx, params)


def register(engine: Any) -> None:
    engine.register("sprint_status", sprint_status)
