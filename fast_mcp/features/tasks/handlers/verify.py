"""handlers/verify.py — verify_predicates handler (anti-hallucination, G-W6).

Uses the verify_engine from infrastructure/ for real predicate checking:
  - file_exists, symbol_exists, test_pass, command_exit, grep, etc.
  - Empty predicates → failure (never empty success, G-W6).
  - Shell commands, AST-level verification, batch optimization.
"""
from __future__ import annotations

import logging
from typing import Any

from fast_mcp.features.tasks.domain.task_registry import ExecutionContext, TaskResult
from fast_mcp.features.tasks.infrastructure.verify_engine import (
    invalidate_verify_cache,
    verify_predicates_batch,
)
from fast_mcp.features.tasks.handlers._inspect_codebase_helper import run_inspect_codebase

logger = logging.getLogger(__name__)


def verify_predicates(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Verify predicates against real code using the verify engine.

    G-W6 anti-hallucination: empty predicates → failure.

    W5 (ADR-076): si ctx.cpg_adapter está disponible y `data` incluye
    `inspect_codebase_action`, ejecuta la action y añade el resultado.
    """
    if data.get("_fresh"):
        invalidate_verify_cache()

    predicates = data.get("predicates", [])
    if not isinstance(predicates, list):
        predicates = []

    project_root = data.get("project_root") or ctx.chain_dir or ""
    if not project_root:
        return TaskResult.failure(
            "verify_predicates: no project_root/chain_dir — cannot verify"
        )

    # G-W6: empty predicates = no verification criteria = failure
    if not predicates:
        return TaskResult.failure(
            "verify_predicates: predicates vacíos — "
            "el gate no puede validar nada. Inyecta task.validation.predicates "
            "desde la spec (DeltaGraph) o define predicates en la spec."
        )

    results = verify_predicates_batch(predicates, project_root)

    detailed = []
    failed = 0
    for pred, (passed, info) in zip(predicates, results):
        if not passed:
            failed += 1
        detailed.append({
            "predicate": info.get("label") or (pred if isinstance(pred, str) else str(pred)),
            "method": info.get("method", "unknown"),
            "passed": passed,
            "detail": info,
        })

    result_data = {
        "task_id": data.get("task_id") or ctx.task_id,
        "all_satisfied": failed == 0,
        "results": detailed,
        "failed_count": failed,
        "passed_count": len(detailed) - failed,
        "total": len(detailed),
    }

    inspect_result = run_inspect_codebase(ctx.cpg_adapter, data)
    if inspect_result is not None:
        result_data["inspect_codebase"] = inspect_result
        # W6 (ADR-076): action=imports detecta imports alucinados;
        # si hay Hallucinations, fallar el predicado con el listado.
        if data.get("inspect_codebase_action") == "imports":
            hallucinations = inspect_result.get("hallucinations", 0)
            if hallucinations > 0:
                result_data["all_satisfied"] = False
                result_data["hallucinations_found"] = True
                result_data["failed_count"] = result_data.get("failed_count", 0) + hallucinations
                result_data["total"] = result_data.get("total", 0) + hallucinations
                result_data["results"].append({
                    "predicate": "imports:no_hallucinations",
                    "method": "hallucination_detector",
                    "passed": False,
                    "detail": {
                        "hallucinations": hallucinations,
                        "issues": inspect_result.get("issues", [])[:10],
                    },
                })

    return TaskResult.success(data=result_data)


def register(registry) -> None:
    """Register verify_predicates."""
    registry.register("verify_predicates", verify_predicates, metadata={
        "description": "Verify task predicates against code (anti-hallucination)",
        "tags": ["verification"],
        "scope": "internal",
    })
