"""handlers/state_construction.py — STATE CONSTRUCTION (Gollwitzer).

Sub-state de deliberación: el LLM construye el estado mental del trabajo
 (interfaz, huecos, plan) ANTES de cruzar al compromiso de implementación.
"""
from __future__ import annotations

from typing import Any

from fast_mcp.features.tasks.domain.task_registry import ExecutionContext, TaskResult


def state_construction(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Registra el estado construido: interfaz propuesta, huecos, plan."""
    interface = data.get("interface", {})
    gaps = data.get("gaps", [])
    plan = data.get("plan", [])
    task_id = data.get("task_id") or ctx.task_id

    return TaskResult.success(data={
        "task_id": task_id,
        "phase": "state_construction",
        "interface": interface,
        "gaps": gaps,
        "plan": plan,
        "chain_dir": ctx.chain_dir,
        "constructed": True,
    })


def register(registry) -> None:
    """Register state_construction (Gollwitzer phase)."""
    registry.register("state_construction", state_construction, metadata={
        "description": "STATE CONSTRUCTION: mental state of the work (Gollwitzer)",
        "tags": ["gollwitzer", "action-phase"],
        "scope": "internal",
    })
