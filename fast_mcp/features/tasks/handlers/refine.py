"""handlers/refine.py — acciones del nodo REFINE.

`refine_diagnose`, `route_to_target` y `escalate_refine` estaban declaradas por
el grafo y no tenían handler: el camino de fallo no se podía recorrer.
"""
from __future__ import annotations

from typing import Any

from fast_mcp.features.tasks.domain.task_registry import ExecutionContext, TaskResult

_TARGETS = ("analyze", "implement", "verify", "sprint")


def refine_diagnose(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Diagnóstico de causa raíz antes de re-rutear."""
    target = str(data.get("target") or "").strip().lower()
    reason = str(data.get("reason") or "").strip()
    if target not in _TARGETS:
        return TaskResult.failure(
            f"refine_diagnose: target inválido {target!r}; válidos: {_TARGETS}"
        )
    if len(reason) < 10:
        return TaskResult.failure(
            "refine_diagnose: reason debe explicar la causa (mínimo 10 caracteres)"
        )
    return TaskResult.success(
        data={
            "status": "diagnosed",
            "target": target,
            "reason": reason,
            "source_node": str(data.get("source_node") or ""),
            "attempt": int(data.get("attempt") or 0),
            "knowledge_items": [],
        }
    )


def route_to_target(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Confirma el destino del refinamiento (la transición la hace el caller)."""
    target = str(data.get("target") or "").strip().lower()
    if target not in _TARGETS:
        return TaskResult.failure(f"route_to_target: target inválido {target!r}")
    return TaskResult.success(data={"ok": True, "target": target})


def escalate_refine(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Escala a revisión manual tras N intentos."""
    attempts = int(data.get("attempt") or data.get("previous_attempts") or 0)
    return TaskResult.success(
        data={
            "ok": True,
            "previous_attempts": attempts,
            "message": f"Refinamiento escalado tras {attempts} intento(s)",
        }
    )


def register(registry) -> None:
    for name, fn, desc in (
        ("refine_diagnose", refine_diagnose, "Diagnose refinement root cause"),
        ("route_to_target", route_to_target, "Confirm refinement target"),
        ("escalate_refine", escalate_refine, "Escalate refinement after N attempts"),
    ):
        registry.register(
            name,
            fn,
            metadata={"description": desc, "tags": ["refine"], "scope": "internal"},
        )
