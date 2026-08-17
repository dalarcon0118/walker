"""phase_intelligence.py — Tasks públicas de consulta por fase del workflow.

Cada una delega en el orquestador TaskIntelligenceService (task_execution/application),
que cruza las 4 memorias (cpg × ckg × delta × trace) — ADR-WALKER-023: las tools
NO tocan CognitiveGraph directo. El output_schema se deriva del DTO
(contracts.to_schema(), ADR-043), nunca a mano.

Tools: task_context (IMPLEMENT), diagnose_failure (REFINE), verify_evidence
(VERIFY), sprint_evidence (SPRINT.PLANNING). Result pattern (convention-001).
"""

from __future__ import annotations
import logging
from typing import Any
from shared.common.shared_domain import ExecutionContext, TaskResult
from returns.pipeline import is_successful
from returns.result import safe

from orchestration.contracts import (
    FailureDiagnosisDTO,
    QaCoverageDTO,
    SprintEvidenceDTO,
    TaskFileIntelligenceDTO,
)

logger = logging.getLogger(__name__)


@safe
def _build_service(chain_dir: str) -> Any:
    """Core puro: construye el orquestador de joins para chain_dir (puede lanzar)."""
    from orchestration.intelligence_service import (
        build_intelligence_service,
    )

    return build_intelligence_service(chain_dir)


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
        return None, TaskResult.failure(str(error), data={"error": f"No se pudo resolver el proyecto desde chain_dir={chain_dir!r}. Crea el proyecto con 'walker <project>/prepare'."})
    return built.value_or(None), None


def task_context(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Fase IMPLEMENT — contexto completo de un archivo antes de editarlo."""
    body = data.get("_payload", data) if isinstance(data, dict) else {}
    chain_dir = ctx.chain_dir or ""

    svc, err = _get_service(chain_dir, "task_context")
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
    return _task_result(ok, payload, error, tool="task_context")


def diagnose_failure(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Fase REFINE — ¿por qué falló la tarea y qué convenciones se violaron?"""
    body = data.get("_payload", data) if isinstance(data, dict) else {}
    chain = ctx.chain_dir or ""

    svc, err = _get_service(chain, "diagnose_failure")
    if err is not None:
        return err
    result = svc.failure_diagnosis(task_id=body.get("task_id", ""), chain_dir=chain)
    ok, payload, error = _split_result(result)
    if ok and payload is not None:
        payload["phase"] = FailureDiagnosisDTO.PHASE
    return _task_result(ok, payload, error, tool="diagnose_failure")


def verify_evidence(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Fase VERIFY — ¿qué verificaciones/predicados existen para la tarea?"""
    body = data.get("_payload", data) if isinstance(data, dict) else {}
    chain = ctx.chain_dir or ""

    svc, err = _get_service(chain, "verify_evidence")
    if err is not None:
        return err
    result = svc.qa_coverage(task_id=body.get("task_id", ""), chain_dir=chain)
    ok, payload, error = _split_result(result)
    if ok and payload is not None:
        payload["phase"] = QaCoverageDTO.PHASE
    return _task_result(ok, payload, error, tool="verify_evidence")


def sprint_evidence(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Fase SPRINT.PLANNING — evidencia previa para planear el sprint."""
    body = data.get("_payload", data) if isinstance(data, dict) else {}
    chain = ctx.chain_dir or ""

    svc, err = _get_service(chain, "sprint_evidence")
    if err is not None:
        return err
    result = svc.sprint_evidence(goal=body.get("goal", ""), scope=body.get("scope", ""), chain_dir=chain)
    ok, payload, error = _split_result(result)
    if ok and payload is not None:
        payload["phase"] = SprintEvidenceDTO.PHASE
    return _task_result(ok, payload, error, tool="sprint_evidence")


def register(registry: Any) -> None:
    """Registra las 4 tools de fase con contrato completo (ADR-043)."""
    _PHASE_TOOLS = [
        (
            "task_context", task_context,
            "Fase IMPLEMENT — contexto completo de un archivo antes de editarlo (símbolos, convenciones, specs, actividad, riesgo).",
            'walker <project>/task_context \'{"file":"src/auth.py","task_id":"TASK-042"}\'',
            'walker <project>/task_context \'{"file":"src/auth.py"}\'',
            ["implement", "context", "code"],
            {"type": "object", "required": ["file"], "additionalProperties": False,
             "properties": {"file": {"type": "string", "description": "Ruta del archivo (join cpg×ckg×delta×trace por archivo)."},
                              "task_id": {"type": "string", "description": "Spec/TASK del sprint para enriquecer con la tarea."}}},
            TaskFileIntelligenceDTO,
        ),
        (
            "diagnose_failure", diagnose_failure,
            "Fase REFINE — diagnóstico de fallo de una tarea (actividad reciente, convenciones aplicables, spec).",
            'walker <project>/diagnose_failure \'{"task_id":"TASK-042"}\'',
            'walker <project>/diagnose_failure \'{"task_id":"TASK-042"}\'',
            ["refine", "diagnosis", "failure"],
            {"type": "object", "required": ["task_id"], "additionalProperties": False,
             "properties": {"task_id": {"type": "string", "description": "Spec/T del sprint a diagnosticar."}}},
            FailureDiagnosisDTO,
        ),
        (
            "verify_evidence", verify_evidence,
            "Fase VERIFY — cobertura de criterios de verificación de una tarea (specs + actividad por herramientas).",
            'walker <project>/verify_evidence \'{"task_id":"TASK-042"}\'',
            'walker <project>/verify_evidence \'{"task_id":"TASK-042"}\'',
            ["verify", "evidence", "qa"],
            {"type": "object", "required": ["task_id"], "additionalProperties": False,
             "properties": {"task_id": {"type": "string", "description": "Spec/T del sprint a auditar."}}},
            QaCoverageDTO,
        ),
        (
            "sprint_evidence", sprint_evidence,
            "Fase SPRINT.PLANNING — evidencia previa para planear el sprint (conocimiento existente + tareas relacionadas).",
            'walker <project>/sprint_evidence \'{"goal":"agregar auth"}\'',
            'walker <project>/sprint_evidence \'{"goal":"agregar auth"}\'',
            ["sprint", "planning", "evidence"],
            {"type": "object", "required": [], "additionalProperties": False,
             "properties": {"goal": {"type": "string", "description": "Meta del sprint para buscar conocimiento/precedente."},
                              "scope": {"type": "string", "description": "Ámbito (proyecto/feature) para centrar la búsqueda."}}},
            SprintEvidenceDTO,
        ),
    ]
    for name, fn, desc, usage, example, tags, input_schema, dto in _PHASE_TOOLS:
        registry.register(
            name,
            fn,
            metadata={
                "description": desc,
                "usage": usage,
                "example": example,
                "tags": tags,
                "scope": "public",
                "input_schema": input_schema,
                "output_schema": dto.to_schema(),
            },
        )
