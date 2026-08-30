"""decision_capture.py — Side-effect: captura decisiones del PO en el CKG.

Thin adapter: delega la orquestación cross-feature (Project × CKG) en
DecisionCaptureService (app/orchestration/).
"""
from __future__ import annotations
import logging
from typing import Any
from returns.result import safe
from returns.pipeline import is_successful
from shared.common.shared_domain import ExecutionContext
from shared.core.engine.domain.side_effect_contract import as_side_effect, SCOPE_DECISION

logger = logging.getLogger(__name__)


@safe
def _read_decision_from_state_core(chain_dir: str) -> dict:
    """Core puro: lee la decision del estado del workflow (puede lanzar)."""
    from shared.core.engine.infrastructure.repositories import WorkflowStateRepository

    state = WorkflowStateRepository().load(chain_dir)
    ss_data = state.get_sub_state_data(state.current_sub_state)
    return {
        "decision": ss_data.fields.get("decision", "") or "",
        "feedback": ss_data.fields.get("feedback", "") or "",
        "evidence": ss_data.fields.get("evidence", "") or "",
        "sub_state": f"{state.current_node}.{state.current_sub_state}",
    }


@as_side_effect(scope=SCOPE_DECISION)
def capture_decision(ctx: ExecutionContext, params: dict[str, Any]) -> dict[str, Any]:
    """Thin adapter: delega en DecisionCaptureService."""
    from app.orchestration.decision_capture_service import DecisionCaptureService

    chain_dir = ctx.chain_dir or ""
    if not chain_dir:
        return {"ok": False, "error": "chain_dir not available"}

    decision = params.get("decision", "") or ""
    rationale = params.get("feedback", "") or params.get("evidence", "") or ""
    sub_state = params.get("sub_state", "") or "?"

    # Si el workflow no paso la decision en params, leerla del estado:
    if not decision:
        from returns.pipeline import is_successful

        state_result = _read_decision_from_state_core(chain_dir)
        if is_successful(state_result):
            state_data = state_result.value_or({})
            decision = state_data.get("decision", "")
            rationale = rationale or state_data.get("feedback", "") or state_data.get("evidence", "")
            sub_state = state_data.get("sub_state", "?")
        else:
            logger.warning(
                "decision_capture: no se pudo leer la decision del estado (%s: %s)",
                type(state_result.failure()).__name__, state_result.failure(),
            )

    return DecisionCaptureService.capture_decision(
        chain_dir=chain_dir,
        decision=decision,
        rationale=rationale,
        sub_state=sub_state,
        task_id=ctx.task_id or "",
        session_id=getattr(ctx, "session_id", ""),
    )


def register(engine: Any) -> None:
    engine.register("capture_decision", capture_decision)
