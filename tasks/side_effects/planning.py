"""Sprint planning side-effects: advance_sprint_task.

SSOT de main (SPEC-014 Fase 0): el handler advance_sprint_task vive AQUÍ,
migrado desde el paquete huérfano features/delta_tracking/side_effects.

Thin adapter: delega en AdvanceSprintTaskService (app/orchestration/).
"""

from __future__ import annotations
import logging
from typing import Any
from shared.common.shared_domain import ExecutionContext
from shared.core.engine.domain.side_effect_contract import as_side_effect, SCOPE_TRACKING

logger = logging.getLogger(__name__)


@as_side_effect(scope=SCOPE_TRACKING)
def advance_sprint_task(ctx: ExecutionContext, params: dict[str, Any]) -> dict[str, Any]:
    """Thin adapter: delega en AdvanceSprintTaskService."""
    from app.orchestration.advance_sprint_task_service import AdvanceSprintTaskService

    return AdvanceSprintTaskService.advance_sprint_task(
        chain_dir=ctx.chain_dir or "",
        params=params,
    )


def register(engine: Any) -> None:
    engine.register("advance_sprint_task", advance_sprint_task)
