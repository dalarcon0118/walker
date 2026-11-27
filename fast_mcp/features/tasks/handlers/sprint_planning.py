"""handlers/sprint_planning.py — Sprint planning task handlers.

Thin adapters between workflow action names and a SprintService-like
object (injected at the composition root). Business logic lives in
delta_tracking; these handlers only connect names.

Per R1 (ADR-071 §3bis): features must NOT import other features.
The SprintService is injected here via closure, never imported directly.

Workflow action names ported from legacy mcp/shared/tasks/actions/
sprint_planning.py:
  - plan_sprint: create a sprint plan from goal/tasks (create spring + specs)
  - spec_task: specify/refine a spec
  - validate_plan: validate all specs in the plan
  - close_sprint: close the current sprint
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any

from fast_mcp.features.tasks.domain.task_registry import ExecutionContext, TaskResult

logger = logging.getLogger(__name__)

# Campos válidos del Spec que spec_task puede persistir (refinement y create).
# Antes solo se persistía un subconjunto y se perdían preconditions,
# quality_gates, task_description (no es campo del Spec), etc.
_SPEC_FIELDS = {
    "objective", "domain", "spec_type", "result_state", "files",
    "description", "predicates", "depends_on", "problem_statement",
    "evidence", "acceptance_criteria", "quality_gates",
    "impacted_artifacts", "task_context", "risks", "preconditions",
    "decision", "architecture", "implementation", "invariants",
    "project_snapshot", "loop_verification", "loop_acceptance",
    "review_metrics", "review_conventions", "review_architecture",
    "review_verdict", "reviewed_by", "review_findings",
    "commit_hash", "commit_url", "completed_at", "generated_from",
    "generates_specs", "related_decisions", "transformation_map",
    "created_at", "updated_at",
}


def _short_hash(text: str) -> str:
    return hashlib.sha1((text or "session").encode("utf-8")).hexdigest()[:8]


def _spec_id(spec: dict) -> str:
    return str(spec.get("spec_id") or spec.get("_id") or spec.get("task_id") or "?")


def _predicates_empty(spec: dict) -> bool:
    preds = spec.get("predicates")
    if isinstance(preds, dict):
        items = preds.get("items")
        if items is not None:
            return not bool(items)
        return not bool(preds)
    if isinstance(preds, list):
        return not bool(preds)
    return not bool(preds)


def _acceptance_empty(spec: dict) -> bool:
    return not bool(spec.get("acceptance_criteria"))


def _build_handlers(sprint_service: Any):
    def _resolve_spring_id(data: dict[str, Any]) -> tuple[str, str]:
        """(spring_id, resolved_by): explícito > sesión (registro) > ''.

        El default del PROYECTO no se usa acá: una operación en nombre de una
        sesión no puede caer en el sprint de otra (ADR-WALKER-077).
        """
        explicit = str(data.get("spring_id") or "").strip()
        if explicit:
            return explicit, "explicit"
        session_id = str(data.get("session_id") or "").strip()
        if session_id and hasattr(sprint_service, "get_session"):
            try:
                record = sprint_service.get_session(session_id) or {}
            except Exception:  # noqa: BLE001
                record = {}
            sid = str(record.get("spring_id") or "")
            if sid:
                return sid, "session"
        return "", ""

    def plan_sprint(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
        """Create or update a sprint plan: creates/updates spring + specs."""
        if sprint_service is None:
            return TaskResult.failure("plan_sprint: sprint_service not configured")

        goal = data.get("goal", "")
        tasks = data.get("tasks", []) or []
        project_id = data.get("project_id", "")
        if not goal:
            return TaskResult.failure("plan_sprint: goal es requerido")

        session_id = str(data.get("session_id") or "").strip()
        # El sprint de la SESIÓN manda (ADR-077/085). Antes esto sólo miraba
        # `data["spring_id"]` y caía a `sprint-<project>`: el `advance` del
        # workflow creaba un sprint PARALELO "sprint-walker" y dejaba el de la
        # sesión intacto (medido 2026-11-24).
        spring_id, _resolved_by = _resolve_spring_id(data)
        if not spring_id:
            spring_id = data.get("spring_id") or (
                f"SPR-S{_short_hash(session_id)}" if session_id else f"sprint-{project_id or 'workflow'}"
            )

        # Detect existing sprint (create-or-update, never overwrite).
        existing = None
        if hasattr(sprint_service, "get_spring"):
            try:
                existing = sprint_service.get_spring(spring_id)
            except Exception:  # noqa: BLE001
                existing = None

        try:
            if existing:
                # Update ONLY provided fields; preserve status and context.
                update_payload: dict[str, Any] = {}
                update_payload["goal"] = goal
                if project_id:
                    update_payload["project_id"] = project_id
                if session_id:
                    update_payload["session_id"] = session_id

                # Merge context: union of session_ids, preserve the rest.
                existing_context = (existing.get("context") if isinstance(existing, dict) else None) or {}
                merged_context = dict(existing_context)
                existing_ids = list(merged_context.get("session_ids") or [])
                session_ids_set = set(existing_ids)
                if session_id:
                    session_ids_set.add(session_id)
                if session_ids_set:
                    merged_context["session_ids"] = sorted(session_ids_set)
                # Never delete context: keep merged (even if empty) when existing had it.
                if existing_context or session_id:
                    update_payload["context"] = merged_context

                if hasattr(sprint_service, "update_spring"):
                    sprint_service.update_spring(spring_id, update_payload)
                else:
                    # Fallback: create_spring with merged dict, preserving status.
                    existing_status = existing.get("status") if isinstance(existing, dict) else "planning"
                    sprint_service.create_spring(
                        spring_id=spring_id,
                        goal=goal,
                        status=existing_status or "planning",
                        project_id=project_id or (existing.get("project_id", "") if isinstance(existing, dict) else ""),
                        session_id=session_id or (existing.get("session_id", "") if isinstance(existing, dict) else ""),
                        context=merged_context,
                    )
                if session_id and hasattr(sprint_service, "open_session"):
                    sprint_service.open_session(
                        session_id, project_id=project_id, spring_id=spring_id, goal=goal
                    )
            else:
                sprint_service.create_spring(
                    spring_id=spring_id,
                    goal=goal,
                    status="planning",
                    project_id=project_id,
                    session_id=session_id,
                    context={"session_ids": [session_id]} if session_id else None,
                )
                if session_id and hasattr(sprint_service, "open_session"):
                    sprint_service.open_session(
                        session_id, project_id=project_id, spring_id=spring_id, goal=goal
                    )
        except Exception as exc:  # noqa: BLE001
            return TaskResult.failure(f"plan_sprint: {type(exc).__name__}: {exc}")

        specs = []
        existing_specs = (
            sprint_service.get_specs(spring_id)
            if hasattr(sprint_service, "get_specs")
            else []
        )
        by_index = {
            int(s.get("spec_index", -1)): s
            for s in (existing_specs or [])
            if s.get("spec_index") is not None
        }
        for i, task in enumerate(tasks):
            try:
                # Idempotencia por ÍNDICE: re-planear no debe borrar lo que la
                # spec ya tenía (predicados, depends_on, acceptance, task_context).
                # Antes `add_spec` recreaba la spec vacía y el segundo `advance`
                # del workflow la pisaba (medido 2026-11-24).
                existing = by_index.get(i)
                if existing and hasattr(sprint_service, "update_spec"):
                    updates: dict[str, Any] = {}
                    objective = task.get("objective", task.get("title", ""))
                    if objective:
                        updates["objective"] = objective
                    if "domain" in task:
                        updates["domain"] = task.get("domain", "")
                    if "type" in task:
                        updates["spec_type"] = task.get("type", "feature")
                    if "result_state" in task:
                        updates["result_state"] = task.get("result_state", "")
                    if "files" in task:
                        updates["files"] = task.get("files", [])
                    if "depends_on" in task:
                        updates["depends_on"] = task.get("depends_on", [])
                    spec = sprint_service.update_spec(
                        spring_id, str(existing.get("spec_id") or ""), updates
                    ) or existing
                else:
                    spec = sprint_service.add_spec(
                        spring_id=spring_id,
                        objective=task.get("objective", task.get("title", "")),
                        domain=task.get("domain", ""),
                        spec_type=task.get("type", "feature"),
                        spec_index=i,
                        result_state=task.get("result_state", ""),
                        files=task.get("files", []),
                    )
                specs.append(spec)
            except Exception as exc:  # noqa: BLE001
                logger.warning("plan_sprint: spec %d falló (%s)", i, exc)

        return TaskResult.success(data={
            "spring_id": spring_id,
            "goal": goal,
            "spec_count": len(specs),
            "specs": specs,
        })

    def validate_plan(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
        """Validate all specs in the sprint plan."""
        if sprint_service is None:
            return TaskResult.failure("validate_plan: sprint_service not configured")

        spring_id, _ = _resolve_spring_id(data)
        specs = []
        if spring_id:
            try:
                specs = sprint_service.get_specs(spring_id, status="draft") or sprint_service.get_specs(spring_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("validate_plan: get_specs falló (%s)", exc)
                try:
                    specs = sprint_service.get_specs(spring_id)
                except Exception:  # noqa: BLE001
                    specs = []

        missing_specs = [s for s in specs if not s.get("objective")]
        acceptance_missing_specs = [
            s for s in specs
            if _acceptance_empty(s) and _predicates_empty(s)
        ]

        errors = []
        for s in missing_specs:
            errors.append(f"spec {_spec_id(s)}: objective requerido")
        for s in acceptance_missing_specs:
            errors.append(
                f"spec {_spec_id(s)}: acceptance_criteria o predicates requeridos"
            )

        missing_ids = [_spec_id(s) for s in missing_specs]
        acceptance_missing_ids = [_spec_id(s) for s in acceptance_missing_specs]

        return TaskResult.success(data={
            "valid": not missing_specs and not acceptance_missing_specs,
            "errors": errors,
            "spec_count": len(specs),
            "missing": missing_ids,
            "acceptance_missing": acceptance_missing_ids,
        })

    def spec_task(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
        """Specify/refine a spec. Si no es válida → failure (no auto-advance)."""
        if sprint_service is None:
            return TaskResult.failure("spec_task: sprint_service not configured")

        spring_id, _ = _resolve_spring_id(data)
        objective = data.get("objective", "") or data.get("title", "")
        spec_id = str(data.get("spec_id") or "").strip()

        if not spring_id:
            return TaskResult.failure(
                "spec_task: spec incompleta (objective requerido; spring_id explícito o sesión con sprint ligado)"
            )

        # Refinement mode.
        if spec_id:
            if not hasattr(sprint_service, "get_spec"):
                return TaskResult.failure("spec_task: sprint_service no soporta get_spec")
            # Normalizar: aceptar tanto "SPEC-002" como doc_id "SPR-1:SPEC-002".
            lookup_id = spec_id
            try:
                existing = sprint_service.get_spec(spring_id, lookup_id)
            except Exception as exc:  # noqa: BLE001
                return TaskResult.failure(f"spec_task: {type(exc).__name__}: {exc}")
            if existing is None and ":" in lookup_id:
                lookup_id = lookup_id.split(":", 1)[-1]
                try:
                    existing = sprint_service.get_spec(spring_id, lookup_id)
                except Exception as exc:  # noqa: BLE001
                    return TaskResult.failure(f"spec_task: {type(exc).__name__}: {exc}")
            spec_id = lookup_id
            if not existing:
                return TaskResult.failure(
                    f"spec_task: spec inexistente: {spec_id}"
                )

            # Solo claves provistas. Se persisten TODOS los campos válidos del
            # Spec (no solo un subconjunto): antes se perdían preconditions,
            # quality_gates, etc. (bug de canal).
            updates: dict[str, Any] = {}
            if "title" in data and "objective" not in data:
                updates["objective"] = data.get("title")
            # Alias del input_schema del workflow.
            if "task_description" in data:
                updates["problem_statement"] = data.get("task_description")
            for key, value in (data or {}).items():
                if key in ("title", "spring_id", "spec_id", "session_id",
                           "project_id", "task_id", "_human", "action",
                           "chain_dir", "task_description"):
                    continue
                # Alias del input_schema del workflow: `type` -> `spec_type`.
                if key == "type":
                    updates["spec_type"] = value
                    continue
                if key in _SPEC_FIELDS:
                    updates[key] = value
            # `status="pending"` es el marcador interno del sub-estado destino
            # que siembra el motor al entrar (TransitionService), no un estado
            # de spec. Reenviarlo rompía `advance` con "Invalid spec status".
            if "status" in updates and updates.get("status") in (None, "", "pending"):
                updates.pop("status", None)

            # Remove None values to avoid merge overwriting? Requirement: solo claves provistas.
            # Keep provided empty lists if explicitly given; but drop None.
            updates = {k: v for k, v in updates.items() if v is not None}

            if not hasattr(sprint_service, "update_spec"):
                return TaskResult.failure("spec_task: sprint_service no soporta update_spec")
            try:
                updated_spec = sprint_service.update_spec(spring_id, spec_id, updates)
            except Exception as exc:  # noqa: BLE001
                return TaskResult.failure(f"spec_task: {type(exc).__name__}: {exc}")
            if not updated_spec:
                return TaskResult.failure(f"spec_task: no se pudo actualizar {spec_id}")

            return TaskResult.success(data={
                "spec_id": spec_id,
                "valid": True,
                "objective": updated_spec.get("objective", objective),
                "status": updated_spec.get("status", "draft"),
                # La spec especificada ES la tarea: si el caller nombró
                # SPEC-002, el workflow debe recordar SPEC-002 (no el task_id
                # anterior). Sin esto, el ciclo siguiente de TASK_SPEC seguía
                # escribiendo sobre la spec vieja (medido 2026-11-24).
                "task_id": spec_id,
                "updated": True,
            })

        # Create mode (no spec_id).
        if not objective:
            return TaskResult.failure(
                "spec_task: spec incompleta (objective requerido; spring_id explícito o sesión con sprint ligado)"
            )

        try:
            spec = sprint_service.add_spec(
                spring_id=spring_id,
                objective=objective,
                domain=data.get("domain", ""),
                spec_type=data.get("type", "feature"),
                spec_index=data.get("spec_index"),
                result_state=data.get("result_state", ""),
                files=data.get("files", []),
                status=data.get("status", "draft"),
            )
        except Exception as exc:  # noqa: BLE001
            return TaskResult.failure(f"spec_task: {type(exc).__name__}: {exc}")

        spec_id = (spec.get("spec_id") if isinstance(spec, dict) else "") or (spec.get("_id", "") if isinstance(spec, dict) else "")
        # Apply the remaining valid Spec fields afterwards (add_spec solo acepta
        # un subconjunto). Antes solo se aplicaban acceptance_criteria y
        # predicates: se perdían preconditions, quality_gates, etc.
        _already = {"objective", "domain", "spec_type", "result_state", "files",
                    "status", "description", "depends_on", "spring_id", "spec_index"}
        if spec_id:
            updates = {}
            if "task_description" in data:
                updates["problem_statement"] = data.get("task_description")
            for key, value in (data or {}).items():
                if key in ("title", "spring_id", "spec_id", "session_id",
                           "project_id", "task_id", "_human", "action",
                           "chain_dir", "status", "spec_index",
                           "task_description"):
                    continue
                if key == "type":
                    if "spec_type" not in _already:
                        updates["spec_type"] = value
                    continue
                if key in _SPEC_FIELDS and key not in _already:
                    updates[key] = value
            if "title" in data and "objective" not in data:
                updates["objective"] = data.get("title")
            updates = {k: v for k, v in updates.items() if v is not None}
            if updates and hasattr(sprint_service, "update_spec"):
                try:
                    sprint_service.update_spec(spring_id, spec_id, updates)
                    refreshed = None
                    if hasattr(sprint_service, "get_spec"):
                        refreshed = sprint_service.get_spec(spring_id, spec_id)
                    if refreshed:
                        spec = refreshed
                except Exception as exc:  # noqa: BLE001
                    logger.warning("spec_task: update_spec falló (%s)", exc)

        spec_id = (spec.get("spec_id") if isinstance(spec, dict) else "") or (spec.get("_id", "") if isinstance(spec, dict) else "") or spec_id
        return TaskResult.success(data={
            "spec_id": spec_id,
            "valid": True,
            "objective": spec.get("objective", objective) if isinstance(spec, dict) else objective,
            "status": spec.get("status", "draft") if isinstance(spec, dict) else data.get("status", "draft"),
            "task_id": data.get("task_id") or spec_id,
            "updated": False,
        })

    def close_sprint(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
        """Close the current sprint, settling its tasks first.

        El cierre NO puede dejar specs `in_progress` en un sprint cerrado: eso
        fue el estado real del 2026-11-19. La regla vive en
        `SprintService.close_spring` (dominio/aplicación), no acá.
        """
        if sprint_service is None:
            return TaskResult.failure("close_sprint: sprint_service not configured")

        spring_id, resolved_by = _resolve_spring_id(data)
        if not spring_id:
            # Sin sesión ni spring_id no hay contexto: se usa el default del
            # proyecto, pero se declara en la respuesta (`resolved_by`) para que
            # cerrar el sprint de otro no sea invisible.
            try:
                active = sprint_service.get_active_spring()
                spring_id = (active.get("spring_id") or active.get("_id") or "") if active else ""
                resolved_by = "project_default"
            except Exception as exc:  # noqa: BLE001
                return TaskResult.failure(f"close_sprint: no se pudo resolver spring activo: {exc}")
        if not spring_id:
            return TaskResult.failure("close_sprint: no hay sprint activo")

        try:
            result = sprint_service.close_spring(
                spring_id,
                force=bool(data.get("force")),
                reason=str(data.get("reason") or ""),
            )
        except Exception as exc:  # noqa: BLE001
            return TaskResult.failure(f"close_sprint: {type(exc).__name__}: {exc}")

        if isinstance(result, dict) and result.get("ok") is False:
            return TaskResult.failure(str(result.get("error") or "close_sprint falló"))
        payload = dict(result or {})
        payload.setdefault("spring_id", spring_id)
        payload.setdefault("status", "closed")
        payload["resolved_by"] = resolved_by
        return TaskResult.success(data=payload)

    return plan_sprint, validate_plan, spec_task, close_sprint


def register(registry, sprint_service: Any = None) -> None:
    """Register sprint planning workflow actions.

    Args:
        registry: TaskRegistry to register into.
        sprint_service: SprintService-like object injected by the
            composition root (R1: never imported here directly).
    """
    plan_sprint, validate_plan, spec_task, close_sprint = _build_handlers(sprint_service)
    for name, fn, desc in [
        ("plan_sprint", plan_sprint, "Create sprint plan from goal and tasks"),
        ("spec_task", spec_task, "Specify or refine a specification"),
        ("validate_plan", validate_plan, "Validate all specifications in the plan"),
        ("close_sprint", close_sprint, "Close the current sprint"),
    ]:
        registry.register(name, fn, metadata={
            "description": desc,
            "tags": ["planning", "sprint"],
            "scope": "internal",
        })
