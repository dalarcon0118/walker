"""
application/use_cases.py — Spring use cases for the workflow.

Each use case:
  - Is a thin orchestrator: calls domain services + infrastructure repos
  - Contains NO business logic (that's in domain/services.py)
  - Contains NO IO directly (that's in infrastructure/repositories.py)

The workflow calls these via registered tasks (features/task_execution/tasks/).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from libs.shared_domain import ExecutionContext
from ..domain.services import (
    SpecValidator,
    SpecBuilder,
    SprintPlanBuilder,
    SpecStatusUpdater,
)
from ..infrastructure.repositories import SprintStateRepository, PlanRepository
from features.engine.domain.models import WorkflowState


class PlanSprintUseCase:
    """Creates a new sprint: plan.json + sprint-state.json + session."""

    @staticmethod
    def _from_payload(data: dict, key: str, default=None):
        """Lee un campo desde _payload (REST body) o desde data directa."""
        payload = data.get("_payload")
        if isinstance(payload, dict) and key in payload:
            return payload.get(key, default)
        return data.get(key, default)

    @staticmethod
    def execute(ctx: ExecutionContext, data: dict) -> dict:
        sprint_dir = _resolve_chain_dir(ctx, project_id=data.get("_project_id", ""))
        goal = PlanSprintUseCase._from_payload(data, "goal", "")
        specs_raw = PlanSprintUseCase._from_payload(
            data, "tasks", PlanSprintUseCase._from_payload(data, "specifications", [])
        )

        sprint_id = os.path.basename(sprint_dir)
        plan = SprintPlanBuilder.build_plan(sprint_id, goal, specs_raw)
        PlanRepository().save(sprint_dir, plan)

        record = _get_active_record(ctx)
        project_id = record.project_id if record else ""
        state = SprintPlanBuilder.build_initial_state(
            sprint_dir,
            sprint_id,
            goal,
            project_id,
            ctx.session_id or "",
            plan["specifications"],
        )
        SprintStateRepository().save_state_dict(sprint_dir, state)
        # Sincronizar .workflow-state.json (canonico del engine)
        try:
            from features.engine.infrastructure.repositories import (
                WorkflowStateRepository,
            )

            wf_state = WorkflowState.from_dict(state)
            WorkflowStateRepository().save(sprint_dir, wf_state)
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
            "spec_count": len(plan["specifications"]),
            "task_count": len(plan["specifications"]),
            "chain_dir": sprint_dir,
            "status": "planned",
        }


class SpecifySpecUseCase:
    """Creates or refines a specification in plan.json.

    Called by spec_task workflow action.
    """

    @staticmethod
    def execute(ctx: ExecutionContext, data: dict) -> dict:
        sprint_dir = ctx.chain_dir or ""
        plan_repo = PlanRepository()

        # Fuente unica: delta (SpringGraphService). Fallback a plan.json
        # si el spring no esta indexado en el delta.
        spring_svc = None
        try:
            from features.delta_tracking.application.spring_graph_service import (
                SpringGraphService,
            )

            spring_svc = SpringGraphService.from_chain_dir(sprint_dir)
            if spring_svc:
                import os

                spring_id = os.path.basename(os.path.normpath(sprint_dir))
                spring_svc.ensure_spring_imported(sprint_dir, spring_id)
                specs = spring_svc.get_specs_legacy(spring_id)
            else:
                specs = []
        except Exception:
            spring_svc = None
            specs = []
        if spring_svc is None:
            try:
                plan = plan_repo.load(sprint_dir)
                specs = plan.get("specifications", plan.get("tasks", []))
            except Exception:
                specs = []

        # Phase 1: Create initial spec if plan is empty
        is_initial = data.get("spec_id", data.get("task_id")) and not data.get(
            "result_state"
        )
        if not specs and is_initial:
            new_spec = SpecBuilder.create_default(0, data)
            if spring_svc:
                spring_svc.add_spec(
                    spring_svc.get_spring_id(sprint_dir)
                    or os.path.basename(os.path.normpath(sprint_dir)),
                    objective=new_spec.get("objective", ""),
                    domain=new_spec.get("domain", ""),
                    spec_type=new_spec.get("type", "feature"),
                    spec_index=0,
                    status="draft",
                )
            else:
                specs.append(new_spec)
                plan = plan_repo.load(sprint_dir)
                plan["specifications"] = specs
                plan_repo.save(sprint_dir, plan)
            # SSOT: sprint-state lo escribe el store (unico dueno)
            SprintStateRepository().save_context(sprint_dir, {"current_spec_index": 0})
            return {
                "spec_id": new_spec["spec_id"],
                "status": "draft",
                "message": f"Spec {new_spec['spec_id']} created.",
            }

        # Phase 2: Get current index
        current_idx = _get_current_spec_index(sprint_dir)
        if not specs:
            raise ValueError("No specifications in plan.json")
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
        if spring_svc:
            spring_id = spring_svc.get_spring_id(sprint_dir) or os.path.basename(
                os.path.normpath(sprint_dir)
            )
            spec_id = refined.get(
                "spec_id", refined.get("task_id", f"SPEC-{current_idx + 1:03d}")
            )
            spring_svc.update_spec(spring_id, spec_id, refined)
            # Proyeccion de compatibilidad
            try:
                spring_svc.sync_plan_projection(spring_id)
            except Exception:
                pass
        else:
            plan = plan_repo.load(sprint_dir)
            plan["specifications"] = specs
            plan_repo.save(sprint_dir, plan)

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
        state = SprintStateRepository().load(sprint_dir)
        current_idx = state.context.get("current_spec_index", 0)
        new_idx = current_idx + 1
        # SSOT: sprint-state.json lo actualiza el store (unico dueno)
        SprintStateRepository().save_context(
            sprint_dir, {"current_spec_index": new_idx}
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
        plan = PlanRepository().load(sprint_dir)
        specs = plan.get("specifications", plan.get("tasks", []))
        for i, spec in enumerate(specs):
            if spec.get("spec_id", spec.get("task_id")) == spec_id:
                specs[i] = SpecStatusUpdater.update(spec, new_status)
                plan["specifications"] = specs
                PlanRepository().save(sprint_dir, plan)
                return {"spec_id": spec_id, "status": new_status}
        raise ValueError(f"Spec {spec_id} not found in plan")


class ShowDashboardUseCase:
    """Returns the current sprint dashboard state."""

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
            state = SprintStateRepository().load(sprint_dir)
        except Exception:
            return {
                "sprint_id": "",
                "goal": "",
                "specs": [],
                "spec_count": 0,
                "completed_count": 0,
            }
        sd = state.to_dict()
        specs = sd.get("specifications", sd.get("tasks", []))
        goal = state.sprint.get("goal", "")
        total = len(specs)
        completed = sum(
            1 for s in specs if isinstance(s, dict) and s.get("status") == "completed"
        )
        return {
            "sprint_id": state.sprint.get("chain_id", ""),
            "goal": goal,
            "specs": specs,
            "spec_count": total,
            "task_count": total,
            "completed_count": completed,
        }


class CloseSprintUseCase:
    """Closes the current sprint."""

    @staticmethod
    def execute(ctx: ExecutionContext, data: dict) -> dict:
        sprint_dir = ctx.chain_dir or ""
        if not sprint_dir:
            raise ValueError("chain_dir not available")
        state = SprintStateRepository().load(sprint_dir)
        new_state_data = state.to_dict()
        new_state_data.setdefault("sprint", {})["status"] = "closed"
        new_state_data["sprint"]["closed_at"] = datetime.now(timezone.utc).isoformat()
        SprintStateRepository().save_state_dict(sprint_dir, new_state_data)
        return {"status": "closed", "sprint_id": state.sprint.get("chain_id", "")}


class EditSprintUseCase:
    """Edits an existing sprint: adds/updates specs without overwriting.

    Unlike PlanSprintUseCase (which creates from scratch), this merges
    changes: adds new specs, updates the goal, preserves existing specs.
    """

    @staticmethod
    def execute(ctx: ExecutionContext, data: dict) -> dict:
        sprint_dir = ctx.chain_dir or ""
        plan = PlanRepository().load(sprint_dir)
        specs = plan.get("specifications", plan.get("tasks", []))

        # Update goal if provided
        if data.get("goal"):
            plan["goal"] = data["goal"]

        # Add new specs from planning input
        specs_raw = data.get("tasks", data.get("specifications", []))
        if specs_raw:
            existing_count = len(specs)
            for i, item in enumerate(specs_raw):
                spec = SpecBuilder.create_from_plan(existing_count + i, item)
                specs.append(spec)
            plan["specifications"] = specs

        # Update existing spec if spec_id matches
        spec_updates = data.get("spec_updates", [])
        for update in spec_updates:
            update_id = update.get("spec_id", update.get("task_id", ""))
            for i, s in enumerate(specs):
                if s.get("spec_id", s.get("task_id", "")) == update_id:
                    specs[i] = SpecBuilder.refine(s, update)
                    break

        plan["specifications"] = specs
        PlanRepository().save(sprint_dir, plan)
        return {
            "modified": True,
            "spec_count": len(specs),
            "goal": plan.get("goal", ""),
        }


class UpdateSprintStatusUseCase:
    """Updates sprint status: planning → active → reviewing → closed."""

    @staticmethod
    def execute(ctx: ExecutionContext, data: dict) -> dict:
        sprint_dir = ctx.chain_dir or ""
        new_status = data.get("status", "")
        if not new_status:
            raise ValueError("Requires --status")
        state = SprintStateRepository().load(sprint_dir)
        current = state.sprint.get("status", "planning")
        from ..domain.services import SprintStatusUpdater

        allowed, reason = SprintStatusUpdater.can_transition(current, new_status)
        if not allowed:
            raise ValueError(reason)
        new_state_data = state.to_dict()
        new_state_data.setdefault("sprint", {})["status"] = new_status
        SprintStateRepository().save_state_dict(sprint_dir, new_state_data)
        return {"previous_status": current, "status": new_status}


class ValidatePlanUseCase:
    """Validates all specifications in plan.json."""

    @staticmethod
    def execute(ctx: ExecutionContext, data: dict) -> dict:
        sprint_dir = ctx.chain_dir or ""
        try:
            plan = PlanRepository().load(sprint_dir)
        except FileNotFoundError:
            return {"valid": False, "errors": ["plan.json not found"], "spec_count": 0}
        specs = plan.get("specifications", plan.get("tasks", []))
        if not specs:
            return {
                "valid": False,
                "errors": ["No specifications in plan.json"],
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


def _resolve_chain_dir(ctx: ExecutionContext, project_id: str = "") -> str:
    """Resolve or create sprint chain directory.

    SSOT: Delegates to ProjectService for path resolution.
    Never uses dirname(dirname()) or hardcoded paths.
    Usa project_id de data cuando no hay sesion activa (CLI directo).
    """
    cd = ctx.chain_dir or ""
    if (
        cd
        and os.path.isdir(cd)
        and os.path.exists(os.path.join(cd, "sprint-state.json"))
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
        sprint_dir = os.path.join(
            delta_dir,
            f"sprint-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}",
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


def _get_current_spec_index(sprint_dir: str) -> int:
    state_path = os.path.join(sprint_dir, "sprint-state.json")
    if not os.path.exists(state_path):
        return 0
    try:
        with open(state_path) as f:
            st = json.load(f)
        return st.get("context", {}).get(
            "current_spec_index", st.get("context", {}).get("current_task_index", 0)
        )
    except Exception:
        return 0
