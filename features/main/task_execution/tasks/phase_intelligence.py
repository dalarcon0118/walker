"""phase_intelligence.py — Tasks públicas de consulta por fase del workflow.

Cada una delega en el orquestador TaskIntelligenceService (task_execution/application),
que cruza las 4 memorias (cpg × ckg × delta × trace) con joins por archivo/task_id
(ADR-WALKER-023): las tools NO tocan CognitiveGraph directo.

Contrato (ADR-WALKER-043): cada task pública declara input_schema Y output_schema
en el registro. El `output_schema` se DERIVA del DTO de contrato de la fase
(`contracts.to_schema()`), nunca se escribe a mano (SSOT del contrato).

Tools de fase:
  - kg_task_context      → Fase IMPLEMENT (TaskFileIntelligenceDTO)
  - kg_diagnose          → Fase REFINE    (FailureDiagnosisDTO)
  - kg_qa_evidence       → Fase VERIFY    (QaCoverageDTO)
  - kg_sprint_evidence   → Fase SPRINT.PLANNING (SprintEvidenceDTO)

Result pattern (dry-python/returns): el orquestador degrada cada sección con log
(convention-001); el caller solo enruta y serializa el DTO.
"""

from __future__ import annotations
import logging
from typing import Any
from shared.common.shared_domain import ExecutionContext, TaskResult
from returns.pipeline import is_successful
from returns.result import safe

from features.main.task_execution.application.contracts import (
    FailureDiagnosisDTO,
    QaCoverageDTO,
    SprintEvidenceDTO,
    TaskFileIntelligenceDTO,
)

logger = logging.getLogger(__name__)


@safe
def _build_service(chain_dir: str) -> Any:
    """Core puro: construye el orquestador de joins para chain_dir (puede lanzar)."""
    from features.main.task_execution.application.intelligence_service import (
        build_intelligence_service,
    )

    return build_intelligence_service(chain_dir)


def _resolve_chain(ctx: ExecutionContext) -> str:
    return ctx.chain_dir or ""


def _split_result(result) -> tuple[bool, dict | None, str]:
    """Desempaqueta Result returns en (ok, dict, error) sin lanzar (convention-001)."""
    if is_successful(result):
        return True, result.value_or(None).as_dict(), ""
    return False, None, str(result.failure())


def _task_result(ok: bool, data: dict | None, error: str, *, tool: str) -> TaskResult:
    if ok and data is not None:
        return TaskResult.success(data={"type": tool, **data})
    return TaskResult.failure(
        error or f"{tool}: no se pudo completar la consulta",
        data={"error": error or f"{tool}: consulta incompleta"},
    )


def _get_service(chain_dir: str, tool: str) -> tuple[Any, TaskResult | None]:
    """Resuelve el orquestador (convention-001: ante fallo devuelve TaskResult failure)."""
    built = _build_service(chain_dir)
    if not is_successful(built):
        error = built.failure()
        logger.warning("%s: build_intelligence_service fallo (%s: %s)", tool, type(error).__name__, error)
        return None, TaskResult.failure(
            str(error),
            data={"error": f"No se pudo resolver el proyecto desde chain_dir={chain_dir!r}. Crea el proyecto con 'walker <project>/prepare'."},
        )
    return built.value_or(None), None


