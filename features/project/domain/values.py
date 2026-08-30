"""
domain/values.py — Value Objects for the Project domain.

Pure, immutable, validated on construction.
Uses return pattern: each factory method returns (value, errors) tuple.

DISTINCIÓN DE CONCEPTOS (features/project):
  - Value Objects (ProjectId, ProjectPaths, ProjectManifest) = SOBRE LA
    ESTRUCTURA Y UBICACIÓN del proyecto (rutas calculadas, identidad).
  - ProjectPaths es el UTILITARIO de resolución de rutas (chain_dir →
    project_root, data_dir, ...). NO es un archivo ni un índice persistido.
  - El manifiesto de UN proyecto es .cognitive/{pid}/data/project.json.
  - El registro GLOBAL de TODOS los proyectos es data/projects.json
    (en la raíz del walker, gestionado por ProjectService).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass


# ── Validation patterns ──

_PROJECT_ID_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")


def _validate_project_id(value: str) -> list[str]:
    errors: list[str] = []
    if not value or not str(value).strip():
        errors.append("project_id cannot be empty")
    elif not _PROJECT_ID_RE.match(value):
        errors.append(f"Invalid project_id '{value}': must match [a-zA-Z0-9_-]+")
    return errors


# ═══════════════════════════════════════════════════════════════════════
# Value Objects
# ═══════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class ProjectId:
    """VO — immutable project identifier, validated.

    Use the factory `ProjectId.create(value)` for safe construction
    with return pattern. Direct construction skips validation.
    """

    value: str

    def __str__(self) -> str:
        return self.value

    @staticmethod
    def create(value: str) -> tuple["ProjectId | None", list[str]]:
        """Factory with validation. Returns (ProjectId, errors)."""
        errors = _validate_project_id(value)
        if errors:
            return None, errors
        return ProjectId(value), []

    @staticmethod
    def from_chain_dir(chain_dir: str) -> tuple["ProjectId | None", list[str]]:
        """Derive project_id from chain_dir path structure.

        Pure: receives path, parses it. NO IO.
        """
        if not chain_dir:
            return None, ["chain_dir is empty"]
        # Walk up from chain_dir to find the project dir
        parts = os.path.normpath(chain_dir).split(os.sep)
        # Look for 'projects' marker in path
        try:
            proj_idx = parts.index("projects")
            pid = parts[proj_idx + 1]
            return ProjectId.create(pid)
        except (ValueError, IndexError):
            return None, [f"Cannot derive project_id from '{chain_dir}'"]


def derive_project_id(chain_dir: str) -> tuple[str, list[str]]:
    """Pure function: extract project_id from chain_dir path.
    Returns (project_id, errors).
    """
    pid, errors = ProjectId.from_chain_dir(chain_dir)
    if errors:
        return "", errors
    return pid.value if pid else "", []


@dataclass(frozen=True)
class ProjectPaths:
    """VO — immutable computed paths for a project.

    UTILITARIO DE RESOLUCIÓN DE RUTAS (no es un archivo ni un índice):
    calcula y agrupa las rutas de un proyecto a partir de chain_dir.
    Computed eagerly at construction. NO IO.
    All paths are absolute strings.

    DISTINCIÓN (features/project):
      - ProjectPaths       = este utilitario (rutas calculadas en memoria).
      - Manifest           = <repo>/.cognitive/projects/{pid}/data/project.json
                             (identidad del proyecto; lo lee
                             CognitiveProjectRepository).
      - Registry global    = data/projects.json en la raíz del walker (índice
                             de TODOS los proyectos que el walker administra,
                             incluido él mismo; lo gestiona ProjectService).

    CONVENCIÓN DE LAYOUT (SSOT — ver también el docstring de
    shared/libs/path_resolver, Jerarquía de resolución):

      Capa 1 — DENTRO del repo de cada proyecto, creada por `walker prepare`
      (análogo a `git init`). TODO el espacio cognitivo del proyecto vive
      aquí: manifiesto + memorias + estado del watcher.

        <repo_path>/.cognitive/projects/<project_id>/data/
            project.json          ← manifiesto (identidad + location)
            ckg.json.gz           ← memoria semántica (CKG)
            trace.json.gz         ← trazabilidad (TraceGraph)
            cpg.json.gz           ← grafo de código (CPG)
            change_detected.json  ← estado del watcher
            .file_snapshot.json   ← baseline del watcher

      Capa 2 — RAÍZ del walker (el administrador):
        <walker_root>/data/projects.json ← REGISTRO GLOBAL: solo referencias
        (project_id → repo_path). No contiene memorias ni manifiesto.

      CLAVE: el registro global SOLO RESUELVE rutas; la identidad y las
      memorias de cada proyecto viven en su propio .cognitive/. Si se
      prepara un proyecto React "pepe", se crea
      <pepe>/.cognitive/projects/pepe/data/ (su manifiesto + memorias) y
      solo se añade pepe → <pepe> al registro global del walker.

    Attributes:
        chain_dir: Sprint directory (or "" if bootstrap).
        project_root: Project root (<repo>/.cognitive/projects/<pid>/).
        project_id: Short project identifier.
        data_dir: data/ directory (memorias del proyecto).
        delta_dir: Sprite directory (delta/ or data/spring/).
        workflow_dir: Workflow definitions.
        traces_dir: Trace storage.
        cpg_path: CPG data file.
        ckg_path: CKG data file.
        project_json_path: project.json (manifest).
        workflow_json_path: workflow.json.
        repo_path: Source code repo root.
        cognitive_dir: .cognitive/ dir for active project.
        walker_root: Walker installation root.
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
    project_json_path: str = ""
    workflow_json_path: str = ""
    repo_path: str = ""
    cognitive_dir: str = ""
    walker_root: str = ""

    @property
    def sprint_id(self) -> str:
        if not self.chain_dir:
            return ""
        return os.path.basename(self.chain_dir)


@dataclass(frozen=True)
class ProjectManifest:
    """VO — identity metadata from project.json.

    Immutable snapshot of a project's manifest data.
    """

    name: str = ""
    language: str = ""
    framework: str = ""
    repo_path: str = ""
    description: str = ""

    @staticmethod
    def create(data: dict) -> tuple["ProjectManifest", list[str]]:
        """Create from project.json dict. Returns (manifest, errors)."""
        errors: list[str] = []
        identity = data.get("project", data).get("identity", {})
        name = identity.get("name", data.get("project", data).get("name", ""))
        language = identity.get("language", data.get("project", data).get("language", ""))
        framework = identity.get("framework", data.get("project", data).get("framework", ""))
        repo_path = data.get("project", data).get("repo_path", "") or ""
        description = data.get("project", data).get("description", "") or ""

        if not name:
            errors.append("manifest.name is required")

        return ProjectManifest(
            name=name,
            language=language,
            framework=framework,
            repo_path=repo_path,
            description=description,
        ), errors
