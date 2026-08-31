"""Sprint tracking side-effects: index_sprint_tasks, rebuild_sprint_index.

Thin adapters: delegan la orquestación cross-feature (Project × Delta) en
SprintTrackingService (app/orchestration/).
"""
from __future__ import annotations
import logging
from typing import Any
from shared.core.engine.domain.execution import ExecutionContext
from shared.core.engine.domain.side_effect_contract import as_side_effect, SCOPE_TRACKING

logger = logging.getLogger(__name__)


@as_side_effect(scope=SCOPE_TRACKING)
def index_sprint_tasks(ctx: ExecutionContext, params: dict[str, Any]) -> dict[str, Any]:
    """Thin adapter: delega en SprintTrackingService."""
    from app.orchestration.sprint_tracking_service import SprintTrackingService

    return SprintTrackingService.index_sprint_tasks(chain_dir=ctx.chain_dir or "")


@as_side_effect(scope=SCOPE_TRACKING)
def rebuild_sprint_index(ctx: ExecutionContext, params: dict[str, Any]) -> dict[str, Any]:
    """Thin adapter: delega en SprintTrackingService."""
    from app.orchestration.sprint_tracking_service import SprintTrackingService

    return SprintTrackingService.rebuild_sprint_index(chain_dir=ctx.chain_dir or "")


def register(engine: Any) -> None:
    engine.register("index_sprint_tasks", index_sprint_tasks)
    engine.register("rebuild_sprint_index", rebuild_sprint_index)
