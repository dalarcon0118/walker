"""side_effects/status.py — Side effect: status update on workflow transitions."""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def update_status(chain_dir: str, params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Update status after a workflow transition."""
    node = params.get("node", "")
    sub_state = params.get("sub_state", "")

    return {
        "ok": True,
        "node": node,
        "sub_state": sub_state,
        "chain_dir": chain_dir,
    }


def register(registry) -> None:
    """Register update_status."""
    registry.register("update_status", update_status)
