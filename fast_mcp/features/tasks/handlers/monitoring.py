"""handlers/monitoring.py — MONITORING (Gollwitzer, efecto no-ocurrencia).

Sub-state post-ejecución: verificar que lo implementado cumple lo planificado
 y que NO apareció lo no-deseado (efecto de no-ocurrencia del §monitoring de
 rule.md). Registra desviaciones, no las corrige.
"""
from __future__ import annotations

from typing import Any

from fast_mcp.features.tasks.domain.task_registry import ExecutionContext, TaskResult


def monitoring(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Registra la verificación de cumplimiento vs plan (desviaciones incluidas).

    Espera en data: plan (lo comprometido), deviations (lo observado distinto),
    non_occurrence (lo que NO debía pasar y no pasó).
    """
    plan = data.get("plan", [])
    deviations = data.get("deviations", [])
    non_occurrence = data.get("non_occurrence", [])
    task_id = data.get("task_id") or ctx.task_id

    return TaskResult.success(data={
        "task_id": task_id,
        "phase": "monitoring",
        "plan": plan,
        "deviations": deviations,
        "non_occurrence": non_occurrence,
        "compliant": not deviations,
        "chain_dir": ctx.chain_dir,
    })


def register(registry) -> None:
    """Register monitoring (Gollwitzer phase)."""
    registry.register("monitoring", monitoring, metadata={
        "description": "MONITORING: plan-vs-result verification, non-occurrence effect (Gollwitzer)",
        "tags": ["gollwitzer", "action-phase"],
        "scope": "internal",
    })
