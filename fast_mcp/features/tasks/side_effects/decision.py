"""side_effects/decision.py — Side effect: capture decision from workflow state.

Simplified from legacy (196 LOC → ~40 LOC): reads decision directly from params.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def capture_decision(chain_dir: str, params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Capture a decision from the workflow."""
    decision = params.get("decision", "")
    rationale = params.get("feedback", "") or params.get("evidence", "")
    sub_state = params.get("sub_state", "?")
    task_id = params.get("task_id", "")

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
