"""capture_task.py — Captura de tareas del agente en el historico (delta).

PROPOSITO: las tareas implementadas por el agente IA durante una sesion
con el usuario NO deben perderse. La manera formal es iniciar un spring
y usar el workflow. De manera informal, el agente recibe instrucciones
del usuario directamente; esta task crea una especificacion formal bajo
un spring informal por fecha:

    data/spring/session-YYYYMMDD/   (ej: session-20260731)

Todas las tareas del agente durante una determinada fecha se agrupan en
ese spring informal. El DeltaGraph (indice delta) las indexa y el agente
puede consultarlas como contexto historico con kg_search/kg_multi.

FLUJO:
  1. Resolver project_id (desde ctx o data._project_id).
  2. Resolver/crear el sprint informal del dia: data/spring/session-YYYYMMDD/.
     - Si no existe en el delta, crear el nodo spring + specs (fuente unica).
     - Si existe, reutilizar (misma fecha = mismo sprint).
  3. Agregar la tarea como spec via SpringService.add_spec (merge, no overwrite).
  4. Reindexar el sprint en el indice delta via DeltaGraphService.index_sprint.

PRINCIPIO estructural: el DeltaGraph es la fuente unica del spring.
plan.json NO se escribe (un plan es un estado del spring en el delta,
no un dato paralelo); sprint-state.json fue eliminado.

INVOCACION:
    walker <project>/capture_task '{"objective":"...", "domain":"...",
                                    "files":["..."], "result_state":"..."}'
"""

from __future__ import annotations
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from libs.shared_domain import ExecutionContext, TaskResult
from libs.shared_domain.validated_vo import TaskInput


@dataclass(frozen=True)
class CaptureTaskInput(TaskInput):
    """Input validado de capture_task.

    objective (required): descripcion de lo implementado.
    domain: area (default '').
    type: feature|refactor|fix|... (default 'feature').
    files: lista de archivos impactados.
    result_state: estado final del resultado.
    description: descripcion detallada.
    """

    objective: str = ""
    domain: str = ""
    type: str = "feature"
    files: list | None = None
    result_state: str = ""
    description: str = ""
    label: str = ""

    def _validate(self) -> str:
        objective = self.objective or self.label or ""
        if not objective or not str(objective).strip():
            return "Provide 'objective' describing the work done"
        if self.files is not None and not isinstance(self.files, list):
            return "files debe ser una lista"
        return ""


def _resolve_project_root(project_id: str) -> str:
    """Resuelve el directorio del proyecto (.cognitive/projects/<id>) desde registry."""
    from libs.path_resolver import get_project_dir

    try:
        return str(get_project_dir(project_id))
    except Exception:
        return ""


def _sprint_dir(project_root: str, date_str: str) -> str:
    """Retorna el directorio del spring informal session-{date}.

    project_root es el directorio cognitivo del proyecto
    (.cognitive/projects/<pid>); el spring vive en <project_root>/data/spring/.
    """
    spring_base = os.path.join(project_root, "data", "spring")
    os.makedirs(spring_base, exist_ok=True)
    return os.path.join(spring_base, f"session-{date_str}")


