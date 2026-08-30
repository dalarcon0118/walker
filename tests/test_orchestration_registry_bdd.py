"""tests/test_orchestration_registry_bdd.py — Completitud de orchestrators por id+contrato (ADR-068 M-06).

Valida que los 14 orchestrators cross-feature (G10 ADR-051) estén registrados
como services DI por id+contrato (ADR-062) en el composition root.
"""
from __future__ import annotations

import pytest

EXPECTED_ORCHESTRATOR_IDS = {
    "prepare_project_service",
    "check_health_service",
    "intelligence_service",
    "adr_orchestrator_service",
    "advance_sprint_task_service",
    "code_index_orchestrator_service",
    "decision_capture_service",
    "decision_detector_service",
    "knowledge_persist_service",
    "knowledge_status_service",
    "spec_transform_service",
    "sprint_status_service",
    "sprint_tracking_service",
    "validate_plan_service",
}


def _resolves(registry, svc_id: str) -> bool:
    try:
        registry.get_service(svc_id)
        return True
    except Exception:
        return False


class TestOrchestrationRegistry:
    """Completitud del registro de orchestrators por id+contrato (M-06)."""

    def test_los_14_orchestrators_estan_registrados(self):
        from app.container import AppContainer

        app = AppContainer.build()
        registry = app.resolve("module_registry")
        missing = [sid for sid in EXPECTED_ORCHESTRATOR_IDS if not _resolves(registry, sid)]
        assert not missing, f"faltan orchestrators por id+contrato: {missing}"

    def test_cada_orchestrator_resuelve_por_id(self):
        from app.container import AppContainer

        app = AppContainer.build()
        registry = app.resolve("module_registry")
        for svc_id in sorted(EXPECTED_ORCHESTRATOR_IDS):
            svc = registry.get_service(svc_id)
            assert callable(svc), f"{svc_id} no resuelve por id+contrato"