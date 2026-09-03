"""handlers/sprint_planning.py — Sprint planning task handlers.

Thin adapters between workflow action names and a SprintService-like
object (injected at the composition root). Business logic lives in
delta_tracking; these handlers only connect names.

Per R1 (ADR-071 §3bis): features must NOT import other features.
The SprintService is injected here via closure, never imported directly.

Workflow action names ported from legacy mcp/shared/tasks/actions/
sprint_planning.py:
  - plan_sprint: create a sprint plan from goal/tasks (create spring + specs)
  - spec_task: specify/refine a spec
  - validate_plan: validate all specs in the plan
  - close_sprint: close the current sprint
"""
from __future__ import annotations

import logging
from typing import Any

from fast_mcp.features.tasks.domain.task_registry import ExecutionContext, TaskResult

logger = logging.getLogger(__name__)


def _build_handlers(sprint_service: Any):
    """Return the four sprint-planning handlers bound to `sprint_service`.

    `sprint_service` is a SprintService-like object (from delta_tracking)
    injected by the composition root. It must implement:
      create_spring, add_spec, get_specs, set_spring_status,
      get_active_spring  (and optionally get_spring).
    """

    def plan_sprint(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
        """Create a sprint plan: creates spring + specs from goal/tasks."""
        if sprint_service is None:
            return TaskResult.failure("plan_sprint: sprint_service not configured")

        goal = data.get("goal", "")
        tasks = data.get("tasks", []) or []
        project_id = data.get("project_id", "")
        if not goal:
            return TaskResult.failure("plan_sprint: goal es requerido")

        spring_id = data.get("spring_id") or f"sprint-{project_id or ctx.session_id or 'workflow'}"
        try:
            sprint_service.create_spring(
                spring_id=spring_id,
                goal=goal,
                status="planning",
                project_id=project_id or ctx.session_id,
            )
        except Exception as exc:  # noqa: BLE001
            return TaskResult.failure(f"plan_sprint: {type(exc).__name__}: {exc}")

        specs = []
        for i, task in enumerate(tasks):
            try:
                spec = sprint_service.add_spec(
                    spring_id=spring_id,
                    objective=task.get("objective", task.get("title", "")),
                    domain=task.get("domain", ""),
                    spec_type=task.get("type", "feature"),
                    spec_index=i,
                    result_state=task.get("result_state", ""),
                    files=task.get("files", []),
                )
                specs.append(spec)
            except Exception as exc:  # noqa: BLE001
                logger.warning("plan_sprint: spec %d falló (%s)", i, exc)

        return TaskResult.success(data={
            "spring_id": spring_id,
            "goal": goal,
            "spec_count": len(specs),
            "specs": specs,
        })

    def validate_plan(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
        """Validate all specs in the sprint plan."""
        if sprint_service is None:
            return TaskResult.failure("validate_plan: sprint_service not configured")

        spring_id = data.get("spring_id", "")
        specs = []
        if spring_id:
            try:
                specs = sprint_service.get_specs(spring_id, status="draft") or sprint_service.get_specs(spring_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("validate_plan: get_specs falló (%s)", exc)

        missing = [s for s in specs if not s.get("objective")]
        return TaskResult.success(data={
            "valid": not missing,
            "errors": [f"spec {s.get('_id', '?')}: objective requerido" for s in missing],
            "spec_count": len(specs),
        })

    def spec_task(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
        """Specify/refine a spec. Si no es válida → failure (no auto-advance)."""
        if sprint_service is None:
            return TaskResult.failure("spec_task: sprint_service not configured")

        spring_id = data.get("spring_id", "")
        objective = data.get("objective", "") or data.get("title", "")
        if not spring_id or not objective:
            return TaskResult.failure(
                "spec_task: spec incompleta (spring_id + objective requeridos)"
            )

        try:
            spec = sprint_service.add_spec(
                spring_id=spring_id,
                objective=objective,
                domain=data.get("domain", ""),
                spec_type=data.get("type", "feature"),
                spec_index=data.get("spec_index"),
                result_state=data.get("result_state", ""),
                files=data.get("files", []),
                status=data.get("status", "draft"),
            )
        except Exception as exc:  # noqa: BLE001
            return TaskResult.failure(f"spec_task: {type(exc).__name__}: {exc}")

        spec_id = spec.get("_id", "")
        return TaskResult.success(data={
            "spec_id": spec_id,
            "valid": True,
            "objective": objective,
            "status": spec.get("status", "draft"),
            "task_id": data.get("task_id") or spec_id,
        })

    def close_sprint(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
        """Close the current sprint."""
        if sprint_service is None:
            return TaskResult.failure("close_sprint: sprint_service not configured")

        spring_id = data.get("spring_id", "")
        if not spring_id:
            try:
                active = sprint_service.get_active_spring()
                spring_id = active.get("_id", "") if active else ""
            except Exception as exc:  # noqa: BLE001
                return TaskResult.failure(f"close_sprint: no se pudo resolver spring activo: {exc}")
        if not spring_id:
            return TaskResult.failure("close_sprint: no hay sprint activo")

        try:
            sprint_service.set_spring_status(spring_id, "closed")
        except Exception as exc:  # noqa: BLE001
            return TaskResult.failure(f"close_sprint: {type(exc).__name__}: {exc}")

        return TaskResult.success(data={
            "spring_id": spring_id,
            "status": "closed",
        })

    return plan_sprint, validate_plan, spec_task, close_sprint


def register(registry, sprint_service: Any = None) -> None:
    """Register sprint planning workflow actions.

    Args:
        registry: TaskRegistry to register into.
        sprint_service: SprintService-like object injected by the
            composition root (R1: never imported here directly).
    """
    plan_sprint, validate_plan, spec_task, close_sprint = _build_handlers(sprint_service)
    for name, fn, desc in [
        ("plan_sprint", plan_sprint, "Create sprint plan from goal and tasks"),
        ("spec_task", spec_task, "Specify or refine a specification"),
        ("validate_plan", validate_plan, "Validate all specifications in the plan"),
        ("close_sprint", close_sprint, "Close the current sprint"),
    ]:
        registry.register(name, fn, metadata={
            "description": desc,
            "tags": ["planning", "sprint"],
            "scope": "internal",
        })
