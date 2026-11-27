"""handlers/sprint.py — Sprint planning handlers for delta_tracking.

Handles:
  - sprint_planning: create a new sprint with specs
  - resolve_project_sprint: find the active sprint for a project
"""
from __future__ import annotations

import logging
from typing import Any

from fast_mcp.features.delta_tracking.application.services.sprint_context import (
    resolve_sprint_context,
)
from fast_mcp.features.delta_tracking.application.services.sprint_service import SprintService
from fast_mcp.shared_kernel.objectives import objectives_match

logger = logging.getLogger(__name__)


def handle_sprint_planning(args: dict) -> dict[str, Any]:
    """Handle sprint_planning tool: create a sprint with specs.

    Input:
      - project_id: project identifier
      - goal: sprint goal description
      - tasks: list of task dicts (objective, type, domain, etc.)

    Output:
      - ok, spring_id, spec_count, specs
    """
    project_id = args.get("project_id", "") or args.get("_project_id", "")

    goal = args.get("goal", "")
    tasks = args.get("tasks", [])
    body = args.get("body", {})
    if body:
        goal = goal or body.get("goal", "")
        tasks = tasks or body.get("tasks", [])

    # DI: the composition root injects the repo-backed SprintService
    # ('_sprint_service'). Fail-fast (INV-016): no anemic in-memory
    # fallback — without repo, sprint writes would be silently dropped.
    svc = args.get("_sprint_service")
    if svc is None:
        return {
            "ok": False,
            "error": (
                f"Cannot resolve sprint service for project {project_id}: "
                "no repo-backed SprintService injected ('_sprint_service'). "
                "The composition root must wire it (INV-016 fail-fast)."
            ),
        }

    if not goal:
        return {"ok": False, "error": "goal is required"}

    # Create sprint. `spring_id`/`status` explícitos: un roadmap nace en
    # planning, no activo, y no debe pisar `sprint-<project>` si ya existe.
    from fast_mcp.features.delta_tracking.domain.values import is_valid_spring_status

    spring_id = str(args.get("spring_id") or f"sprint-{project_id}")
    status = str(args.get("status") or "active")
    if not is_valid_spring_status(status):
        return {"ok": False, "error": f"invalid sprint status: {status}"}

    # Agregar tareas a un sprint existente NO puede recrearlo: `create_spring`
    # es un save del doc completo y borraría `context.session_ids`, el dueño y
    # las fechas. Si ya existe, se actualiza el goal y se agregan specs.
    existing = svc.get_spring(spring_id)
    if existing is not None:
        updates: dict[str, Any] = {"goal": goal}
        if args.get("status"):
            updates["status"] = status
        svc.update_spring(spring_id, updates)
        existing_specs = list(svc.get_specs(spring_id) or [])
        specs = []
        duplicates: list[dict[str, str]] = []
        for task in tasks:
            objective = str(task.get("objective", task.get("title", "")) or "")
            match = next(
                (
                    spec
                    for spec in existing_specs
                    if objectives_match(str(spec.get("objective") or ""), objective)
                ),
                None,
            )
            if match is not None:
                duplicates.append(
                    {
                        "objective": objective,
                        "spec_id": str(match.get("spec_id") or ""),
                    }
                )
                continue
            spec = svc.add_spec(
                spring_id=spring_id,
                objective=objective,
                domain=task.get("domain", ""),
                spec_type=task.get("type", "feature"),
                spec_index=None,
                result_state=task.get("result_state", ""),
                files=task.get("files", []),
                depends_on=task.get("depends_on", []),
            )
            specs.append(spec)
            existing_specs.append(spec)
        return {
            "ok": True,
            "spring_id": spring_id,
            "goal": goal,
            "created": False,
            "updated": True,
            "skipped": len(duplicates),
            "duplicates": duplicates,
            "spec_count": len(specs),
            "specs": specs,
        }

    svc.create_spring(
        spring_id=spring_id,
        goal=goal,
        status=status,
        project_id=project_id,
    )

    # Add specs from tasks
    specs = []
    for i, task in enumerate(tasks):
        spec = svc.add_spec(
            spring_id=spring_id,
            objective=task.get("objective", task.get("title", "")),
            domain=task.get("domain", ""),
            spec_type=task.get("type", "feature"),
            spec_index=i,
            result_state=task.get("result_state", ""),
            files=task.get("files", []),
            depends_on=task.get("depends_on", []),
        )
        specs.append(spec)

    return {
        "ok": True,
        "spring_id": spring_id,
        "goal": goal,
        "created": True,
        "updated": False,
        "spec_count": len(specs),
        "specs": specs,
    }


def handle_resolve_project_sprint(args: dict) -> dict[str, Any]:
    """Handle resolve_project_sprint tool: find active sprint.

    Input:
      - project_id: project identifier
      - chain_dir: optional chain_dir override

    Output:
      - ok, spring_id, sprint info, specs
    """
    project_id = args.get("project_id", "") or args.get("_project_id", "")
    chain_dir = args.get("chain_dir", "")
    session_id = args.get("session_id", "") or args.get("session", "") or ""

    # DI first (composition root injects the repo-backed service via
    # delta_tool_runner); the local resolver is a read-only fallback for
    # direct programmatic calls (never used by MCP tools).
    svc = args.get("_sprint_service") or _resolve_sprint_service(
        project_id, chain_dir=chain_dir
    )
    if svc is None:
        return {"ok": False, "error": f"Cannot resolve sprint service for project {project_id}"}

    # Política ÚNICA (ADR-WALKER-085): sesión > activo > planning. Este handler
    # tenía su propia copia de esa cadena; ahora delega para que no vuelva a
    # divergir de `status`, `brief` o `workflow_state`.
    sctx = resolve_sprint_context(
        svc,
        chain_dir or "",
        session_id=session_id,
    )

    if not sctx.found:
        return {
            "ok": True,
            "found": False,
            "message": "No active sprint found",
            "spring_id": "",
            "specs": [],
            "resolved_by": sctx.resolved_by,
            "diagnostics": list(sctx.diagnostics),
        }

    payload = {
        "ok": True,
        "found": True,
        "spring_id": sctx.spring_id,
        "goal": sctx.goal,
        "status": sctx.status,
        "spec_count": len(sctx.specs),
        "specs": list(sctx.specs),
        "resolved_by": sctx.resolved_by,
    }
    if sctx.diagnostics:
        payload["diagnostics"] = list(sctx.diagnostics)
    return payload


def _resolve_sprint_service(project_id: str, chain_dir: str = "") -> SprintService | None:
    """Resolve SprintService from project context.

    Uses DI: the composition root injects path_resolver.
    For now, uses a minimal resolver for the delta_tracking context.
    """
    try:
        from fast_mcp.shared_kernel.paths import (
            get_project_dir,
            get_data_dir,
        )

        if chain_dir:
            return SprintService.from_chain_dir(chain_dir)

        if project_id:
            project_dir = get_project_dir(project_id)
            if project_dir.is_dir():
                return SprintService(project_root=str(project_dir))
    except Exception as e:
        logger.warning("resolve_sprint_service failed: %s", e)

    return None
