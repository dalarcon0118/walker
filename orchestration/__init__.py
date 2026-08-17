"""orchestration/ — Capa de aplicación cross-feature reutilizable (ADR-051 §2.6).

Capa de aplicación del sistema: coordina lógica que cruza los bounded
contexts (features). Extraída de features/main (ADR-050 §2.1: "hoy en main,
reutilizable en el futuro" — este es el destino).

Reglas:
  - NO importa features.main (ni transporte, ni tasks, ni composition).
  - Consume SOLO Application Services de features (features.<x>.application.services).
  - La registra el composition root (features/main/composition/registry.py).

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

from orchestration.prepare_project_service import PrepareProjectService
from orchestration.check_health_service import CheckHealthService
from orchestration.intelligence_service import build_intelligence_service
from orchestration.adr_orchestrator_service import AdrOrchestratorService
from orchestration.code_index_orchestrator_service import CodeIndexOrchestratorService
from orchestration.knowledge_persist_service import KnowledgePersistService
from orchestration.sprint_tracking_service import SprintTrackingService
from orchestration.knowledge_status_service import KnowledgeStatusService
from orchestration.decision_capture_service import DecisionCaptureService
from orchestration.decision_detector_service import DecisionDetectorService
from orchestration.spec_transform_service import SpecTransformService
from orchestration.validate_plan_service import ValidatePlanService
from orchestration.sprint_status_service import SprintStatusService
from orchestration.advance_sprint_task_service import AdvanceSprintTaskService

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
