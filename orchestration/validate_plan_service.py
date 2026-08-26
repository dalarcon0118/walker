"""orchestration/validate_plan_service.py — ValidatePlanService.

Cross-feature orchestrator para validate_plan (ADR-051 Fase 4 G10).

Orquesta Delta (SprintApplicationService.validate_plan). El side-effect
validate.py se reduce a un thin adapter.
"""
from __future__ import annotations

import logging
from typing import Any

from returns.pipeline import is_successful
from returns.result import safe

logger = logging.getLogger(__name__)


class ValidatePlanService:
    """Valida todas las specs del plan del sprint."""

    @staticmethod
    def validate_plan(chain_dir: str) -> dict[str, Any]:
        """Valida el plan de forma pura. Puede lanzar excepciones."""
        try:
            from shared.core.engine.domain.models import ExecutionContext
            from features.delta_tracking.application.services.sprint_service import (
                SprintApplicationService,
            )

            ctx = ExecutionContext.create(session_id="", chain_dir=chain_dir)
            result = SprintApplicationService.validate_plan(ctx, {})
            valid = result.get("valid", False)
            errors = result.get("errors", [])
            spec_count = result.get("spec_count", 0)
            specified_count = result.get("specified_count", 0)
            return {
                "ok": valid,
                "valid": valid,
                "errors": errors,
                "spec_count": spec_count,
                "specified_count": specified_count,
            }
        except Exception as e:
            logger.warning("validate_plan: validate_plan fallo (%s: %s)", type(e).__name__, e)
            return {"ok": False, "error": str(e), "valid": False, "errors": [str(e)]}
