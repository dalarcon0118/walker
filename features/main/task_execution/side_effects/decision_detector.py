"""decision_detector.py — Deteccion automatica de decisiones desde watcher changes.

Thin adapter: delega la orquestación cross-feature (Project × CKG) en
DecisionDetectorService (orchestration/).
"""
from __future__ import annotations
import logging
from typing import Any
from shared.common.shared_domain import ExecutionContext
from shared.core.engine.domain.side_effect_contract import as_side_effect, SCOPE_DECISION

logger = logging.getLogger(__name__)


@as_side_effect(scope=SCOPE_DECISION)
def detect_decisions(ctx: ExecutionContext, params: dict[str, Any]) -> dict[str, Any]:
    """Thin adapter: delega en DecisionDetectorService."""
    from orchestration.decision_detector_service import DecisionDetectorService

    return DecisionDetectorService.detect_decisions(
        chain_dir=ctx.chain_dir or "",
        task_id=ctx.task_id or "",
        session_id=getattr(ctx, "session_id", ""),
        params=params,
    )


def register(engine: Any) -> None:
    engine.register("detect_decisions", detect_decisions)