def capture_task(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Registra una tarea del agente en el spring informal de la fecha.

    Body:
        objective (required): descripcion de lo implementado.
        domain: area (default '').
        type: feature|refactor|fix|... (default 'feature').
        files: lista de archivos impactados.
        result_state: estado final del resultado.
        description: descripcion detallada.
    """
    try:
        payload = data.get("_payload", data)
        # Input validado: la lógica de negocio recibe una entidad limpia
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

        # 1. Resolver proyecto
        project_id = data.get("_project_id", "")
        if not project_id:
            from features.project import ProjectService

            try:
                if ctx.chain_dir:
                    paths = ProjectService.resolve_paths(ctx.chain_dir)
                    project_id = paths.project_id
            except Exception:
                pass
        if not project_id:
            return TaskResult.failure(
                "No project context",
                data={
                    "error": "Cannot resolve project_id",
                    "next_step": "Run from project dir or provide _project_id",
                },
            )

        # project_root puede venir directo (override para tests/uso especial)
        project_root = data.get("_project_root", "") or _resolve_project_root(
            project_id
        )
        if not project_root or not os.path.isdir(project_root):
            return TaskResult.failure(
                "Project not found",
                data={
                    "error": f"Cannot resolve project dir for '{project_id}'",
                },
            )
        project_root = os.path.realpath(project_root)

        # 2. Spring informal del dia (modelo unificado: delta = fuente unica)
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        sprint_id = f"session-{date_str}"
        sprint_dir = _sprint_dir(project_root, date_str)

        from features.delta_tracking.application.services.spring_service import (
            SpringService,
        )

        spring_svc = SpringService(project_root)

        # Crear/actualizar el nodo spring en el delta
        existing = spring_svc.get_spring(sprint_id)
        created = existing is None
        if created:
            spring_svc.create_spring(
                sprint_id,
                goal=f"Session log {sprint_id} — trabajo del agente",
                status="planning",
                project_id=project_id,
                session_id=ctx.session_id or "cli",
                informal=True,
            )
        else:
            # Los session logs NUNCA compiten como sprint activo del workflow:
            # se mantienen en planning (no activos) y siempre informales.
            existing_data = dict(existing)
            if existing_data.get("status") == "active":
                existing_data["status"] = "planning"
                existing_data["active"] = False
            existing_data.setdefault("informal", True)
            existing_data.setdefault("session_id", ctx.session_id or "cli")
            spring_svc.update_spring(sprint_id, existing_data)

        # 3. Agregar la tarea como spec en el delta (fuente unica)
        result = spring_svc.add_spec(
            sprint_id,
            objective=str(objective).strip(),
            domain=inp.domain,
            spec_type=inp.type,
            result_state=inp.result_state,
            files=inp.files or [],
            description=inp.description,
        )

        # 5. Captura de DECISIONES en el CKG (estructural, no manual):
        #    - Si el agente reporta decisions[] → se registran con contexto.
        #    - Si NO reporta pero los files tocan arquitectura → el detector
        #      genera la decision automaticamente (objective como base).
        decisions = _resolve_decision_list(payload, inp)
        captured_decisions = _capture_decisions(
            decisions,
            project_root,
            result.get("spec_id", ""),
            ctx.session_id or "",
            sprint_id,
            objective,
            inp.files or [],
        )

        return TaskResult.success(
            data={
                "ok": True,
                "sprint_id": sprint_id,
                "sprint_dir": sprint_dir,
                "created_sprint": created,
                "spec_count": len(spring_svc.get_specs(sprint_id)),
                "indexed_tasks": 1,
                "decisions_captured": len(captured_decisions),
                "decisions": captured_decisions,
                "next_step": (
                    "Consulta el historico con: walker <project>/kg_search "
                    '\'{"_index":"delta","text":"<domain>"}\''
                ),
            }
        )
    except Exception as e:
        return TaskResult.failure(
            str(e),
            data={
                "error": f"capture_task failed: {e}",
            },
        )


def _resolve_decision_list(payload: dict, inp: "CaptureTaskInput") -> list:
    """Resuelve la lista de decisiones: explícitas, de campo decision, o
    detectadas automaticamente desde archivos de arquitectura."""
    decisions = payload.get("decisions", []) or []
    if not decisions and payload.get("decision"):
        decisions = [
            payload.get("decision")
            if isinstance(payload["decision"], dict)
            else {"statement": payload["decision"]}
        ]

    # Deteccion automatica: archivos de arquitectura → decision implicita
    if not decisions:
        try:
            from features.task_execution.side_effects.decision_detector import (
                detect_decision_from_changes,
            )

            detected = detect_decision_from_changes(inp.files or [])
            if detected:
                objective = inp.objective or inp.label or ""
                decisions = [
                    {
                        "statement": (
                            f"ADR: {objective} — afecta {det['area']} "
                            f"({len(det['files'])} archivos de arquitectura)"
                        ),
                        "rationale": (
                            f"Detectado automaticamente desde files de {det['area']}. "
                            f"Archivos: {', '.join(det['files'][:5])}"
                        ),
                    }
                    for det in detected
                ]
        except Exception:
            pass
    return decisions


def _capture_decisions(
    decisions: list,
    project_root: str,
    spec_id: str,
    session_id: str,
    sprint_id: str,
    objective: str,
    files: list,
) -> list[dict]:
    """Registra decisiones en el CKG (best-effort)."""
    captured_decisions = []
    for dec in decisions:
        if isinstance(dec, str):
            dec = {"statement": dec}
        statement = dec.get("statement", "") or dec.get("title", "")
        if not statement:
            continue
        try:
            from features.cognitive_knowledge.application.ckg import (
                CodeKnowledgeGraph,
            )

            ckg = CodeKnowledgeGraph(project_root)
            node = {
                "type": "decision",
                "label": f"decision:{statement[:40]}",
                "description": dec.get("rationale", "")
                or dec.get("description", ""),
                "metadata": {
                    "task_id": spec_id,
                    "source": f"session:{session_id}",
                    "session_sprint": sprint_id,
                },
            }
            if dec.get("supersedes"):
                node["supersedes"] = dec["supersedes"]
            save_r = ckg.save_nodes([node])
            captured_decisions.append(
                {
                    "node_id": save_r.get("node_ids", [None])[0],
                    "statement": statement[:60],
                    "degraded": save_r.get("degraded", []),
                }
            )
        except Exception:
            pass  # captura de decision best-effort; el trabajo ya quedo en delta
    return captured_decisions


def register(registry: Any) -> None:
    registry.register(
        "capture_task",
        capture_task,
        metadata={
            "description": (
                "Registra una tarea del agente en el spring informal de la fecha "
                "(session-YYYYMMDD) para que el DeltaGraph la consulte como historico. "
                "Invocar al completar trabajo implementado."
            ),
            "usage": 'walker <project>/capture_task \'{"objective":"...","domain":"...","files":["..."]}\'',
            "scope": "public",
            "tags": ["delta", "history", "capture", "session-log"],
        },
    )
