"""side_effects/decision_detector.py — Side effect: detect decisions from state.

Detects if a decision was made during workflow execution.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def detect_decision(chain_dir: str, params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Detect if a decision was made."""
    decision = params.get("decision", "")
    has_decision = bool(decision)

    return {
        "ok": True,
        "has_decision": has_decision,
        "decision": decision,
        "chain_dir": chain_dir,
    }


def register(registry) -> None:
    """Register detect_decision."""
    registry.register("detect_decision", detect_decision)
