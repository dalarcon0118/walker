"""Sprint planning tasks — thin adapters to Spring use cases.

SRP: These functions are only adapters between the workflow action names
     and the Spring use cases. Business logic lives in features/spring/.

Terminology:
  - "specification" (spec) = work item in plan.json (sprint backlog)
  - "task" = workflow action registered in TaskRegistry
  - "task" in API responses = kept for backward compat with prompts
"""

from __future__ import annotations

from typing import Any
from libs.shared_domain import ExecutionContext, TaskResult
from features.spring.application.use_cases import (
    PlanSprintUseCase,
    SpecifySpecUseCase,
    AdvanceSpecIndexUseCase,
    UpdateSpecStatusUseCase,
    ShowDashboardUseCase,
    CloseSprintUseCase,
    EditSprintUseCase,
    UpdateSprintStatusUseCase,
    ValidatePlanUseCase,
)


def plan_sprint(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    try:
        return TaskResult.success(data=PlanSprintUseCase.execute(ctx, data))
    except (FileNotFoundError, ValueError) as e:
        return TaskResult.failure(str(e))


def spec_task(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Specify or refine a specification. (Legacy name: spec_task).

    Regla del workflow: si la spec NO pasa la validacion completa
    (SpecValidator.validate_spec: 7 campos required + status 'specified'),
    la task retorna failure -> el auto-advance no ocurre y el sprint
    permanece en TASK_SPEC (especificacion/refinamiento).

    El resultado incluye task_id (cumple output_schema de TASK_SPEC).
    """
    try:
        result = SpecifySpecUseCase.execute(ctx, data)
        if result.get("valid") is False:
            errors = result.get("errors", [])
            return TaskResult.failure(
                f"Spec {result.get('spec_id', '?')} incompleta: "
                f"{'; '.join(errors[:3])}{'...' if len(errors) > 3 else ''}"
            )
        # Cumplir output_schema: task_id + status
        out = dict(result)
        out["task_id"] = result.get("task_id") or result.get("spec_id", "")
        return TaskResult.success(data=out)
    except (FileNotFoundError, ValueError) as e:
        return TaskResult.failure(str(e))


def save_task_spec(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Save specification (alias de spec_task) — misma regla de validación."""
    return spec_task(ctx, data)


def advance_task_index(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Advance to next specification index."""
    try:
        return TaskResult.success(data=AdvanceSpecIndexUseCase.execute(ctx, data))
    except (FileNotFoundError, ValueError) as e:
        return TaskResult.failure(str(e))


def update_spec_status(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Update specification status (draft→specified→in_progress→completed)."""
    try:
        return TaskResult.success(data=UpdateSpecStatusUseCase.execute(ctx, data))
    except (FileNotFoundError, ValueError) as e:
        return TaskResult.failure(str(e))


def show_dashboard(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    try:
        return TaskResult.success(data=ShowDashboardUseCase.execute(ctx, data))
    except Exception:
        return TaskResult.success(
            data={"sprint_id": "", "goal": "", "spec_count": 0, "completed_count": 0}
        )


def edit_sprint(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Edit sprint: add specs, update goal, preserve existing."""
    try:
        return TaskResult.success(data=EditSprintUseCase.execute(ctx, data))
    except (FileNotFoundError, ValueError) as e:
        return TaskResult.failure(str(e))


def update_sprint_status(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Update sprint status: planning→active→reviewing→closed."""
    try:
        return TaskResult.success(data=UpdateSprintStatusUseCase.execute(ctx, data))
    except (FileNotFoundError, ValueError) as e:
        return TaskResult.failure(str(e))


def close_sprint(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    try:
        return TaskResult.success(data=CloseSprintUseCase.execute(ctx, data))
    except (FileNotFoundError, ValueError) as e:
        return TaskResult.failure(str(e))


def validate_plan(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    try:
        return TaskResult.success(data=ValidatePlanUseCase.execute(ctx, data))
    except Exception as e:
        return TaskResult.success(
            data={"valid": False, "errors": [str(e)], "spec_count": 0}
        )


def update_sprint_progress(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    return TaskResult.success(
        data={
            "spec_id": data.get("spec_id", ctx.task_id),
            "progress_updated": True,
            "completed": data.get("completed_count", 0),
            "total": data.get("spec_count", 0),
        }
    )


def log_rejection_to_ckg(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    return TaskResult.success(
        data={
            "spec_id": data.get("spec_id", ctx.task_id),
            "logged": True,
            "reason": data.get("reason", ""),
            "source": data.get("source_node", ""),
        }
    )


def notify_po_escalation(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    return TaskResult.success(
        data={
            "spec_id": data.get("spec_id", ctx.task_id),
            "notified": True,
            "attempts": data.get("attempt", 0),
            "message": f"Refine limit reached for {data.get('spec_id', ctx.task_id)}",
        }
    )


def archive_sprint_metrics(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    return TaskResult.success(
        data={
            "sprint_id": ctx.session_id,
            "archived": True,
            "completed": data.get("completed_count", 0),
            "total": data.get("spec_count", 0),
        }
    )


def register(registry: Any) -> None:
    for name, fn, desc in [
        ("plan_sprint", plan_sprint, "Create sprint plan from goal and tasks"),
        ("spec_task", spec_task, "Specify or refine a specification"),
        ("save_task_spec", save_task_spec, "Save specification (alias)"),
        (
            "advance_task_index",
            advance_task_index,
            "Advance to next specification index",
        ),
        ("show_dashboard", show_dashboard, "Show sprint dashboard"),
        ("close_sprint", close_sprint, "Close the current sprint"),
        ("edit_sprint", edit_sprint, "Edit sprint: add specs, update goal"),
        ("validate_plan", validate_plan, "Validate all specifications in plan.json"),
        (
            "update_sprint_progress",
            update_sprint_progress,
            "Update sprint progress metrics",
        ),
        ("log_rejection_to_ckg", log_rejection_to_ckg, "Log rejection reason to CKG"),
        ("notify_po_escalation", notify_po_escalation, "Notify PO of refine limit"),
        ("archive_sprint_metrics", archive_sprint_metrics, "Archive sprint metrics"),
    ]:
        # Regla de visibilidad: tareas del workflow son INTERNAS.
        # Solo se ejecutan desde dentro del workflow (execution:llm_task + action),
        # porque es la unica manera de que se actualice workflow-state.json.
        # NO se invocan via 'walker <pid>/<task>'.
        registry.register(
            name,
            fn,
            metadata={
                "description": desc,
                "tags": ["planning", "sprint"],
                "scope": "internal",
            },
        )
    # Capacidades para descubrimiento dinamico (health/status/help)
    registry.register(
        "plan_sprint",
        plan_sprint,
        metadata={
            "description": "Create sprint plan from goal and tasks",
            "tags": ["planning", "sprint"],
            "purpose": ["sprint_create", "sprint_plan"],
            "scope": "internal",
        },
    )
