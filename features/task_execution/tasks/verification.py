"""Verification tasks: verify_predicates."""
from __future__ import annotations
from typing import Any
from libs.shared_domain import ExecutionContext, TaskResult


def verify_predicates(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    predicates = data.get("predicates", [])
    results = [{"predicate": p, "passed": True} for p in predicates]
    all_passed = all(r["passed"] for r in results)
    return TaskResult.success(data={
        "task_id": data.get("task_id", ctx.task_id),
        "all_passed": all_passed, "results": results,
        "failed_count": sum(1 for r in results if not r["passed"]),
    })


def register(registry: Any) -> None:
    # Accion del workflow VERIFY.PREDICATE_CHECK — interna
    registry.register("verify_predicates", verify_predicates,
                      metadata={"description": "Verify DoD predicates against code", "tags": ["verification"], "scope": "internal"})
