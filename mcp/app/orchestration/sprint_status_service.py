"""app/orchestration/sprint_status_service.py — SprintStatusService.

Cross-feature orchestrator para sprint_status (ADR-051 Fase 4 G10).

Orquesta Delta (SprintApplicationService.update_sprint_status). El side-effect
status.py se reduce a un thin adapter.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class SprintStatusService:
    """Actualiza el estado del sprint."""

    @staticmethod
    def update_sprint_status(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
        """Side-effect handler: updates sprint status."""
        try:
            from features.delta_tracking.application.services.sprint_service import (
                SprintApplicationService,
            )

            result = SprintApplicationService.update_sprint_status(ctx, params)
            return {"ok": True, **result}
        except (ValueError, FileNotFoundError) as e:
            logger.warning(
                "sprint_status: update_sprint_status fallo (%s: %s)", type(e).__name__, e,
            )
            return {"ok": False, "error": str(e)}
