"""capture_task.py — Captura de tareas del agente en el historico (delta).

PROPOSITO: las tareas implementadas por el agente IA durante una sesion con
el usuario NO deben perderse. Esta task crea una especificacion formal bajo
un spring informal por fecha (session-YYYYMMDD) o en un sprint explícito
(sprint_id del workflow, SPEC-005 #4).

ADR-051 G4: la task es un adapter delgado — el flujo de negocio (spring
destino, alta de spec, captura de decisiones) vive en
SessionCaptureService (features/delta_tracking/application/services/).
Aquí queda solo: validación de input, resolución de proyecto y traducción
payload → servicio.

INVOCACION:
    walker <project>/capture_task '{"objective":"...", "domain":"...",
                                    "files":["..."], "result_state":"..."}'
"""

from __future__ import annotations
import logging
import os
from dataclasses import dataclass
from typing import Any

from returns.pipeline import is_successful
from returns.result import safe

from shared.common.shared_domain import ExecutionContext, TaskResult
from shared.common.shared_domain.validated_vo import TaskInput

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CaptureTaskInput(TaskInput):
    """Input validado de capture_task.

    objective (required): descripcion de lo implementado.
    domain: area (default '').
    type: feature|refactor|fix|... (default 'feature').
    files: lista de archivos impactados.
    result_state: estado final del resultado.
    description: descripcion detallada.
    sprint_id: spring destino explícito (p. ej. el activo del workflow).
    """

    objective: str = ""
    domain: str = ""
    type: str = "feature"
    files: list | None = None
    result_state: str = ""
    description: str = ""
    label: str = ""
    sprint_id: str = ""

    def _validate(self) -> str:
        objective = self.objective or self.label or ""
        if not objective or not str(objective).strip():
            return "Provide 'objective' describing the work done"
        if self.files is not None and not isinstance(self.files, list):
            return "files debe ser una lista"
        return ""


def capture_task(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Registra una tarea del agente en el spring informal de la fecha.

    Body:
        objective (required): descripcion de lo implementado.
        domain / type / files / result_state / description.
        sprint_id (SPEC-005 #4): spring destino explícito (p. ej. el activo del
            workflow) para que la captura sea visible en agent_context.
            Default: spring informal del día session-YYYYMMDD.
    """
    payload = data.get("_payload", data)
    input_result = CaptureTaskInput.from_dict(payload)
    if not input_result.ok:
        return TaskResult.failure(
            input_result.error or "objective requerido",
            data={
                "error": "Provide 'objective' describing the work done",
                "next_step": 'walker <project>/capture_task \'{"objective":"..."}\'',
            },
        )
    inp: CaptureTaskInput = input_result.data
    objective = inp.objective or inp.label or ""
    if not objective.strip():
        return TaskResult.failure(
            "Empty objective",
            data={
                "error": "Provide 'objective' describing the work done",
                "next_step": 'walker <project>/capture_task \'{"objective":"..."}\'',
            },
        )

    # 1. Resolver proyecto (glue del task layer — infrastructure/project_context)
    from features.main.task_execution.infrastructure.project_context import (
        resolve_project_id_from_chain,
        resolve_project_root,
    )

    project_id = data.get("_project_id", "")
    if not project_id and ctx.chain_dir:
        resolved_pid = resolve_project_id_from_chain(ctx.chain_dir)
        if is_successful(resolved_pid):
            project_id = resolved_pid.value_or("")
        else:
            logger.warning(
                "capture_task: no se pudo resolver project_id desde chain (%s)",
                resolved_pid.failure(),
            )
    if not project_id:
        return TaskResult.failure(
            "No project context",
            data={
                "error": "Cannot resolve project_id",
                "next_step": "Run from project dir or provide _project_id",
            },
        )

    # project_root puede venir directo (override para tests/uso especial)
    project_root = data.get("_project_root", "")
    if not project_root:
        resolved_root = resolve_project_root(project_id)
        project_root = resolved_root.value_or("") if is_successful(resolved_root) else ""
        if not is_successful(resolved_root):
            logger.warning(
                "capture_task: resolve_project_root fallo (%s)",
                resolved_root.failure(),
            )
    if not project_root:
        return TaskResult.failure(
            "Project not found",
            data={"error": f"Cannot resolve project dir for '{project_id}'"},
        )
    project_root = os.path.realpath(project_root)

    # 2. Decisiones a capturar (explícitas o detectadas) — la persistencia la
    #    hace SessionCaptureService. La resolución es glue del task layer
    #    (infrastructure/decision_resolution.py).
    from features.main.task_execution.infrastructure.decision_resolution import (
        resolve_decision_list,
    )

    decisions = resolve_decision_list(
        payload, inp.files or [], (inp.objective or inp.label or "").strip()
    )

    # 3. Flujo de negocio → Application Service (delta_tracking).
    from features.delta_tracking.application.services.session_capture_service import (
        SessionCaptureService,
    )

    result = SessionCaptureService.capture_session_task(
        project_root=project_root,
        project_id=project_id,
        session_id=ctx.session_id or "cli",
        sprint_id=inp.sprint_id,
        objective=objective,
        domain=inp.domain,
        spec_type=inp.type,
        result_state=inp.result_state,
        files=inp.files or [],
        description=inp.description,
        decisions=decisions,
    )

    return TaskResult.success(
        data={
            "ok": True,
            **result,
            "next_step": (
                "Consulta el historico con: walker <project>/recall_knowledge "
                '{\"_index\":\"delta\",\"text\":\"<domain>\"}'
            ),
        }
    )


def register(registry: Any) -> None:
    registry.register(
        "capture_task",
        capture_task,
        metadata={
            "description": (
                "Registra una tarea del agente en el spring informal de la fecha "
                "(session-YYYYMMDD) para que el DeltaGraph la consulte como historico. "
                "Con 'sprint_id' explícito captura en ese spring (p. ej. el activo "
                "del workflow) y queda visible para agent_context. "
                "Invocar al completar trabajo implementado."
            ),
            "usage": 'walker <project>/capture_task \'{"objective":"...","sprint_id":"<spring-activo>"}\'',
            "scope": "public",
            "tags": ["delta", "history", "capture", "session-log"],
        },
    )
