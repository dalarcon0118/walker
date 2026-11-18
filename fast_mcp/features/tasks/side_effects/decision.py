"""side_effects/decision.py — Side effect: capture decision from workflow state.

Simplified from legacy (196 LOC → ~40 LOC): reads decision directly from params.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def capture_decision(chain_dir: str, params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Capture a decision from the workflow.

    Los gates `user_input` envían la decisión en `fields`, no en `params` (el
    nodo declara `params: {}`). Leer solo `params` hacía fallar TODO submit de
    un gate de decisión y, como el efecto es no opcional, bloqueaba el cierre
    de la aprobación (medido 2026-11-18: PLAN_APPROVAL devolvía
    "no decision provided").
    """
    fields = kwargs.get("fields") or {}
    if not isinstance(fields, dict):
        fields = {}
    state = kwargs.get("state")

    decision = params.get("decision") or fields.get("decision") or ""
    rationale = (
        params.get("feedback")
        or params.get("evidence")
        or fields.get("feedback")
        or fields.get("evidence")
        or ""
    )
    # `advance` después de `submit` re-ejecuta los efectos con `fields` vacío.
    # La decisión ya está registrada en el estado; sin este fallback el efecto
    # fallaba ("no decision provided") y el paso siguiente se saltaba (medido
    # 2026-11-18: DESIGN_REVIEW -> CKG_SAVE se perdió).
    recorded: dict = {}
    if state is not None:
        sub = str(getattr(state, "current_sub_state", "") or "")
        sub_data = getattr(state, "sub_state_data", None)
        if isinstance(sub_data, dict):
            entry = sub_data.get(sub)
            recorded = getattr(entry, "fields", None) or (
                entry if isinstance(entry, dict) else {}
            ) or {}
    if not decision and isinstance(recorded, dict):
        decision = str(recorded.get("decision") or "")
    if not rationale and isinstance(recorded, dict):
        rationale = str(recorded.get("evidence") or recorded.get("feedback") or "")
    sub_state = params.get("sub_state") or getattr(state, "current_sub_state", "") or "?"
    ctx = getattr(state, "context", {}) or {}
    task_id = params.get("task_id") or (ctx.get("task_id") if isinstance(ctx, dict) else "") or ""

    if not decision:
        return {"ok": False, "error": "no decision provided"}

    return {
        "ok": True,
        "decision": decision,
        "rationale": rationale,
        "sub_state": sub_state,
        "task_id": task_id,
        "chain_dir": chain_dir,
    }


def register(registry) -> None:
    """Register capture_decision."""
    registry.register("capture_decision", capture_decision)
