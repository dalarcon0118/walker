"""
use_cases_sprint.py — SHIM de compatibilidad (ADR-WALKER-048).

Los use cases del sprint ahora viven en `use_cases/` (un archivo por clase,
patrón canonical). Este módulo re-exporta las clases para backward
compatibility durante la migración incremental (ADR-WALKER-048 §6).

Consumidores nuevos: usen los Application Services
(`features.delta_tracking.application.services.sprint_service`), NUNCA
estos Use Cases directos (INV-013).
"""
from __future__ import annotations

from .use_cases.plan_sprint import PlanSprintUseCase
from .use_cases.specify_spec import SpecifySpecUseCase
from .use_cases.advance_spec_index import AdvanceSpecIndexUseCase
from .use_cases.update_spec_status import UpdateSpecStatusUseCase
from .use_cases.show_dashboard import ShowDashboardUseCase
from .use_cases.close_sprint import CloseSprintUseCase
from .use_cases.edit_sprint import EditSprintUseCase
from .use_cases.update_sprint_status import UpdateSprintStatusUseCase
from .use_cases.validate_plan import ValidatePlanUseCase
from .use_cases.approve_spec import ApproveSpecUseCase
from .use_cases.start_implementation import StartImplementationUseCase
from .use_cases.start_code_review import StartCodeReviewUseCase
from .use_cases.complete_spec import CompleteSpecUseCase

__all__ = [
    "PlanSprintUseCase",
    "SpecifySpecUseCase",
    "AdvanceSpecIndexUseCase",
    "UpdateSpecStatusUseCase",
    "ShowDashboardUseCase",
    "CloseSprintUseCase",
    "EditSprintUseCase",
    "UpdateSprintStatusUseCase",
    "ValidatePlanUseCase",
    "ApproveSpecUseCase",
    "StartImplementationUseCase",
    "StartCodeReviewUseCase",
    "CompleteSpecUseCase",
]
