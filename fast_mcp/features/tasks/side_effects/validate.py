"""side_effects/validate.py — Side effect: validate plan specs.

Simplified from legacy: validates that specs have required fields.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def validate_plan(chain_dir: str, params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Validate plan specs."""
    specs = params.get("specs", [])
    if not isinstance(specs, list):
        specs = []

    errors = []
    for i, spec in enumerate(specs):
        if not spec.get("name"):
            errors.append(f"spec[{i}]: missing name")
        if not spec.get("objective"):
            errors.append(f"spec[{i}]: missing objective")

    valid = len(errors) == 0
    return {
        "ok": valid,
        "valid": valid,
        "errors": errors,
        "spec_count": len(specs),
        "specified_count": len(specs) - len(errors),
    }


def register(registry) -> None:
    """Register validate_plan."""
    registry.register("validate_plan", validate_plan)
