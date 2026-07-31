"""project_registry.py — Project registry and profile management.

SSOT for ProjectRegistry and ProjectProfile, consolidated from
scripts/projects.py and scripts/_internal/projects.py.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any


# ── Constants ───────────────────────────────────────────────────────────────

_COGNITIVE_DIR = ".cognitive"
_PROJECTS_DIR = "projects"
_PROJECTS_REGISTRY = "projects.json"
_PROJECT_FILE = "project.json"
_DELTA_DIR = "delta"
_CURRENT_JSON = "current.json"
_BACKLOG_FILE = "backlog.jsonl"
_SCHEMA_VERSION = "1.0"

COGNITIVE_DIR = _COGNITIVE_DIR
PROJECTS_DIR = _PROJECTS_DIR
DELTA_DIR = _DELTA_DIR
PROJECTS_REGISTRY = _PROJECTS_REGISTRY
BACKLOG_FILE = _BACKLOG_FILE
PROJECT_FILE = _PROJECT_FILE
CURRENT_JSON = _CURRENT_JSON


# ── Helpers ─────────────────────────────────────────────────────────────────


def find_cognitive_root(start: str | None = None) -> str | None:
    """Walk up from start to find .cognitive/ directory."""
    cwd = start or os.getcwd()
    cwd = os.path.abspath(cwd)
    for _ in range(10):
        if os.path.isdir(os.path.join(cwd, ".cognitive")):
            return cwd
        parent = os.path.dirname(cwd)
        if parent == cwd:
            break
        cwd = parent
    return None


def _ensure_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


def _read_json(path: str) -> dict | None:
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _write_json(path: str, data: dict) -> None:
    _ensure_dir(os.path.dirname(path))
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _get_pr() -> Any | None:
    """Lazy import of path_resolver to avoid circular dependencies."""
    try:
        from _walker.shared import path_resolver as pr
        return pr
    except ImportError:
        return None


# ═════════════════════════════════════════════════════════════════════════════
# ProjectRegistry — global projects.json
# ═════════════════════════════════════════════════════════════════════════════


class ProjectRegistry:
    """Manages the global registry of projects (projects.json)."""

    def __init__(self, cognitive_root: str | None = None):
        self._root = cognitive_root or find_cognitive_root()
        if not self._root:
            raise FileNotFoundError(
                "No se encontró .cognitive/. "
                "Ejecuta este script desde el directorio del proyecto "
                "o pasa --cognitive-root explícitamente."
            )
        pr = _get_pr()
        if pr:
            self._registry_path = str(pr.get_projects_dir() / _PROJECTS_REGISTRY)
            self._projects_dir = str(pr.get_projects_dir())
        else:
            self._registry_path = os.path.join(
                self._root, _COGNITIVE_DIR, _PROJECTS_REGISTRY
            )
            self._projects_dir = os.path.join(self._root, _COGNITIVE_DIR, _PROJECTS_DIR)

    def _load_registry(self) -> dict:
        data = _read_json(self._registry_path)
        if data is None:
            return {"projects": {}, "created_at": _now_iso(), "updated_at": _now_iso()}
        return data

    def _save_registry(self, data: dict) -> None:
        data["updated_at"] = _now_iso()
        _write_json(self._registry_path, data)

    def list_projects(self) -> dict[str, dict]:
        return self._load_registry().get("projects", {})

    def get_project(self, project_id: str) -> dict | None:
        return self.list_projects().get(project_id)

    def register_project(
        self,
        project_id: str,
        repo_path: str,
        name: str = "",
        cognitive_subdir: str = "",
        code_paths: list[str] | None = None,
    ) -> dict:
        registry = self._load_registry()
        projects = registry.setdefault("projects", {})
        now = _now_iso()
        entry = projects.get(project_id, {})
        entry.update(
            {
                "name": name or project_id,
                "repo_path": os.path.abspath(repo_path),
                "cognitive_subdir": cognitive_subdir,
                "active": entry.get("active", True),
                "registered_at": entry.get("registered_at", now),
                "last_accessed": now,
            }
        )
        if code_paths:
            entry["code_paths"] = code_paths
        projects[project_id] = entry
        self._save_registry(registry)

        pr = _get_pr()
        if pr:
            proj_dir = str(pr.get_project_dir(project_id))
            delta_dir = str(pr.get_delta_dir(project_id))
        else:
            proj_dir = os.path.join(self._projects_dir, project_id)
            delta_dir = os.path.join(proj_dir, _DELTA_DIR)
        _ensure_dir(delta_dir)

        proj_file = os.path.join(proj_dir, _PROJECT_FILE)
        if not os.path.exists(proj_file):
            profile = ProjectProfile(cognitive_root=self._root)
            profile.create_default(
                project_id=project_id,
                name=name or project_id,
                repo_path=repo_path,
            )
            profile.save()
        return entry

    def unregister_project(self, project_id: str) -> bool:
        registry = self._load_registry()
        projects = registry.get("projects", {})
        if project_id not in projects:
            return False
        del projects[project_id]
        self._save_registry(registry)
        return True

    def project_dir(self, project_id: str) -> str:
        pr = _get_pr()
        if pr:
            return str(pr.get_project_dir(project_id))
        return os.path.join(self._projects_dir, project_id)

    def delta_dir(self, project_id: str) -> str:
        pr = _get_pr()
        if pr:
            return str(pr.get_delta_dir(project_id))
        return os.path.join(self.project_dir(project_id), _DELTA_DIR)

    def current_json_path(self, project_id: str) -> str:
        return os.path.join(self.delta_dir(project_id), _CURRENT_JSON)

    def backlog_path(self, project_id: str) -> str:
        return os.path.join(self.delta_dir(project_id), _BACKLOG_FILE)


# ═════════════════════════════════════════════════════════════════════════════
# ProjectProfile — per-project project.json
# ═════════════════════════════════════════════════════════════════════════════


class ProjectProfile:
    """Reads and writes project.json for a single project."""

    def __init__(self, cognitive_root: str | None = None):
        self._root = cognitive_root or find_cognitive_root()
        if not self._root:
            raise FileNotFoundError("No se encontró .cognitive/")
        pr = _get_pr()
        if pr:
            self._projects_dir = str(pr.get_projects_dir())
        else:
            self._projects_dir = os.path.join(self._root, _COGNITIVE_DIR, _PROJECTS_DIR)
        self._project_id: str = ""
        self._data: dict | None = None

    def _path(self, project_id: str) -> str:
        pr = _get_pr()
        if pr:
            return str(pr.get_manifest_path(project_id))
        return os.path.join(self._projects_dir, project_id, _PROJECT_FILE)

    def load(self, project_id: str) -> dict | None:
        self._project_id = project_id
        data = _read_json(self._path(project_id))
        if data is None:
            return None
        self._data = data
        return data.get("project", data)

    def get(self) -> dict | None:
        if self._data is None:
            return None
        return self._data.get("project", self._data)

    def save(self) -> bool:
        if self._data is None or not self._project_id:
            return False
        if "project" in self._data:
            self._data["project"]["meta"]["updated_at"] = _now_iso()
        _write_json(self._path(self._project_id), self._data)
        return True

    def create_default(
        self,
        project_id: str,
        name: str = "",
        repo_path: str = "",
        language: str = "",
        framework: str = "",
    ) -> dict:
        if not project_id or not project_id.strip():
            raise ValueError("project_id es obligatorio y no puede estar vacío")
        if not repo_path or not repo_path.strip():
            raise ValueError("repo_path es obligatorio y no puede estar vacío")
        if not language or not language.strip():
            raise ValueError("language es obligatorio y no puede estar vacío")
        if not framework or not framework.strip():
            raise ValueError("framework es obligatorio y no puede estar vacío")
        now = _now_iso()
        data = {
            "project": {
                "meta": {
                    "project_id": project_id,
                    "version": _SCHEMA_VERSION,
                    "created_at": now,
                    "updated_at": now,
                },
                "identity": {
                    "name": name or project_id,
                    "description": "",
                    "language": language,
                    "framework": framework,
                    "tags": [],
                },
                "location": {
                    "repo_path": os.path.abspath(repo_path) if repo_path else "",
                    "cognitive_subdir": "",
                    "code_paths": [],
                },
                "design": {
                    "patterns": [],
                    "layer_rules": {},
                    "bounded_contexts": [],
                    "port_adapter_required": False,
                    "forbidden_dependencies": [],
                    "decisions": [],
                },
                "architecture": {
                    "project_structure": "",
                    "module_organization": "",
                    "test_strategy": {},
                    "dependency_injection": "",
                    "error_handling": "",
                    "config_management": "",
                },
                "coding": {
                    "language": language or "",
                    "naming_convention": "",
                    "formatter": "",
                    "linter": "",
                    "type_checker": "",
                    "test_framework": "",
                    "import_style": "",
                    "docstring_style": "",
                    "max_line_length": 100,
                    "git_workflow": {},
                },
                "quality_preferences": {
                    "cyclo_max": 60,
                    "lcom_max": 20,
                    "ce_max": 10,
                    "cc_per_func_max": 15,
                    "dup_blocks_max": 2,
                    "security_high_max": 0,
                    "hallucinated_imports_max": 0,
                    "coverage_min": None,
                },
                "active_goal": {},
                "goals_history": [],
                "team": {
                    "owners": [],
                    "bounded_contexts": [],
                },
                "dependencies": {
                    "internal_modules": [],
                    "external_services": [],
                    "related_projects": [],
                },
                "detected_profile": {},
            }
        }
        self._project_id = project_id
        self._data = data
        return data

    def set_field(self, category: str, field: str, value: Any) -> bool:
        if self._data is None:
            return False
        proj = self._data.get("project", self._data)
        if category in proj and field in proj[category]:
            proj[category][field] = value
            return True
        return False

    def set_active_goal(self, description: str, chain_id: str) -> bool:
        if self._data is None:
            return False
        proj = self._data.get("project", self._data)
        proj["active_goal"] = {
            "description": description,
            "chain_id": chain_id,
            "set_at": _now_iso(),
        }
        return True

    def close_goal(self, outcome: str = "completed") -> bool:
        if self._data is None:
            return False
        proj = self._data.get("project", self._data)
        active = proj.get("active_goal", {})
        if active and active.get("chain_id"):
            history = proj.setdefault("goals_history", [])
            history.append({
                "chain_id": active["chain_id"],
                "description": active.get("description", ""),
                "closed_at": _now_iso(),
                "outcome": outcome,
            })
            proj["active_goal"] = {}
        return True

    def set_quality_threshold(self, **kwargs) -> bool:
        if self._data is None:
            return False
        proj = self._data.get("project", self._data)
        qp = proj.setdefault("quality_preferences", {})
        for k, v in kwargs.items():
            if k in qp or True:
                qp[k] = v
        return True

    def to_dict(self) -> dict:
        return self._data or {}

    @staticmethod
    def resolve_active_project(cognitive_root: str | None = None) -> str | None:
        root = cognitive_root or find_cognitive_root()
        if not root:
            return None
        registry = ProjectRegistry(root)
        cwd = os.path.abspath(os.getcwd())
        for pid, info in registry.list_projects().items():
            repo = info.get("repo_path", "")
            if repo and cwd.startswith(repo):
                return pid
        return None
