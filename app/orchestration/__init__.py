"""app/orchestration/ — Capa de aplicación cross-feature reutilizable (ADR-051 §2.6).

Capa de aplicación del sistema: coordina lógica que cruza los bounded
contexts (features). Extraída de features/main (ADR-050 §2.1: "hoy en main,
reutilizable en el futuro" — este es el destino).

Reglas:
  - NO importa features.main (ni transporte, ni tasks, ni composition).
  - Consume SOLO Application Services de features (features.<x>.application.services).
  - La registra el composition root (app/composition/registry.py).

Contiene:
  - PrepareProjectService / CheckHealthService (orquestación de proyecto)
  - TaskIntelligenceService (joins cpg × ckg × delta × trace) + contracts DTOs
  - AdrOrchestratorService (create_adr: delta × CKG × project)
  - CodeIndexOrchestratorService (index_code: project × CPG)
  - KnowledgePersistService (documenting_knowledge: project × CKG)
  - SprintTrackingService (index_sprint_tasks: project × delta)
  - KnowledgeStatusService (update_knowledge_status: delta × CKG)
  - DecisionCaptureService (capture_decision: project × CKG)
  - DecisionDetectorService (detect_decisions: project × CKG)
  - SpecTransformService (transform_spec: delta)
"""
from __future__ import annotations

from app.orchestration.prepare_project_service import PrepareProjectService
from app.orchestration.check_health_service import CheckHealthService
from app.orchestration.intelligence_service import build_intelligence_service
from app.orchestration.adr_orchestrator_service import AdrOrchestratorService
from app.orchestration.code_index_orchestrator_service import CodeIndexOrchestratorService
from app.orchestration.knowledge_persist_service import KnowledgePersistService
from app.orchestration.sprint_tracking_service import SprintTrackingService
from app.orchestration.knowledge_status_service import KnowledgeStatusService
from app.orchestration.decision_capture_service import DecisionCaptureService
from app.orchestration.decision_detector_service import DecisionDetectorService
from app.orchestration.spec_transform_service import SpecTransformService
from app.orchestration.validate_plan_service import ValidatePlanService
from app.orchestration.sprint_status_service import SprintStatusService
from app.orchestration.advance_sprint_task_service import AdvanceSprintTaskService

__all__ = [
    "PrepareProjectService",
    "CheckHealthService",
    "build_intelligence_service",
    "AdrOrchestratorService",
    "CodeIndexOrchestratorService",
    "KnowledgePersistService",
    "SprintTrackingService",
    "KnowledgeStatusService",
    "DecisionCaptureService",
    "DecisionDetectorService",
    "SpecTransformService",
    "ValidatePlanService",
    "SprintStatusService",
    "AdvanceSprintTaskService",
]
