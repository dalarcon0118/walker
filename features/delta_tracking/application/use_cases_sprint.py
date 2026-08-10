"""
application/use_cases.py — Spring use cases for the workflow.

Each use case:
  - Is a thin orchestrator: calls domain services + infrastructure repos
  - Contains NO business logic (that's in domain/services.py)
  - Contains NO IO directly (that's in infrastructure/repositories.py)

The workflow calls these via registered tasks (features/task_execution/tasks/).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from libs.shared_domain import ExecutionContext
from ..domain.services.spec_service import (
    SpecValidator,
    SpecBuilder,
)


def _spring_svc(sprint_dir: str):
    """Resuelve SpringService (delta) o levanta error claro.

    PRINCIPIO: el DELTA (SpringService) es la unica fuente del spring.
    No existe plan.json como fuente paralela (ADR-WALKER-013).
    """
    from features.delta_tracking import spring_service

    spring_svc = spring_service(chain_dir=sprint_dir)
    if spring_svc is None:
        raise ValueError(
            f"Spring no indexado en el delta para '{sprint_dir}'. "
            "Fuente unica: DeltaGraph (SpringService)."
        )
    return spring_svc


class PlanSprintUseCase:
    """Creates a new sprint: spring + specs en el delta (fuente unica).

    PRINCIPIO estructural: el DeltaGraph (SpringService) es la unica
    fuente de verdad del spring. Crea el nodo spring + nodos spec en el
    indice delta. plan.json NO existe como fuente paralela
    (ADR-WALKER-013). El estado del motor (.workflow-state.json) lo crea
    el engine.
    """

    @staticmethod
    def _from_payload(data: dict, key: str, default=None):
        """Lee un campo desde _payload (REST body) o desde data directa."""
        payload = data.get("_payload")
        if isinstance(payload, dict) and key in payload:
            return payload.get(key, default)
        return data.get(key, default)

    @staticmethod
    def execute(ctx: ExecutionContext, data: dict) -> dict:
        goal = PlanSprintUseCase._from_payload(data, "goal", "")
        specs_raw = PlanSprintUseCase._from_payload(
            data, "tasks", PlanSprintUseCase._from_payload(data, "specifications", [])
        )

        sprint_dir = _resolve_chain_dir(
            ctx, project_id=data.get("_project_id", ""), goal=goal
        )

        sprint_id = os.path.basename(sprint_dir)
        record = _get_active_record(ctx)
        project_id = record.project_id if record else ""

        # ── Fuente unica: crear spring + specs en el delta ────────────
        from features.delta_tracking import SpringService

        project_root = _resolve_project_root(project_id) or os.path.dirname(
            os.path.dirname(sprint_dir)
        )
        spring_svc = SpringService(project_root)
        spring_svc.create_spring(
            sprint_id,
            goal=goal,
            status="planning",
            project_id=project_id,
            session_id=ctx.session_id or "",
        )
        plan_specs = []
        # Batch upsert via SpringService (1 serializacion, no N)
        spring_svc.upsert_specs_batch(
            sprint_id,
            [
                {
                    "objective": item.get("objective", ""),
                    "domain": item.get("domain", ""),
                    "type": item.get("type", "feature"),
                    "result_state": item.get("result_state", ""),
                    "files": (
                        item.get("impacted_artifacts", {}).get("files", [])
                        if isinstance(item.get("impacted_artifacts"), dict)
                        else []
                    ),
                    "status": item.get("status", "draft"),
                    "description": item.get("task_description", ""),
                }
                for item in specs_raw
            ],
        )
        plan_specs = list(specs_raw)

        # Sincronizar .workflow-state.json (canonico del engine) con el
        # estado inicial del sprint (PLANNING). El motor no lee
        # sprint-state.json: el estado de negocio vive en el delta.
        try:
            from features.engine.infrastructure.repositories import (
                WorkflowStateRepository,
            )

            from features.engine.domain.entities import WorkflowState

            wf = WorkflowState(current_node="SPRINT", current_sub_state="PLANNING")
            wf.context["chain_dir"] = sprint_dir
            wf.context["project_id"] = project_id
            wf.context["session_id"] = ctx.session_id or ""
            WorkflowStateRepository().save(sprint_dir, wf)
        except Exception:
            pass

        if record:
            record.chain_dir = sprint_dir
            from features.engine.domain.session import SessionStore

            SessionStore.save(record)
            SessionStore.set_active(record.session_id)

        # Keep 'tasks' in response for backward compat with workflow prompts
        return {
            "sprint_id": sprint_id,
            "goal": goal,
            "spec_count": len(plan_specs),
            "task_count": len(plan_specs),
            "chain_dir": sprint_dir,
            "status": "planned",
        }


class SpecifySpecUseCase:
    """Creates or refines a specification in the delta (fuente unica).

    Called by spec_task workflow action.
    """

    @staticmethod
    def execute(ctx: ExecutionContext, data: dict) -> dict:
        sprint_dir = ctx.chain_dir or ""
        if not sprint_dir:
            raise ValueError("chain_dir not available")

        # Fuente unica: delta (SpringService). No hay plan.json paralelo.
        spring_svc = _spring_svc(sprint_dir)
        spring_id = spring_svc.get_spring_id(sprint_dir)
        spring_svc.ensure_spring_imported(sprint_dir, spring_id)
        specs = spring_svc.get_specs_legacy(spring_id)

        # Phase 1: Create initial spec if plan is empty
        is_initial = data.get("spec_id", data.get("task_id")) and not data.get(
            "result_state"
        )
        if not specs and is_initial:
            new_spec = SpecBuilder.create_default(0, data)
            spring_svc.add_spec(
                spring_id,
                objective=new_spec.get("objective", ""),
                domain=new_spec.get("domain", ""),
                spec_type=new_spec.get("type", "feature"),
                spec_index=0,
                status="draft",
            )
            # SSOT: el contexto de negocio vive en el delta (nodo spring)
            spring_svc.set_spring_context(
                spring_id, context={"current_spec_index": 0, "current_task_index": 0}
            )
            return {
                "spec_id": new_spec["spec_id"],
                "status": "draft",
                "message": f"Spec {new_spec['spec_id']} created.",
            }

        # Phase 2: Get current index
        current_idx = _get_current_spec_index(sprint_dir)
        if not specs:
            raise ValueError("No specifications in spring; use plan_sprint first.")
        current_idx = min(current_idx, len(specs) - 1)
        current_spec = specs[current_idx]

        # Phase 3: No new data → return current state
        has_data = any(
            k in data
            for k in (
                "result_state",
                "task_description",
                "predicates",
                "files",
                "preconditions",
                "acceptance_criteria",
                "quality_gates",
            )
        )
        if not has_data:
            return {
                "spec_id": current_spec.get("spec_id", current_spec.get("task_id", "")),
                "status": current_spec.get("status", "draft"),
                "current_index": current_idx,
                "total_specs": len(specs),
            }

        # Phase 4: Validate + refine
        errors = SpecValidator.validate_fields(data)
        if errors:
            raise ValueError(f"Required fields missing: {'; '.join(errors)}")

        refined = SpecBuilder.refine(current_spec, data)
        specs[current_idx] = refined
        spec_id = refined.get(
            "spec_id", refined.get("task_id", f"SPEC-{current_idx + 1:03d}")
        )
        spring_svc.update_spec(spring_id, spec_id, refined)

        # ── Phase 5: Validar la spec COMPLETA antes de permitir avanzar ──
        # Regla del workflow: el sprint solo avanza de TASK_SPEC si la spec
        # actual pasa la validacion completa (7 campos required + status
        # 'specified'). Si no, sigue en especificacion/refinamiento.
        spec_errors = SpecValidator.validate_spec(refined)
        status = refined.get("status", "draft")
        if spec_errors:
            return {
                "spec_id": refined["spec_id"],
                "status": status,
                "valid": False,
                "errors": spec_errors,
                "current_index": current_idx,
                "total_specs": len(specs),
                "message": f"Spec {refined['spec_id']} incomplete: "
                f"{len(spec_errors)} error(es). Sigue en TASK_SPEC.",
            }

        return {
            "spec_id": refined["spec_id"],
            "status": status,
            "valid": True,
            "current_index": current_idx,
            "total_specs": len(specs),
        }


class AdvanceSpecIndexUseCase:
    """Moves to the next specification index."""

    @staticmethod
    def execute(ctx: ExecutionContext, data: dict) -> dict:
        sprint_dir = ctx.chain_dir or ""
        if not sprint_dir:
            raise ValueError("chain_dir not available")
        current_idx = _get_current_spec_index(sprint_dir)
        new_idx = current_idx + 1
        # SSOT: el contexto de negocio vive en el delta (nodo spring)
        from features.delta_tracking import spring_service

        spring_svc = spring_service(chain_dir=sprint_dir)
        if spring_svc:
            spring_id = spring_svc.get_spring_id(sprint_dir)
            spring_svc.ensure_spring_imported(sprint_dir, spring_id)
            spring_svc.set_spring_context(
                spring_id, current_spec_index=new_idx, current_task_index=new_idx
            )
        return {"previous_index": current_idx, "current_index": new_idx}


class UpdateSpecStatusUseCase:
    """Updates the status of a specific specification.

    Status flow: draft → specified → in_progress → completed.
    """

    @staticmethod
    def execute(ctx: ExecutionContext, data: dict) -> dict:
        sprint_dir = ctx.chain_dir or ""
        spec_id = data.get("spec_id", data.get("task_id", ""))
        new_status = data.get("status", "")
        if not spec_id or not new_status:
            raise ValueError("Requires spec_id and status")
        spring_svc = _spring_svc(sprint_dir)
        spring_id = spring_svc.get_spring_id(sprint_dir)
        spring_svc.ensure_spring_imported(sprint_dir, spring_id)
        specs = spring_svc.get_specs_legacy(spring_id)
        for spec in specs:
            if spec.get("spec_id", spec.get("task_id")) == spec_id:
                spring_svc.update_spec_status(spring_id, spec_id, new_status)
                return {"spec_id": spec_id, "status": new_status}
        raise ValueError(f"Spec {spec_id} not found in delta")


class ShowDashboardUseCase:
    """Returns the current sprint dashboard state.

    PRINCIPIO: el estado del spring (goal, specs, estados) vive en el
    DELTA (SpringService), fuente unica. No lee sprint-state.json.
    """

    @staticmethod
    def execute(ctx: ExecutionContext, data: dict) -> dict:
        sprint_dir = ctx.chain_dir or ""
        if not sprint_dir:
            return {
                "sprint_id": "",
                "goal": "",
                "specs": [],
                "spec_count": 0,
                "completed_count": 0,
            }
        try:
            from features.delta_tracking import spring_service

            spring_svc = spring_service(chain_dir=sprint_dir)
            if not spring_svc:
                return {
                    "sprint_id": "",
                    "goal": "",
                    "specs": [],
                    "spec_count": 0,
                    "completed_count": 0,
                }
            spring_id = spring_svc.get_spring_id(sprint_dir)
            spring = spring_svc.ensure_spring_imported(sprint_dir, spring_id)
            spring_data = spring or {}
            specs = spring_svc.get_specs_legacy(spring_id)
        except Exception:
            return {
                "sprint_id": "",
                "goal": "",
                "specs": [],
                "spec_count": 0,
                "completed_count": 0,
            }
        goal = spring_data.get("goal", "")
        total = len(specs)
        completed = sum(
            1 for s in specs if isinstance(s, dict) and s.get("status") == "completed"
        )
        return {
            "sprint_id": spring_data.get("_id", "") or spring_id,
            "goal": goal,
            "specs": specs,
            "spec_count": total,
            "task_count": total,
            "completed_count": completed,
        }


class CloseSprintUseCase:
    """Closes the current sprint.

    PRINCIPIO: el estado del spring vive en el delta. Cerrar = mutar el
    nodo spring (status -> closed). No escribe sprint-state.json.
    """

    @staticmethod
    def execute(ctx: ExecutionContext, data: dict) -> dict:
        sprint_dir = ctx.chain_dir or ""
        if not sprint_dir:
            raise ValueError("chain_dir not available")
        from features.delta_tracking import spring_service

        spring_svc = spring_service(chain_dir=sprint_dir)
        if not spring_svc:
            raise ValueError("cannot resolve SpringService")
        spring_id = spring_svc.get_spring_id(sprint_dir)
        spring = spring_svc.ensure_spring_imported(sprint_dir, spring_id)
        if not spring:
            raise ValueError(f"Spring '{spring_id}' not found in delta")
        spring_svc.set_spring_status(spring_id, "closed")
        return {"status": "closed", "sprint_id": spring_id}


class EditSprintUseCase:
    """Edits an existing sprint: adds/updates specs without overwriting.

    Unlike PlanSprintUseCase (which creates from scratch), this merges
    changes: adds new specs, updates the goal, preserves existing specs.
    """

    @staticmethod
    def execute(ctx: ExecutionContext, data: dict) -> dict:
        sprint_dir = ctx.chain_dir or ""
        spring_svc = _spring_svc(sprint_dir)
        spring_id = spring_svc.get_spring_id(sprint_dir)
        spring_svc.ensure_spring_imported(sprint_dir, spring_id)
        spring_svc.get_spring(spring_id) or {}
        specs = spring_svc.get_specs_legacy(spring_id)

        # Update goal if provided
        if data.get("goal"):
            spring_svc.update_spring(spring_id, {"goal": data["goal"]})

        # Add new specs from planning input (upsert al delta)
        specs_raw = data.get("tasks", data.get("specifications", []))
        if specs_raw:
            items = [
                {
                    "objective": item.get("objective", ""),
                    "domain": item.get("domain", ""),
                    "type": item.get("type", "feature"),
                    "result_state": item.get("result_state", ""),
                    "files": (
                        item.get("impacted_artifacts", {}).get("files", [])
                        if isinstance(item.get("impacted_artifacts"), dict)
                        else []
                    ),
                    "status": item.get("status", "draft"),
                    "description": item.get("task_description", ""),
                }
                for item in specs_raw
            ]
            spring_svc.upsert_specs_batch(spring_id, items)

        # Update existing spec if spec_id matches
        spec_updates = data.get("spec_updates", [])
        for update in spec_updates:
            update_id = update.get("spec_id", update.get("task_id", ""))
            for s in specs:
                if s.get("spec_id", s.get("task_id", "")) == update_id:
                    spring_svc.update_spec(spring_id, update_id, update)
                    break

        updated_specs = spring_svc.get_specs_legacy(spring_id)
        goal = spring_svc.get_spring(spring_id) or {}
        return {
            "modified": True,
            "spec_count": len(updated_specs),
            "goal": goal.get("goal", ""),
        }


class UpdateSprintStatusUseCase:
    """Updates sprint status: planning → active → reviewing → closed.

    PRINCIPIO: el estado del spring vive en el delta. Mutar el status del
    nodo spring via SpringService (fuente unica). No escribe
    sprint-state.json.
    """

    @staticmethod
    def execute(ctx: ExecutionContext, data: dict) -> dict:
        sprint_dir = ctx.chain_dir or ""
        new_status = data.get("status", "")
        if not new_status:
            raise ValueError("Requires --status")
        from features.delta_tracking import spring_service

        spring_svc = spring_service(chain_dir=sprint_dir)
        if not spring_svc:
            raise ValueError("cannot resolve SpringService")
        spring_id = spring_svc.get_spring_id(sprint_dir)
        spring = spring_svc.ensure_spring_imported(sprint_dir, spring_id)
        if not spring:
            raise ValueError(f"Spring '{spring_id}' not found in delta")
        current = spring.get("status", "planning")
        from ..domain.services import SprintStatusUpdater

        allowed, reason = SprintStatusUpdater.can_transition(current, new_status)
        if not allowed:
            raise ValueError(reason)
        spring_svc.set_spring_status(spring_id, new_status)
        return {"previous_status": current, "status": new_status}


class ValidatePlanUseCase:
    """Validates all specifications from the delta (fuente unica)."""

    @staticmethod
    def execute(ctx: ExecutionContext, data: dict) -> dict:
        sprint_dir = ctx.chain_dir or ""
        try:
            spring_svc = _spring_svc(sprint_dir)
            spring_id = spring_svc.get_spring_id(sprint_dir)
            spring_svc.ensure_spring_imported(sprint_dir, spring_id)
            specs = spring_svc.get_specs_legacy(spring_id)
        except ValueError:
            return {
                "valid": False,
                "errors": ["spring not found in delta"],
                "spec_count": 0,
            }
        if not specs:
            return {
                "valid": False,
                "errors": ["No specifications in spring"],
                "spec_count": 0,
            }
        all_errors: list[str] = []
        for spec in specs:
            all_errors.extend(SpecValidator.validate_spec(spec))
        return {
            "valid": len(all_errors) == 0,
            "errors": all_errors,
            "spec_count": len(specs),
            "specified_count": sum(1 for s in specs if s.get("status") == "specified"),
        }


# ── Helpers ────────────────────────────────────────────────────────────────


def _resolve_chain_dir(
    ctx: ExecutionContext, project_id: str = "", goal: str = ""
) -> str:
    """Resolve or create sprint chain directory.

    SSOT: Delegates to ProjectService for path resolution.
    Never uses dirname(dirname()) or hardcoded paths.
    Usa project_id de data cuando no hay sesion activa (CLI directo).

    PRINCIPIO: un chain_dir válido se detecta por .workflow-state.json
    (estado del motor). plan.json no existe como fuente paralela
    (ADR-WALKER-013): el estado de negocio vive en el delta.

    NAMING: los springs formales se nombran con un slug descriptivo del goal
    (sprint-{slug}, ej: sprint-workflow-contract-redesign). El nombre del
    directorio es el spring_id (get_spring_id deriva el id del basename).
    """
    cd = ctx.chain_dir or ""
    if (
        cd
        and os.path.isdir(cd)
        and os.path.exists(os.path.join(cd, ".workflow-state.json"))
    ):
        return cd

    from features.project import ProjectService

    record = _get_active_record(ctx)
    pid = project_id or (getattr(record, "project_id", "") if record else "")

    if pid:
        active = ProjectService.find_active_sprint(pid)
        if active:
            return active
        # get_delta_dir ahora resuelve automaticamente desde el registry
        # (via get_project_dir -> _project_dir_from_registry)
        from libs.path_resolver import get_delta_dir

        delta_dir = str(get_delta_dir(pid))
        os.makedirs(delta_dir, exist_ok=True)
        sprint_name = _slugify_sprint_name(goal)
        sprint_dir = os.path.join(delta_dir, sprint_name)
        if not os.path.isdir(sprint_dir):
            os.makedirs(sprint_dir, exist_ok=True)
            return sprint_dir
        # Colisión de nombre (sprint del mismo slug): anexar timestamp
        sprint_dir = os.path.join(
            delta_dir,
            f"{sprint_name}-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}",
        )
        os.makedirs(sprint_dir, exist_ok=True)
        return sprint_dir

    # Sin proyecto: directorio temporal (fallback solo bootstrap)
    import tempfile

    d = os.path.join(tempfile.gettempdir(), "walker", "sessions", ctx.session_id)
    os.makedirs(d, exist_ok=True)
    return d


def _get_active_record(ctx):
    from features.engine.domain.session import SessionStore

    try:
        return SessionStore.get_active()
    except Exception:
        return None


def _resolve_project_root(project_id: str) -> str:
    """Resuelve el directorio cognitivo del proyecto (.cognitive/projects/<id>)."""
    if not project_id:
        return ""
    from libs.path_resolver import get_project_dir

    try:
        return str(get_project_dir(project_id))
    except Exception:
        return ""


def _get_current_spec_index(sprint_dir: str) -> int:
    """Retorna el índice de la spec actual desde el DELTA (fuente unica).

    PRINCIPIO: el estado de negocio del spring vive en el nodo spring del
    delta (SpringService.get_spring_context). No se lee sprint-state.json.
    """
    try:
        from features.delta_tracking import spring_service

        spring_svc = spring_service(chain_dir=sprint_dir)
        if not spring_svc:
            return 0
        spring_id = spring_svc.get_spring_id(sprint_dir)
        spring = spring_svc.get_spring(spring_id)
        if not spring:
            return 0
        return int(
            spring.get(
                "current_spec_index",
                spring.get("current_task_index", 0),
            )
        )
    except Exception:
        return 0


def _slugify_sprint_name(goal: str) -> str:
    """Genera un nombre de spring formal desde el goal (sprint-{slug}).

    Reglas:
      - Toma la primera frase del goal (hasta ':' o ';').
      - Normaliza: lowercase, espacios->guiones, sin acentos/caracteres raros.
      - Recorta a 40 chars.
    Fallback: sprint-{timestamp} si el goal es vacío o no genera slug.
    Ej: 'Rediseno del contrato autoritativo del workflow: resolver gaps...'
        -> 'sprint-rediseno-contrato-autoritativo-wor'
    """
    import re
    import unicodedata

    text = (goal or "").strip()
    for sep in (":", ";"):
        if sep in text:
            text = text.split(sep, 1)[0]
            break
    if not text:
        return f"sprint-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"

    # Normalizar unicode (eliminar acentos) y quedarnos con alfanumericos
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    text = re.sub(r"-{2,}", "-", text)[:40].rstrip("-")
    if not text:
        return f"sprint-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    return f"sprint-{text}"
