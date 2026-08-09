"""
application/service.py — Application service for project operations.

SRP: Unica responsabilidad = orquestar operaciones de proyecto.
SSOT: Delega en libs.path_resolver para resolucion de paths (la unica
      fuente de verdad). Delega en domain (values/entities/ports) y
      domain/services.py para logica pura.

TODA resolucion de paths en el codebase debe pasar por esta clase.
NADIE debe importar libs.path_resolver directamente desde ahora.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ..domain.values import ProjectId, ProjectPaths, ProjectManifest
from ..domain.entities import Project
from ..domain.ports import ProjectRepository
from ..domain import services as domain_services


class ProjectResolutionError(Exception):
    """Error resolviendo paths de proyecto."""


class ProjectNotFoundError(Exception):
    """Proyecto no encontrado."""


class ProjectService:
    """Application service — unico entry point para operaciones de proyecto.

    Uso:
        # Desde chain_dir (recomendado)
        project = ProjectService.from_chain_dir(chain_dir)
        ckg_path = project.paths.ckg_path

        # Desde project_id
        project = ProjectService.from_project_id("delta-skill")

        # Paths sin cargar el proyecto completo
        paths = ProjectService.resolve_paths(chain_dir)
        ckg_path = paths.ckg_path

        # Descubrimiento
        cognitive = ProjectService.find_cognitive_root()
        projects = ProjectService.list_available()
    """

    # ── Factory methods ───────────────────────────────────────────────

    @staticmethod
    def from_chain_dir(
        chain_dir: str, repo: ProjectRepository | None = None
    ) -> Project:
        """Construye un Project desde un chain_dir (sprint activo).

        Args:
            chain_dir: Directorio del sprint activo.
            repo: Repositorio opcional para cargar datos adicionales.

        Returns:
            Project con paths resueltos.

        Raises:
            ProjectResolutionError: si no se puede resolver el proyecto.
        """
        paths = ProjectService.resolve_paths(chain_dir)
        project_id = ProjectId(paths.project_id)

        project = Project(
            project_id=project_id,
            paths=paths,
            active_sprint_id=paths.sprint_id,
        )

        # Cargar perfil (manifest) desde project.json si hay repositorio
        if repo:
            stored = repo.load(project_id)
            if stored and stored.project_id.value == project_id.value:
                project.manifest = stored.manifest
        else:
            # Intentar leer project.json directamente
            manifest_path = paths.project_json_path
            if manifest_path and os.path.exists(manifest_path):
                try:
                    with open(manifest_path) as f:
                        data = json.load(f)
                    proj_data = data.get("project", data)
                    identity = proj_data.get("identity", {})
                    loc = proj_data.get("location", {})
                    project.manifest = ProjectManifest(
                        name=identity.get("name", project_id.value),
                        language=identity.get("language", ""),
                        framework=identity.get("framework", ""),
                        repo_path=loc.get("repo_path", ""),
                        code_paths=list(loc.get("code_paths", []) or []),
                    )
                except (json.JSONDecodeError, OSError):
                    pass

        return project

    @staticmethod
    def from_project_id(project_id: str, chain_dir: str | None = None) -> Project:
        """Construye un Project desde un project_id.

        Args:
            project_id: Identificador del proyecto.
            chain_dir: Chain_dir opcional (para resolver paths especificos).

        Returns:
            Project con paths resueltos.

        Raises:
            ProjectNotFoundError: si el proyecto no existe.
        """
        from libs.path_resolver import get_project_dir

        pid = ProjectId(project_id)
        proj_dir = str(get_project_dir(project_id))

        # Si hay chain_dir, usarlo para resolver paths
        if chain_dir:
            return ProjectService.from_chain_dir(chain_dir)

        # Sino, construir paths desde el project_dir
        der = domain_services.compute_project_paths(proj_dir)
        paths = ProjectPaths(
            chain_dir="",
            project_root=der["project_root"],
            project_id=project_id,
            data_dir=der["data_dir"],
            delta_dir=der["delta_dir"],
            workflow_dir=der["workflow_dir"],
            traces_dir=der["traces_dir"],
            cpg_path=der["cpg_path"],
            ckg_path=der["ckg_path"],
            project_json_path=der["project_json_path"],
            workflow_json_path=der["workflow_json_path"],
            repo_path="",
            cognitive_dir=str(get_cognitive_dir()),
            walker_root=str(get_walker_root()),
        )

        project = Project(project_id=pid, paths=paths)

        # Cargar perfil (manifest — entity nuevo DDD, ADR-015)
        if os.path.exists(paths.project_json_path):
            try:
                with open(paths.project_json_path) as f:
                    data = json.load(f)
                proj_data = data.get("project", data)
                identity = proj_data.get("identity", {})
                loc = proj_data.get("location", {})
                project.manifest = ProjectManifest(
                    name=identity.get("name", project_id),
                    language=identity.get("language", ""),
                    framework=identity.get("framework", ""),
                    repo_path=loc.get("repo_path", ""),
                    code_paths=list(loc.get("code_paths", []) or []),
                )
            except (json.JSONDecodeError, OSError):
                pass

        return project

    # ── Path resolution (sin cargar el proyecto completo) ─────────────

    @staticmethod
    def resolve_manifest(project_dir: str | os.PathLike) -> dict:
        """Lee el project.json local de un directorio (SSOT del perfil).

        Busca en orden: data/project.json, project.json (legacy), y
        .cognitive/projects/*/data/project.json (estructura descentralizada).
        No depende del registro global. Dict vacío si no existe.
        """
        import json as _json

        base = str(project_dir)
        candidates = [
            os.path.join(base, "data", "project.json"),
            os.path.join(base, "project.json"),
        ]
        # Estructura descentralizada: <base>/.cognitive/projects/<pid>/data/
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
                        return _json.load(f)
                except (_json.JSONDecodeError, OSError):
                    return {}
        return {}

    @staticmethod
    def resolve_paths(chain_dir: str) -> ProjectPaths:
        """Resuelve todas las rutas desde un chain_dir.

        Unico metodo que hace IO de resolucion de paths (via libs.path_resolver).

        Args:
            chain_dir: Directorio del sprint.

        Returns:
            ProjectPaths con todas las rutas resueltas.

        Raises:
            ProjectResolutionError: si no se puede resolver.
        """
        from libs.path_resolver import (
            resolve_project_root,
            resolve_project_id,
            get_data_dir,
            get_delta_dir,
            get_workflow_dir,
            get_cpg_path,
            get_ckg_path,
            get_trace_dir,
            get_cognitive_dir,
            get_walker_root,
        )

        if not chain_dir:
            raise ProjectResolutionError("chain_dir vacio — no se puede resolver paths")

        try:
            project_root = resolve_project_root(chain_dir)
        except FileNotFoundError as e:
            raise ProjectResolutionError(str(e))

        project_id = resolve_project_id(chain_dir)
        pid_str = project_id or os.path.basename(project_root)

        # Construir ProjectPaths — derivar de project_root (resuelto por path
        # real) con fallback al registro global. NO depender solo del registro:
        # falla para proyectos descentralizados (repo_path real ≠ dir cognitivo).
        data_dir = os.path.join(project_root, "data")
        if not os.path.isdir(data_dir):
            try:
                data_dir = str(get_data_dir(pid_str))
            except Exception:
                pass
        delta_dir = str(get_delta_dir(pid_str))
        workflow_dir = str(get_workflow_dir(pid_str))
        trace_dir = str(get_trace_dir(pid_str))
        cpg_path = str(get_cpg_path(pid_str))
        ckg_path = str(get_ckg_path(pid_str))

        # project.json path: data/project.json con fallback a project.json legacy
        project_json = os.path.join(data_dir, "project.json")
        if not os.path.exists(project_json):
            legacy = os.path.join(project_root, "project.json")
            if os.path.exists(legacy):
                project_json = legacy

        # workflow.json path
        workflow_json = os.path.join(workflow_dir, "workflow.json")
        if not os.path.exists(workflow_json):
            legacy_wf = os.path.join(project_root, "workflow.json")
            if os.path.exists(legacy_wf):
                workflow_json = legacy_wf

        # Repo info — leer del project_json ya resuelto (SSOT local).
        # NO depender del registro global por id: falla para proyectos
        # descentralizados (repo_path real ≠ dir cognitivo).
        repo_path = ""
        code_paths: tuple[str, ...] = ()
        if project_json and os.path.exists(project_json):
            try:
                with open(project_json) as _f:
                    _manifest = json.load(_f)
                _proj = _manifest.get("project", _manifest)
                _loc = _proj.get("location", {})
                repo_path = _loc.get("repo_path", "")
                code_paths = tuple(_loc.get("code_paths", []) or [])
            except (json.JSONDecodeError, OSError):
                pass

        # WALKER / COGNITIVE ROOTS (no fallan)
        cognitive_dir = ""
        walker_root = ""
        try:
            cognitive_dir = str(get_cognitive_dir())
        except Exception:
            pass
        try:
            walker_root = str(get_walker_root())
        except Exception:
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
            code_paths=code_paths,
            cognitive_dir=cognitive_dir,
            walker_root=walker_root,
        )

    # ── Discovery ─────────────────────────────────────────────────────

    @staticmethod
    def find_cognitive_root(start: str | None = None) -> str | None:
        """Busca el directorio .cognitive/ caminando hacia arriba desde start.

        Args:
            start: Directorio donde empezar (default: CWD).

        Returns:
            Path absoluto a .cognitive/ o None si no se encuentra.
        """
        from libs.path_resolver import find_cognitive_root as _find

        result = _find(start)
        return str(result) if result else None

    @staticmethod
    def _verify_registration(repo_path: str) -> bool:
        """Verifica que un proyecto registrado sea valido: path existe y tiene .cognitive/."""
        if not repo_path:
            return False
        cog_dir = os.path.join(repo_path, ".cognitive")
        return os.path.isdir(repo_path) and os.path.isdir(cog_dir)

    @staticmethod
    def _load_registry() -> dict[str, dict[str, Any]]:
        """Carga el registro global de proyectos desde data/projects.json."""
        try:
            from libs.path_resolver import get_walker_root

            registry_path = os.path.join(
                str(get_walker_root()), "data", "projects.json"
            )
        except Exception:
            registry_path = os.path.join(
                os.path.dirname(
                    os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
                ),
                "data",
                "projects.json",
            )
        if os.path.exists(registry_path):
            try:
                with open(registry_path) as f:
                    data = json.load(f)
                return data.get("projects", {})
            except Exception:
                pass
        return {}

    @staticmethod
    def list_available(
        spring_service=None,
    ) -> list[dict[str, Any]]:
        """Lista todos los proyectos registrados y verificados.

        Solo incluye proyectos que cumplan TRES condiciones:
          1. Existen en el registro global (data/projects.json)
          2. Su repo_path existe en disco
          3. Tienen directorio .cognitive/ en su repo_path

        Args:
            spring_service: Optional SpringService for DI (lazy import if None).

        Returns:
            Lista de dicts con: project_id, name, language, active_sprints, status.
        """
        registry = ProjectService._load_registry()
        if not registry:
            return []

        result: list[dict[str, Any]] = []
        for pid, reg_entry in sorted(registry.items()):
            repo_path = reg_entry.get("repo_path", "") or ""

            # ── Filtro: repo_path existe y tiene .cognitive/ ──
            if not ProjectService._verify_registration(repo_path):
                continue

            # ── Leer metadata desde project.json ──
            cog_dir = os.path.join(repo_path, ".cognitive")
            projects_dir = Path(cog_dir) / "projects" / pid
            manifest_path = projects_dir / "data" / "project.json"
            if not manifest_path.exists():
                manifest_path = projects_dir / "project.json"

            name = reg_entry.get("name", pid)
            language = ""
            framework = ""
            if manifest_path.exists():
                try:
                    with open(manifest_path) as f:
                        manifest_data = json.load(f)
                    proj = manifest_data.get("project", manifest_data)
                    identity = proj.get("identity", {})
                    name = identity.get("name", name)
                    language = identity.get("language", "")
                    framework = identity.get("framework", "")
                except (json.JSONDecodeError, OSError):
                    pass

            # ── Detectar sprints activos (via delta) ──
            active_sprints: list[str] = []
            try:
                svc = spring_service
                if svc is None:
                    from features.delta_tracking import SpringService, spring_service
                    from libs.path_resolver import get_project_dir

                    project_root = str(get_project_dir(pid))
                    if os.path.isdir(project_root):
                        svc = SpringService(project_root)
                if svc:
                    for spring in svc.list_springs():
                        st = spring.get("status", "")
                        if st in ("active", "in_progress", "planning"):
                            active_sprints.append(spring.get("_id", ""))
            except Exception:
                pass

            result.append(
                {
                    "project_id": pid,
                    "name": name,
                    "language": language,
                    "framework": framework,
                    "repo_path": repo_path,
                    "verified": True,
                    "active_sprints": active_sprints,
                    "status": "active" if active_sprints else "inactive",
                }
            )

        return result

    # ── Sprint discovery ──────────────────────────────────────────────

    @staticmethod
    def find_active_sprint(
        project_id: str,
        spring_service=None,
    ) -> str | None:
        """Encuentra el sprint activo de un proyecto.

        FUENTE UNICA: consulta el DeltaGraph (nodo spring con status active).
        plan.json/progress.json fueron eliminados (ADR-WALKER-014).

        Args:
            project_id: Project identifier.
            spring_service: Optional SpringService for DI (lazy import if None).

        Returns:
            Chain_dir del sprint activo, o None si no hay.
        """
        try:
            svc = spring_service
            if svc is None:
                from features.delta_tracking import SpringService, spring_service
                from libs.path_resolver import get_project_dir

                project_root = str(get_project_dir(project_id))
                if os.path.isdir(project_root):
                    svc = SpringService(project_root)
            if svc:
                active = svc.get_active_spring()
                if active:
                    spring_id = active.get("_id", "")
                    if spring_id:
                        from libs.path_resolver import get_sprint_dir

                        sprint_dir = str(get_sprint_dir(project_id, spring_id))
                        if os.path.isdir(sprint_dir):
                            return sprint_dir
        except Exception:
            pass
        return None

    @staticmethod
    def resolve_from_registry(start: str | None = None) -> dict | None:
        """Resolve current project from global registry using CWD.

        Reads data/projects.json (walker root) and finds the project
        whose repo_path contains the current working directory.

        FALLBACK: If not found in registry, looks for .cognitive/ at CWD.
        If found, migrates project.json to data/ (canonical) if needed,
        auto-registers in data/projects.json, and returns the project.

        Returns:
            Dict with project_id, repo_path, name if found, else None.
        """
        from libs.path_resolver import get_walker_root

        registry_path = get_walker_root() / "data" / "projects.json"
        registry: dict = {"projects": {}}
        if registry_path.exists():
            try:
                with open(registry_path) as f:
                    registry = json.load(f)
            except (json.JSONDecodeError, OSError):
                registry = {"projects": {}}

        cwd = os.path.abspath(start or os.getcwd())
        projects = registry.get("projects", {})

        # 1. Try registry match first
        for pid, info in projects.items():
            repo = info.get("repo_path", "")
            if repo:
                try:
                    if os.path.samefile(cwd, repo):
                        return {
                            "project_id": pid,
                            "repo_path": repo,
                            "name": info.get("name", pid),
                        }
                except OSError:
                    pass
                if cwd.startswith(repo):
                    return {
                        "project_id": pid,
                        "repo_path": repo,
                        "name": info.get("name", pid),
                    }

        # 2. Fallback: detect .cognitive/ at CWD, auto-register
        cognitive = os.path.join(cwd, ".cognitive")
        if not os.path.isdir(cognitive):
            return None

        # Find project inside .cognitive/projects/
        projects_dir = os.path.join(cognitive, "projects")
        if not os.path.isdir(projects_dir):
            return None

        pids = sorted(os.listdir(projects_dir))
        if not pids:
            return None

        pid = pids[0]
        proj_dir = os.path.join(projects_dir, pid)

        # Find project.json (canonical first: data/project.json, legacy: project.json)
        project_json_data = os.path.join(proj_dir, "data", "project.json")
        project_json_legacy = os.path.join(proj_dir, "project.json")
        project_json = (
            project_json_data
            if os.path.exists(project_json_data)
            else project_json_legacy
        )

        # Detect legacy location and warn
        legacy_warning = ""
        if os.path.exists(project_json_legacy) and not os.path.exists(
            project_json_data
        ):
            legacy_warning = (
                f"⚠️  Project {pid}: project.json is in legacy location.\n"
                f"   Move it to: .cognitive/projects/{pid}/data/project.json"
            )
        elif os.path.exists(project_json_legacy) and os.path.exists(project_json_data):
            # Both exist — legacy is stale
            legacy_warning = (
                f"⚠️  Project {pid}: legacy project.json found outside data/.\n"
                f"   Using data/project.json. Legacy file can be removed."
            )

        if not os.path.exists(project_json):
            return None

        # Read project info
        try:
            with open(project_json) as f:
                proj_data = json.load(f)
            project_info = proj_data.get("project", proj_data)
            identity = project_info.get("identity", {})
            location = project_info.get("location", {})
            repo_path = location.get("repo_path", cwd)
            name = identity.get("name", pid)
        except Exception:
            repo_path = cwd
            name = pid

        # Migrate project.json to data/ if it's in legacy location
        if project_json == project_json_legacy and not os.path.exists(
            project_json_data
        ):
            try:
                os.makedirs(os.path.join(proj_dir, "data"), exist_ok=True)
                with open(project_json) as f_src:
                    content = f_src.read()
                with open(project_json_data, "w") as f_dst:
                    f_dst.write(content)
                # Keep legacy file for backward compat
            except Exception:
                pass

        # Auto-register in global registry
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        projects[pid] = {
            "project_id": pid,
            "name": name,
            "repo_path": repo_path,
            "active": True,
            "registered_at": now,
            "last_accessed": now,
        }
        registry["projects"] = projects
        registry["updated_at"] = now
        try:
            os.makedirs(os.path.dirname(str(registry_path)), exist_ok=True)
            with open(registry_path, "w") as f:
                json.dump(registry, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

        result = {"project_id": pid, "repo_path": repo_path, "name": name}
        if legacy_warning:
            result["_warning"] = legacy_warning
        return result

    @staticmethod
    def resolve_chain_dir(
        project_id: str | None = None,
        start: str | None = None,
        spring_service=None,
    ) -> str | None:
        """Resuelve un chain_dir desde un project_id o directorio de trabajo.

        Si no se especifica project_id, busca el proyecto activo desde CWD.

        Args:
            project_id: Optional project identifier.
            start: Optional starting directory.
            spring_service: Optional SpringService for DI (lazy import if None).

        Returns:
            Chain_dir del sprint activo, o None si no se encuentra.
        """
        if project_id:
            return ProjectService.find_active_sprint(project_id)

        # Buscar .cognitive/ desde CWD
        cognitive = ProjectService.find_cognitive_root(start)
        if cognitive:
            cwd = os.path.abspath(start or os.getcwd())
            if os.path.exists(os.path.join(cwd, ".workflow-state.json")):
                return cwd
            try:
                svc = spring_service
                if svc is None:
                    pass
                if svc and svc.from_chain_dir(cwd) is not None:
                    svc2 = svc.from_chain_dir(cwd)
                    if svc2 and svc2.get_spring(svc2.get_spring_id(cwd)) is not None:
                        return cwd
            except Exception:
                pass
            # Buscar proyecto activo en el cognitive root
            projects = ProjectService.list_available(spring_service=spring_service)
            for p in projects:
                if p.get("status") == "active" and p.get("active_sprints"):
                    chain = ProjectService.find_active_sprint(
                        p["project_id"], spring_service=spring_service
                    )
                    if chain:
                        return chain
        return None

    # ── Creacion ──────────────────────────────────────────────────────

    @staticmethod
    def create_project(
        project_id: str,
        repo_path: str,
        name: str = "",
        language: str = "",
        framework: str = "",
        repo: ProjectRepository | None = None,
    ) -> Project:
        """Crea un nuevo proyecto con su project.json y lo registra.

        Args:
            project_id: Identificador unico.
            repo_path: Ruta absoluta al repositorio de codigo.
            name: Nombre descriptivo (default: project_id).
            language: Lenguaje principal.
            framework: Framework principal.
            repo: Repositorio opcional para persistir.

        Returns:
            Project creado.

        Raises:
            ValueError: Si project_id es invalido.
        """
        if not domain_services.validate_project_id(project_id):
            raise ValueError(
                f"project_id invalido: '{project_id}'. "
                "Usa solo letras, numeros, guiones y underscores."
            )

        from libs.path_resolver import get_project_dir, get_data_dir

        pid = ProjectId(project_id)
        name = name or project_id

        # Construir paths
        proj_dir = str(get_project_dir(project_id))
        data_dir = str(get_data_dir(project_id))

        paths = ProjectPaths(
            chain_dir="",
            project_root=proj_dir,
            project_id=project_id,
            data_dir=data_dir,
            delta_dir=str(get_data_dir(project_id) / "spring"),
            workflow_dir=str(get_project_dir(project_id) / "workflow.v3"),
            traces_dir=os.path.join(data_dir, "traces"),
            cpg_path=os.path.join(data_dir, "cpg.json.gz"),
            ckg_path=os.path.join(data_dir, "ckg.json.gz"),
            project_json_path=os.path.join(data_dir, "project.json"),
            workflow_json_path=os.path.join(proj_dir, "workflow.v3", "workflow.json"),
            repo_path=os.path.abspath(repo_path) if repo_path else "",
            cognitive_dir="",
            walker_root="",
        )

        project = Project(
            project_id=pid,
            manifest=ProjectManifest(
                name=name,
                language=language,
                framework=framework,
                repo_path=paths.repo_path,
                code_paths=list(paths.code_paths),
            ),
            paths=paths,
        )

        if repo:
            repo.save(project)

        return project

    # ── Proxies to use cases (single public API) ──────────────────────

    @staticmethod
    def detect_context(project_path: str) -> dict:
        """Detect project context: languages, frameworks, git, code paths."""
        from .use_cases import DetectProjectContextUseCase

        return DetectProjectContextUseCase.execute(project_path)

    @staticmethod
    def detect_legacy(project_path: str) -> dict:
        """Detect legacy walker project structure."""
        from .use_cases import DetectLegacyUseCase

        return DetectLegacyUseCase.execute(project_path)

    @staticmethod
    def update_project(chain_dir: str, data: dict) -> dict:
        """Update project.json with schema validation."""
        from .use_cases import UpdateProjectUseCase

        return UpdateProjectUseCase.execute(chain_dir, data)

    @staticmethod
    def import_legacy_history(
        project_path: str, project_id: str, chains: list[str]
    ) -> dict:
        """Import legacy chain history to trace files."""
        from .use_cases import ImportLegacyHistoryUseCase

        return ImportLegacyHistoryUseCase.execute(project_path, project_id, chains)


# ── Convenience re-exports ──────────────────────────────────────────────


def get_cognitive_dir() -> Path:
    """Re-export desde libs.path_resolver para conveniencia."""
    from libs.path_resolver import get_cognitive_dir as _get

    return _get()


def get_walker_root() -> Path:
    """Re-export desde libs.path_resolver para conveniencia."""
    from libs.path_resolver import get_walker_root as _get

    return _get()
