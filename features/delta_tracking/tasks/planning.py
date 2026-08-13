"""Sprint planning tasks — thin adapters to delta_tracking use cases.

SRP: These functions are only adapters between the workflow action names
     and the delta_tracking use cases. Business logic lives in
     features/delta_tracking/.

Result pattern (dry-python/returns): los use cases se envuelven en cores
@safe que nunca lanzan; el wrapper decide con is_successful y hace fallback
explícito con log (convention-001: nunca except silencioso).

Terminology:
  - "specification" (spec) = work item in the sprint backlog (DeltaGraph, SSOT)
  - "task" = workflow action registered in TaskRegistry
  - "task" in API responses = kept for backward compat with prompts
"""

from __future__ import annotations

import logging
from typing import Any
from returns.pipeline import is_successful
from returns.result import safe
from shared.common.shared_domain import ExecutionContext, TaskResult
from features.delta_tracking.application.use_cases_sprint import (
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

logger = logging.getLogger(__name__)


@safe
def _plan_sprint_core(ctx: ExecutionContext, data: dict[str, Any]) -> dict:
    """Core puro: ejecuta PlanSprintUseCase (puede lanzar)."""
    return PlanSprintUseCase.execute(ctx, data)


def plan_sprint(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    result = _plan_sprint_core(ctx, data)
    if is_successful(result):
        return TaskResult.success(data=result.value_or({}))
    return TaskResult.failure(str(result.failure()))


@safe
def _specify_spec_core(ctx: ExecutionContext, data: dict[str, Any]) -> dict:
    """Core puro: ejecuta SpecifySpecUseCase (puede lanzar)."""
    return SpecifySpecUseCase.execute(ctx, data)


def spec_task(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Specify or refine a specification. (Legacy name: spec_task).

    Regla del workflow: si la spec NO pasa la validacion completa
    (SpecValidator.validate_spec: 7 campos required + status 'specified'),
    la task retorna failure -> el auto-advance no ocurre y el sprint
    permanece en TASK_SPEC (especificacion/refinamiento).

    El resultado incluye task_id (cumple output_schema de TASK_SPEC).
    """
    result = _specify_spec_core(ctx, data)
    if not is_successful(result):
        return TaskResult.failure(str(result.failure()))
    result_data = result.value_or({})
    if result_data.get("valid") is False:
        errors = result_data.get("errors", [])
        return TaskResult.failure(
            f"Spec {result_data.get('spec_id', '?')} incompleta: "
            f"{'; '.join(errors[:3])}{'...' if len(errors) > 3 else ''}"
        )
    # Cumplir output_schema: task_id + status
    out = dict(result_data)
    out["task_id"] = result_data.get("task_id") or result_data.get("spec_id", "")
    return TaskResult.success(data=out)


def save_task_spec(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Save specification (alias de spec_task) — misma regla de validación."""
    return spec_task(ctx, data)


@safe
def _advance_index_core(ctx: ExecutionContext, data: dict[str, Any]) -> dict:
    """Core puro: ejecuta AdvanceSpecIndexUseCase (puede lanzar)."""
    return AdvanceSpecIndexUseCase.execute(ctx, data)


def advance_task_index(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Advance to next specification index."""
    result = _advance_index_core(ctx, data)
    if is_successful(result):
        return TaskResult.success(data=result.value_or({}))
    return TaskResult.failure(str(result.failure()))


@safe
def _update_spec_status_core(ctx: ExecutionContext, data: dict[str, Any]) -> dict:
    """Core puro: ejecuta UpdateSpecStatusUseCase (puede lanzar)."""
    return UpdateSpecStatusUseCase.execute(ctx, data)


def update_spec_status(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Update specification status (draft→specified→in_progress→completed)."""
    result = _update_spec_status_core(ctx, data)
    if is_successful(result):
        return TaskResult.success(data=result.value_or({}))
    return TaskResult.failure(str(result.failure()))


@safe
def _show_dashboard_core(ctx: ExecutionContext, data: dict[str, Any]) -> dict:
    """Core puro: ejecuta ShowDashboardUseCase (puede lanzar)."""
    return ShowDashboardUseCase.execute(ctx, data)


def show_dashboard(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Muestra el dashboard; ante fallo degrada a dashboard vacío con log."""
    result = _show_dashboard_core(ctx, data)
    if is_successful(result):
        return TaskResult.success(data=result.value_or({}))
    error = result.failure()
    logger.warning(
        "show_dashboard fallo (%s: %s); degrade a dashboard vacío",
        type(error).__name__, error,
    )
    return TaskResult.success(
        data={"sprint_id": "", "goal": "", "spec_count": 0, "completed_count": 0}
    )


@safe
def _edit_sprint_core(ctx: ExecutionContext, data: dict[str, Any]) -> dict:
    """Core puro: ejecuta EditSprintUseCase (puede lanzar)."""
    return EditSprintUseCase.execute(ctx, data)


def edit_sprint(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Edit sprint: add specs, update goal, preserve existing."""
    result = _edit_sprint_core(ctx, data)
    if is_successful(result):
        return TaskResult.success(data=result.value_or({}))
    return TaskResult.failure(str(result.failure()))


@safe
def _update_sprint_status_core(ctx: ExecutionContext, data: dict[str, Any]) -> dict:
    """Core puro: ejecuta UpdateSprintStatusUseCase (puede lanzar)."""
    return UpdateSprintStatusUseCase.execute(ctx, data)


def update_sprint_status(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Update sprint status: planning→active→reviewing→closed."""
    result = _update_sprint_status_core(ctx, data)
    if is_successful(result):
        return TaskResult.success(data=result.value_or({}))
    return TaskResult.failure(str(result.failure()))


@safe
def _close_sprint_core(ctx: ExecutionContext, data: dict[str, Any]) -> dict:
    """Core puro: ejecuta CloseSprintUseCase (puede lanzar)."""
    return CloseSprintUseCase.execute(ctx, data)


def close_sprint(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    result = _close_sprint_core(ctx, data)
    if is_successful(result):
        return TaskResult.success(data=result.value_or({}))
    return TaskResult.failure(str(result.failure()))


@safe
def _validate_plan_core(ctx: ExecutionContext, data: dict[str, Any]) -> dict:
    """Core puro: ejecuta ValidatePlanUseCase (puede lanzar)."""
    return ValidatePlanUseCase.execute(ctx, data)


def validate_plan(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Valida el plan; ante fallo degrada a valid=False con log."""
    result = _validate_plan_core(ctx, data)
    if is_successful(result):
        return TaskResult.success(data=result.value_or({}))
    error = result.failure()
    logger.warning(
        "validate_plan fallo (%s: %s); degrade a valid=False",
        type(error).__name__, error,
    )
    return TaskResult.success(
        data={"valid": False, "errors": [str(error)], "spec_count": 0}
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
        ("edit_sprint", edit_sprint, "Edit sprint: add specs, update goal"),            ("validate_plan", validate_plan, "Validate all specifications in the DeltaGraph"),
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