def kg_task_context(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Fase IMPLEMENT — contexto completo de un archivo antes de editarlo."""
    body = data.get("_payload", data) if isinstance(data, dict) else {}
    chain_dir = _resolve_chain(ctx)

    svc, err = _get_service(chain_dir, "kg_task_context")
    if err is not None:
        return err
    result = svc.task_file_intelligence(
        file=body.get("file", ""),
        task_id=body.get("task_id", ""),
        chain_dir=chain_dir,
    )
    ok, payload, error = _split_result(result)
    if ok:
        payload["phase"] = TaskFileIntelligenceDTO.PHASE
    return _task_result(ok, payload, error, tool="kg_task_context")


def kg_diagnose(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Fase REFINE — ¿por qué falló la tarea y qué convenciones se violaron?"""
    body = data.get("_payload", data) if isinstance(data, dict) else {}
    chain = _resolve_chain(ctx)

    svc, err = _get_service(chain, "kg_diagnose")
    if err is not None:
        return err
    result = svc.failure_diagnosis(
        task_id=body.get("task_id", ""),
        chain_dir=chain,
    )
    ok, payload, error = _split_result(result)
    if ok and payload is not None:
        payload["phase"] = FailureDiagnosisDTO.PHASE
    return _task_result(ok, payload, error, tool="kg_diagnose")


def kg_qa_evidence(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Fase VERIFY — ¿qué verificaciones/predicados existen para la tarea?"""
    body = data.get("_payload", data) if isinstance(data, dict) else {}
    chain = _resolve_chain(ctx)

    svc, err = _get_service(chain, "kg_qa_evidence")
    if err is not None:
        return err
    result = svc.qa_coverage(
        task_id=body.get("task_id", ""),
        chain_dir=chain,
    )
    ok, payload, error = _split_result(result)
    if ok and payload is not None:
        payload["phase"] = QaCoverageDTO.PHASE
    return _task_result(ok, payload, error, tool="kg_qa_evidence")


def kg_sprint_evidence(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Fase SPRINT.PLANNING — evidencia previa para planear el sprint."""
    body = data.get("_payload", data) if isinstance(data, dict) else {}
    chain = _resolve_chain(ctx)

    svc, err = _get_service(chain, "kg_sprint_evidence")
    if err is not None:
        return err
    result = svc.sprint_evidence(
        goal=body.get("goal", ""),
        scope=body.get("scope", ""),
        chain_dir=chain,
    )
    ok, payload, error = _split_result(result)
    if ok and payload is not None:
        payload["phase"] = SprintEvidenceDTO.PHASE
    return _task_result(ok, payload, error, tool="kg_sprint_evidence")


def register(registry: Any) -> None:
    """Registra las 4 tools de fase con contrato completo (ADR-043)."""
    registry.register(
        "kg_task_context",
        kg_task_context,
        metadata={
            "description": "Fase IMPLEMENT — contexto completo de un archivo antes de editarlo (símbolos, convenciones, specs, actividad, riesgo).",
            "usage": 'walker <project>/kg_task_context \'{"file":"src/auth.py","task_id":"TASK-042"}\'',
            "example": 'walker <project>/kg_task_context \'{"file":"src/auth.py"}\'',
            "tags": ["kg", "phase", "implement", "context"],
            "scope": "public",
            "input_schema": {
                "type": "object",
                "required": ["file"],
                "additionalProperties": False,
                "properties": {
                    "file": {"type": "string", "description": "Ruta del archivo (join cpg×ckg×delta×trace por archivo)."},
                    "task_id": {"type": "string", "description": "Spec/TASK del sprint para enriquecer con la tarea."},
                },
            },
            "output_schema": TaskFileIntelligenceDTO.to_schema(),
        },
    )
    registry.register(
        "kg_diagnose",
        kg_diagnose,
        metadata={
            "description": "Fase REFINE — diagnóstico de fallo de una tarea (actividad reciente, convenciones aplicables, spec).",
            "usage": 'walker <project>/kg_diagnose \'{"task_id":"TASK-042"}\'',
            "example": 'walker <project>/kg_diagnose \'{"task_id":"TASK-042"}\'',
            "tags": ["kg", "phase", "refine"],
            "scope": "public",
            "input_schema": {
                "type": "object",
                "required": ["task_id"],
                "additionalProperties": False,
                "properties": {
                    "task_id": {"type": "string", "description": "Spec/T del sprint a diagnosticar."},
                },
            },
            "output_schema": FailureDiagnosisDTO.to_schema(),
        },
    )
    registry.register(
        "kg_qa_evidence",
        kg_qa_evidence,
        metadata={
            "description": "Fase VERIFY — cobertura de criterios de verificación de una tarea (specs + actividad por herramientas).",
            "usage": 'walker <project>/kg_qa_evidence \'{"task_id":"TASK-042"}\'',
            "example": 'walker <project>/kg_qa_evidence \'{"task_id":"TASK-042"}\'',
            "tags": ["kg", "phase", "verify"],
            "scope": "public",
            "input_schema": {
                "type": "object",
                "required": ["task_id"],
                "additionalProperties": False,
                "properties": {
                    "task_id": {"type": "string", "description": "Spec/T del sprint a auditar."},
                },
            },
            "output_schema": QaCoverageDTO.to_schema(),
        },
    )
    registry.register(
        "kg_sprint_evidence",
        kg_sprint_evidence,
        metadata={
            "description": "Fase SPRINT.PLANNING — evidencia previa para planear el sprint (conocimiento existente + tareas relacionadas).",
            "usage": 'walker <project>/kg_sprint_evidence \'{"goal":"agregar auth"}\'',
            "example": 'walker <project>/kg_sprint_evidence \'{"goal":"agregar auth"}\'',
            "tags": ["kg", "phase", "sprint"],
            "scope": "public",
            "input_schema": {
                "type": "object",
                "required": [],
                "additionalProperties": False,
                "properties": {
                    "goal": {"type": "string", "description": "Meta del sprint para buscar conocimiento/precedente."},
                    "scope": {"type": "string", "description": "Ámbito (proyecto/feature) para centrar la búsqueda."},
                },
            },
            "output_schema": SprintEvidenceDTO.to_schema(),
        },
    )