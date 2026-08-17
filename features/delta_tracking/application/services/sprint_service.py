"""application/services/sprint_service.py — SprintApplicationService.

Application Service (fachada pública) del sprint (ADR-WALKER-048).

Orquesta los Use Cases del sprint (`application/use_cases/`), que son
implementación interna del módulo (INV-013). Consumidores externos (tasks,
side-effects, CLI/MCP, engine) consumen ESTE servicio, nunca los Use Cases
directos. No contiene lógica de negocio: solo delega en los use cases.
"""
from __future__ import annotations

from typing import Any

from shared.common.shared_domain import ExecutionContext


class SprintApplicationService:
    """Fachada pública de las operaciones del sprint (delta como SSOT).

    Cada método delega en el Use Case correspondiente. Firmas idénticas a
    las que consumían las tasks (ctx + data) para migración sin fricción.
    """

    @staticmethod
    def plan_sprint(ctx: ExecutionContext, data: dict) -> dict:
        from ..use_cases.plan_sprint import PlanSprintUseCase

        return PlanSprintUseCase.execute(ctx, data)

    @staticmethod
    def specify_spec(ctx: ExecutionContext, data: dict) -> dict:
        from ..use_cases.specify_spec import SpecifySpecUseCase

        return SpecifySpecUseCase.execute(ctx, data)

    @staticmethod
    def advance_spec_index(ctx: ExecutionContext, data: dict) -> dict:
        from ..use_cases.advance_spec_index import AdvanceSpecIndexUseCase

        return AdvanceSpecIndexUseCase.execute(ctx, data)

    @staticmethod
    def update_spec_status(ctx: ExecutionContext, data: dict) -> dict:
        from ..use_cases.update_spec_status import UpdateSpecStatusUseCase

        return UpdateSpecStatusUseCase.execute(ctx, data)

    @staticmethod
    def show_dashboard(ctx: ExecutionContext, data: dict) -> dict:
        from ..use_cases.show_dashboard import ShowDashboardUseCase

        return ShowDashboardUseCase.execute(ctx, data)

    @staticmethod
    def close_sprint(ctx: ExecutionContext, data: dict) -> dict:
        from ..use_cases.close_sprint import CloseSprintUseCase

        return CloseSprintUseCase.execute(ctx, data)

    @staticmethod
    def edit_sprint(ctx: ExecutionContext, data: dict) -> dict:
        from ..use_cases.edit_sprint import EditSprintUseCase

        return EditSprintUseCase.execute(ctx, data)

    @staticmethod
    def update_sprint_status(ctx: ExecutionContext, data: dict) -> dict:
        from ..use_cases.update_sprint_status import UpdateSprintStatusUseCase

        return UpdateSprintStatusUseCase.execute(ctx, data)

    @staticmethod
    def validate_plan(ctx: ExecutionContext, data: dict) -> dict:
        from ..use_cases.validate_plan import ValidatePlanUseCase

        return ValidatePlanUseCase.execute(ctx, data)


# Alias ergonómico: el servicio se registra en el composition root como
# "sprint_application_service" (Application Service, ADR-WALKER-048).
def sprint_application_service(chain_dir: str = "") -> SprintApplicationService:
    """Factory del Application Service (patrón spring_service/ckg_service)."""
    return SprintApplicationService()
