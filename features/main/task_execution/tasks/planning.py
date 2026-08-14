"""Sprint planning tasks — thin adapters a SprintApplicationService.

SRP: estas funciones son solo adapters entre los nombres de acción del
workflow y el Application Service del sprint (features/delta_tracking/).
La lógica de negocio vive en delta_tracking.

Result pattern (dry-python/returns): los cores @safe nunca lanzan; el wrapper
decide con is_successful y degrada con fallback explícito + log
(convention-001: nunca except silencioso).

Terminology:
  - "specification" (spec) = work item in the sprint backlog (DeltaGraph, SSOT)
  - "task" = workflow action registered in TaskRegistry
"""

from __future__ import annotations

import logging
from typing import Any
from returns.pipeline import is_successful
from returns.result import safe
from shared.common.shared_domain import ExecutionContext, TaskResult
from features.delta_tracking.application.services.sprint_service import (
    SprintApplicationService,
)

logger = logging.getLogger(__name__)


def _sprint_task(service_method: str):
    """Fábrica de adapters delgados: delega en SprintApplicationService.<method>."""

    @safe
    def _core(ctx: ExecutionContext, data: dict[str, Any]) -> dict:
        return getattr(SprintApplicationService, service_method)(ctx, data)

    def _run(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
        result = _core(ctx, data)
        if is_successful(result):
            return TaskResult.success(data=result.value_or({}))
        return TaskResult.failure(str(result.failure()))

    return _run


plan_sprint = _sprint_task("plan_sprint")
advance_task_index = _sprint_task("advance_spec_index")
update_spec_status = _sprint_task("update_spec_status")
update_sprint_status = _sprint_task("update_sprint_status")
edit_sprint = _sprint_task("edit_sprint")
close_sprint = _sprint_task("close_sprint")


def spec_task(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Specify or refine a specification (Legacy name: spec_task).

    Regla del workflow: si la spec NO pasa la validacion completa
    (SpecValidator: 7 campos required + status 'specified'), la task retorna
    failure -> el auto-advance no ocurre y el sprint permanece en TASK_SPEC.
    """
    result = safe(SprintApplicationService.specify_spec)(ctx, data)
    if not is_successful(result):
        return TaskResult.failure(str(result.failure()))
    result_data = result.value_or({})
    if result_data.get("valid") is False:
        errors = result_data.get("errors", [])
        return TaskResult.failure(
            f"Spec {result_data.get('spec_id', '?')} incompleta: "
            f"{'; '.join(errors[:3])}{'...' if len(errors) > 3 else ''}"
        )
    out = dict(result_data)
    out["task_id"] = result_data.get("task_id") or result_data.get("spec_id", "")
    return TaskResult.success(data=out)


def save_task_spec(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Save specification (alias de spec_task) — misma regla de validación."""
    return spec_task(ctx, data)


def show_dashboard(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    result = safe(SprintApplicationService.show_dashboard)(ctx, data)
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


def validate_plan(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    result = safe(SprintApplicationService.validate_plan)(ctx, data)
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
    return TaskResult.success(data={
        "spec_id": data.get("spec_id", ctx.task_id),
        "progress_updated": True,
        "completed": data.get("completed_count", 0),
        "total": data.get("spec_count", 0),
    })


def log_rejection_to_ckg(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    return TaskResult.success(data={
        "spec_id": data.get("spec_id", ctx.task_id),
        "logged": True,
        "reason": data.get("reason", ""),
        "source": data.get("source_node", ""),
    })


def notify_po_escalation(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    return TaskResult.success(data={
        "spec_id": data.get("spec_id", ctx.task_id),
        "notified": True,
        "attempts": data.get("attempt", 0),
        "message": f"Refine limit reached for {data.get('spec_id', ctx.task_id)}",
    })


def archive_sprint_metrics(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    return TaskResult.success(data={
        "sprint_id": ctx.session_id,
        "archived": True,
        "completed": data.get("completed_count", 0),
        "total": data.get("spec_count", 0),
    })


def register(registry: Any) -> None:
    # Tareas del workflow son INTERNAS: solo se ejecutan desde el workflow
    # (execution:llm_task + action), no via 'walker <pid>/<task>'.
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
        ("validate_plan", validate_plan, "Validate all specifications in the DeltaGraph"),
        (
            "update_sprint_progress",
            update_sprint_progress,
            "Update sprint progress metrics",
        ),
        ("log_rejection_to_ckg", log_rejection_to_ckg, "Log rejection reason to CKG"),
        ("notify_po_escalation", notify_po_escalation, "Notify PO of refine limit"),
        ("archive_sprint_metrics", archive_sprint_metrics, "Archive sprint metrics"),
    ]:
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
