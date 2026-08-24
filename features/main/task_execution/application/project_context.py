"""project_context.py — Resolución ÚNICA de contexto de proyecto (ADR-WALKER-053).

Problema que resuelve: dos semánticas fusionadas en ctx.chain_dir (dir de
sprint activo vs raíz del repo) + resolución duplicada en ~15 tasks con el
guard "chain_dir not available". El contexto se resuelve UNA vez aquí y
viaja inyectado; prohibido resolverlo dentro de tasks.

Precedencia EXPLÍCITO > AMBIENTAL:
    payload.project_id / payload._project_id  >  route pid  >
    env WALKER_PROJECT_ID  >  cwd (resolve_from_registry)
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ProjectContext:
    """Contexto resuelto de proyecto para UN despacho."""

    project_id: str          # "" si no resolvió nada
    repo_path: str           # raíz del repo (tasks de código)
    sprint_chain_dir: str    # dir del sprint activo o "" (workflow/delta)

    @property
    def resolved(self) -> bool:
        return bool(self.project_id and self.repo_path)


def resolve_project_context(
    payload_pid: str = "",
    route_pid: str = "",
) -> ProjectContext:
    """Punto ÚNICO de resolución (llamado por handle_task).

    Nunca lanza: devuelve ProjectContext vacío y el despachador decide el
    error accionable (lista de proyectos disponibles).
    """
    pid = (payload_pid or route_pid or os.environ.get("WALKER_PROJECT_ID") or "").strip()

    if not pid:
        # último recurso git-like: cwd del proceso
        try:
            from features.project import ProjectService

            resolved = ProjectService.resolve_from_registry()
            pid = (resolved or {}).get("project_id", "")
        except Exception:
            pid = ""

    if not pid:
        return ProjectContext("", "", "")

    repo_path = ""
    try:
        from features.project.application.services.project_service import (
            ProjectService,
        )

        project = ProjectService.from_project_id(pid)
        repo_path = str(project.manifest.repo_path or "")
    except Exception:
        repo_path = ""

    sprint_chain_dir = ""
    try:
        from features.project.application.services.project_service import (
            ProjectService as _PS,
        )

        sprint_chain_dir = _PS.find_active_sprint(pid) or ""
    except Exception:
        sprint_chain_dir = ""

    return ProjectContext(project_id=pid, repo_path=repo_path, sprint_chain_dir=sprint_chain_dir)


def resolve_repo_path(data: dict, ctx) -> str:
    """Helper de una línea para tasks migradas: repo path efectivo.

    Orden: payload explícito → _project_ctx inyectado por el despachador →
    ctx.chain_dir ambiental (legacy). Las tasks NUNCA arman esta lógica.
    """
    explicit = (data.get("project_id") or data.get("_project_id") or "").strip()
    pctx = data.get("_project_ctx") or {}

    if explicit and explicit != pctx.get("project_id"):
        # proyecto pedido ≠ inyectado: re-resolver solo la raíz
        try:
            from features.project.application.services.project_service import (
                ProjectService,
            )

            return str(ProjectService.from_project_id(explicit).manifest.repo_path or "")
        except Exception:
            pass

    if pctx.get("repo_path"):
        return str(pctx["repo_path"])
    return getattr(ctx, "chain_dir", "") or ""
