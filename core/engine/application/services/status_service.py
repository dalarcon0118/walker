"""application/services/status_service.py — WorkflowStatusService.

Application Service (fachada pública) del engine para consultas de estado
del workflow (ADR-WALKER-048: Application Services son la API pública del
módulo; los Use Cases son implementación interna — INV-013).

Envuelve CheckSprintStatusUseCase (`application/use_cases.py`) para que los
consumidores externos (orchestrators cross-feature de main, CLI, MCP) no
importen use cases del engine directamente.
"""
from __future__ import annotations

from typing import Any


class WorkflowStatusService:
    """Fachada pública de consultas de estado del workflow/sprint."""

    @staticmethod
    def check_sprint_status(chain_dir: str, project_id: str) -> dict:
        """Snapshot de estado del sprint (nodo, sub-estado, gates, prompt)."""
        from core.engine.application.use_cases import CheckSprintStatusUseCase

        return CheckSprintStatusUseCase.execute(chain_dir, project_id)


def workflow_status_service(chain_dir: str = "") -> WorkflowStatusService:
    """Factory del Application Service (patrón spring_service/ckg_service)."""
    return WorkflowStatusService()
