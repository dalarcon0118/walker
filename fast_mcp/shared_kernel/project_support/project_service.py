"""project_support.project_service — Resolución de rutas de proyecto (kernel).

Port del ProjectService legacy (features/project/application/services) —
SOLO la superficie que otros features consumen: resolve_paths,
resolve_manifest, resolve_from_registry, find_cognitive_root.
from_chain_dir/from_project_id (dominio Project/ProjectManifest) llegan con
el port completo de features/project.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from fast_mcp.shared_kernel import paths
from fast_mcp.shared_kernel.path_manager import PathManager


class ProjectResolutionError(Exception):
    """Error resolviendo paths de proyecto."""


class ProjectNotFoundError(Exception):
    """Proyecto no encontrado."""


@dataclass
class ProjectPaths:
    """VO — rutas calculadas de un proyecto (todas absolutas, sin IO extra).

    Layout SSOT:
      Capa 1 (proyecto): <repo>/.cognitive/projects/<pid>/data/ — manifiesto
        (project.json) + memorias (cpg/ckg/trace) + estado del watcher.
      Capa 2 (walker):   <walker_root>/data/projects.json — registro global
        (project_id → repo_path). Solo RESUELVE rutas.
    """

    chain_dir: str
    project_root: str
    project_id: str
    data_dir: str
    delta_dir: str
    workflow_dir: str
    traces_dir: str
    cpg_path: str
    ckg_path: str
    project_json_path: str
    workflow_json_path: str
    repo_path: str
    cognitive_dir: str
    walker_root: str
    sprint_id: str = ""


class ProjectService:
    """Fachada de resolución de proyecto (solo métodos estáticos usados hoy)."""

    # ── Path resolution (sin cargar el proyecto completo) ─────────────

    @staticmethod
    def resolve_manifest(project_dir: str | os.PathLike) -> dict:
        """Lee el project.json local de un directorio (SSOT del perfil).

        Busca en cascada (mayor a menor precedencia):
          1. <dir>/data/project.json (canónico)
          2. <dir>/project.json (legacy)
          3. <dir>/.cognitive/projects/<pid>/data/project.json (descentralizado)
        """
        base = str(project_dir)
        candidates = [
            os.path.join(base, "data", "project.json"),
            os.path.join(base, "project.json"),
        ]
        cog_projects = os.path.join(base, ".cognitive", "projects")
        if os.path.isdir(cog_projects):
            for pid in sorted(os.listdir(cog_projects)):
                candidates.append(
                    os.path.join(cog_projects, pid, "data", "project.json")
                )
        for candidate in candidates:
            if os.path.isfile(candidate):
                try:
                    with open(candidate) as f:
                        return json.load(f)
                except (json.JSONDecodeError, OSError):
                    return {}
        return {}

    @staticmethod
    def resolve_paths(chain_dir: str) -> ProjectPaths:
        """Resuelve todas las rutas desde un chain_dir (vía kernel paths)."""
        from fast_mcp.shared_kernel.paths import (
            get_ckg_path,
            get_cognitive_dir,
            get_cpg_path,
            get_data_dir,
            get_delta_dir,
            get_trace_dir,
            resolve_project_id,
            resolve_project_root,
            walker_root,
        )

        if not chain_dir:
            raise ProjectResolutionError("chain_dir vacio — no se puede resolver paths")

        try:
            project_root = resolve_project_root(chain_dir)
        except FileNotFoundError as e:
            raise ProjectResolutionError(str(e))

        project_id = resolve_project_id(chain_dir)
        pid_str = project_id or os.path.basename(project_root)

        # data_dir canónico (ADR-WALKER-054 D4): el layout cognitivo del
        # proyecto ES la fuente de verdad — <repo>/.cognitive/projects/<pid>/data.
        # El legacy <repo>/data es SOLO fallback para proyectos sin .cognitive
        # (tests, proyectos simples sin preparar) — misma cascada que
        # FileWatcher._default_data_dir y ProjectLayout.data_dir.
        #
        # ANTES esto invertía la prioridad (legacy <repo>/data primero si existía),
        # lo que desalineaba CPG/CKG/grep/read del data_dir canónico que usan
        # prepare/status/watcher → el CPG se escribía en una base parásita
        # distinta a la que lee el watcher (cpg_status stale permanente).
        data_dir = os.path.join(project_root, "data")
        cog_layout = os.path.join(
            project_root, ".cognitive", "projects", pid_str, "data"
        )
        if os.path.isdir(cog_layout):
            data_dir = cog_layout
        elif not os.path.isdir(data_dir):
            try:
                data_dir = str(get_data_dir(pid_str))
            except Exception:  # noqa: BLE001
                pass
        try:
            delta_dir = str(get_delta_dir(pid_str))
        except (FileNotFoundError, Exception):
            delta_dir = os.path.join(data_dir, "spring")
        _pm = PathManager()
        workflow_dir = str(_pm.engine_workflow_dir())
        trace_dir = str(get_trace_dir(pid_str))
        cpg_path = str(get_cpg_path(pid_str))
        ckg_path = str(get_ckg_path(pid_str))

        project_json = os.path.join(data_dir, "project.json")
        workflow_json = str(_pm.engine_manifest())

        # Repo info — del project.json ya resuelto (SSOT local).
        repo_path = ""
        if project_json and os.path.exists(project_json):
            try:
                with open(project_json) as _f:
                    _manifest = json.load(_f)
                _proj = _manifest.get("project", _manifest)
                _loc = _proj.get("location", {})
                repo_path = _loc.get("repo_path", "")
            except (json.JSONDecodeError, OSError):
                pass

        cognitive_dir = ""
        walker_root_str = ""
        try:
            cognitive_dir = str(get_cognitive_dir())
        except Exception:  # noqa: BLE001
            pass
        try:
            walker_root_str = str(walker_root())
        except Exception:  # noqa: BLE001
            pass

        return ProjectPaths(
            chain_dir=chain_dir,
            project_root=project_root,
            project_id=pid_str,
            data_dir=data_dir,
            delta_dir=delta_dir,
            workflow_dir=workflow_dir,
            traces_dir=trace_dir,
            cpg_path=cpg_path,
            ckg_path=ckg_path,
            project_json_path=project_json,
            workflow_json_path=workflow_json,
            repo_path=repo_path,
            cognitive_dir=cognitive_dir,
            walker_root=walker_root_str,
        )

    # ── Discovery ─────────────────────────────────────────────────────

    @staticmethod
    def find_cognitive_root(start: str | None = None) -> str | None:
        """Busca el directorio cognitivo caminando hacia arriba desde start."""
        cognitive = paths.find_cognitive_root(start)
        return str(cognitive) if cognitive else None

    @staticmethod
    def resolve_from_registry(start: str | None = None) -> dict | None:
        """Resuelve {project_id, repo_path} desde el CWD (o start) vía registro.

        Git-like: 1) manifest del layout cognitivo más cercano (resolve via
        project.json), 2) registro global por repo_path que contenga start.
        """
        from fast_mcp.shared_kernel.paths import (
            resolve_project_id,
            resolve_project_root,
        )

        base = str(Path(start or os.getcwd()).resolve())
        try:
            root = resolve_project_root(base)
        except FileNotFoundError:
            root = ""
        if root:
            pid = resolve_project_id(root) or os.path.basename(root)
            manifest = ProjectService.resolve_manifest(root)
            proj = manifest.get("project", manifest)
            repo = (proj.get("location") or {}).get("repo_path", "")
            if pid:
                return {"project_id": pid, "repo_path": repo or root}
        # Fallback registro global: repo_path que contenga el start.
        reg_path = paths.registry_path()
        if reg_path.exists():
            try:
                with open(reg_path) as f:
                    projects = json.load(f).get("projects", {})
                for pid, info in projects.items():
                    repo = info.get("repo_path", "")
                    if repo and base.startswith(repo):
                        return {"project_id": pid, "repo_path": repo}
            except (json.JSONDecodeError, OSError):
                pass
        return None

    @staticmethod
    def find_active_sprint(
        project_id: str,
        spring_service=None,
    ) -> str | None:
        """Sprint activo vía DeltaGraph (SpringService, Fase 3).

        Port fiel de la defensiva del legacy: cualquier fallo (incluida la
        ausencia del context delta_tracking, aún no portado) → None.
        """
        try:
            svc = spring_service
            if svc is None:
                from fast_mcp.features.delta_tracking import (  # noqa: F401
                    SpringService,
                    spring_service,
                )
                from fast_mcp.shared_kernel.paths import get_project_dir

                project_root = str(get_project_dir(project_id))
                if os.path.isdir(project_root):
                    svc = SpringService(project_root)
            if svc:
                active = svc.get_active_spring()
                if active:
                    spring_id = active.get("_id", "")
                    if spring_id:
                        from fast_mcp.shared_kernel.paths import get_sprint_dir

                        sprint_dir = str(get_sprint_dir(project_id, spring_id))
                        if os.path.isdir(sprint_dir):
                            return sprint_dir
        except Exception:  # noqa: BLE001 - defensiva legacy
            pass
        return None
