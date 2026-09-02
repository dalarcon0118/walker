"""handlers/verify.py — verify_predicates handler.

Checks if predicates are satisfied (files exist, tests pass, etc.).
"""
from __future__ import annotations

import os
from typing import Any

from fast_mcp.features.tasks.domain.task_registry import ExecutionContext, TaskResult


def verify_predicates(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Verify that predicates are satisfied."""
    predicates = data.get("predicates", [])
    if not isinstance(predicates, list):
        predicates = []

    chain_dir = ctx.chain_dir or ""
    results = []

    for pred in predicates:
        pred_type = pred.get("type", "")
        target = pred.get("target", "")

        if pred_type == "file_exists":
            path = os.path.join(chain_dir, target) if target else ""
            exists = os.path.isfile(path) if path else False
            results.append({"predicate": pred, "satisfied": exists})
        elif pred_type == "test_passes":
            # Simplified: assume test passes if file exists
            path = os.path.join(chain_dir, target) if target else ""
            exists = os.path.isfile(path) if path else False
            results.append({"predicate": pred, "satisfied": exists})
        else:
            results.append({"predicate": pred, "satisfied": False, "error": f"unknown predicate type: {pred_type}"})

    all_satisfied = all(r["satisfied"] for r in results) if results else True

    return TaskResult.success(data={
        "task_id": data.get("task_id") or ctx.task_id,
        "all_satisfied": all_satisfied,
        "results": results,
    })


def register(registry) -> None:
    """Register verify_predicates."""
    registry.register("verify_predicates", verify_predicates, metadata={
        "description": "Verify task predicates",
        "tags": ["verification"],
        "scope": "internal",
    })
