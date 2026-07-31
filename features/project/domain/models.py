"""
domain/models.py — Project domain entities and value objects.

SSOT for all project-related domain concepts.
Every other module MUST use this module instead of implementing
its own path resolution or project data structures.

Principios:
  - Value Objects: inmutables, sin identidad, comparables por valor
  - Entities: con identidad (project_id), mutables solo via repository
  - ProjectPaths: computed eagerly en construccion, nunca hace IO
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any


# ═══════════════════════════════════════════════════════════════════════
# Value Objects
# ═══════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class ProjectId:
    """Value Object — identificador de un proyecto.

    Inmutable, comparable por valor.
    El project_id es el nombre del directorio del proyecto
    dentro de .cognitive/projects/<project_id>/.
    """
    value: str

    def __str__(self) -> str:
        return self.value

    @classmethod
    def from_chain_dir(cls, chain_dir: str) -> ProjectId:
        """Deriva project_id desde chain_dir via path_resolver."""
        from libs.path_resolver import resolve_project_id
        return cls(resolve_project_id(chain_dir))


@dataclass(frozen=True)
class SprintId:
    """Value Object — identificador de un sprint/chain.

    Inmutable, comparable por valor.
    El sprint_id es el nombre del directorio del sprint
    dentro de .cognitive/projects/<pid>/<sprint_dir>/<sprint_id>/.
    """
    value: str

    def __str__(self) -> str:
        return self.value

    @classmethod
    def from_chain_dir(cls, chain_dir: str) -> SprintId:
        return cls(os.path.basename(chain_dir))


@dataclass(frozen=True)
class ProjectPaths:
    """Value Object — rutas resueltas de un proyecto.

    Inmutable. Se construye una vez (en el application service)
    y nunca cambia. Todas las rutas son absolutas.

    NO hace IO — los paths se computan en el constructor
    usando libs.path_resolver (que si hace IO para descubrir rutas).

    Attributes:
        chain_dir: Directorio del sprint activo (o vacio si bootstrap).
        project_root: Directorio raiz del proyecto (.cognitive/projects/<pid>/).
        project_id: Nombre corto del proyecto.
        data_dir: Directorio data/ del proyecto.
        delta_dir: Directorio de sprints (delta/ o data/spring/).
        workflow_dir: Directorio de definiciones de workflow.
        traces_dir: Directorio de traces.
        cpg_path: Ruta a cpg.json.gz.
        ckg_path: Ruta a ckg.json.gz.
        project_json_path: Ruta a project.json (manifest).
        workflow_json_path: Ruta a workflow.json.
        repo_path: Ruta al repositorio de codigo fuente (desde project.json).
        code_paths: Subdirectorios de codigo fuente dentro del repo.
        cognitive_dir: Ruta a .cognitive/ del proyecto activo.
        walker_root: Ruta raiz del walker.
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
    code_paths: tuple[str, ...] = field(default_factory=tuple)
    cognitive_dir: str = ""
    walker_root: str = ""

    @property
    def sprint_id(self) -> str:
        """Deriva sprint_id del chain_dir si existe."""
        if not self.chain_dir:
            return ""
        return os.path.basename(self.chain_dir)

    @property
    def sprint_state_path(self) -> str:
        """Ruta a sprint-state.json del sprint activo."""
        if not self.chain_dir:
            return ""
        return os.path.join(self.chain_dir, "sprint-state.json")

    @property
    def plan_json_path(self) -> str:
        """Ruta a plan.json del sprint activo."""
        if not self.chain_dir:
            return ""
        return os.path.join(self.chain_dir, "plan.json")

    def sprint_file(self, filename: str) -> str:
        """Ruta a un archivo dentro del directorio del sprint."""
        if not self.chain_dir:
            return ""
        return os.path.join(self.chain_dir, filename)

    def data_file(self, filename: str) -> str:
        """Ruta a un archivo dentro de data/ del proyecto."""
        return os.path.join(self.data_dir, filename)

    def to_dict(self) -> dict[str, str | tuple[str, ...]]:
        """Serializa paths a dict plano para debugging/logging."""
        return {
            "chain_dir": self.chain_dir,
            "project_root": self.project_root,
            "project_id": self.project_id,
            "data_dir": self.data_dir,
            "delta_dir": self.delta_dir,
            "workflow_dir": self.workflow_dir,
            "traces_dir": self.traces_dir,
            "cpg_path": self.cpg_path,
            "ckg_path": self.ckg_path,
            "project_json_path": self.project_json_path,
            "workflow_json_path": self.workflow_json_path,
            "repo_path": self.repo_path,
            "code_paths": self.code_paths,
            "cognitive_dir": self.cognitive_dir,
            "walker_root": self.walker_root,
        }


# ═══════════════════════════════════════════════════════════════════════
# Entity: Project
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class Project:
    """Entity — un proyecto con su identidad, perfil y rutas.

    project_id es la identidad. Dos Projects con el mismo project_id
    son el mismo proyecto aunque tengan datos diferentes.

    Attributes:
        project_id: Identificador unico del proyecto.
        name: Nombre descriptivo.
        language: Lenguaje principal (python, typescript, etc.).
        framework: Framework principal.
        repo_path: Ruta absoluta al repositorio de codigo.
        code_paths: Subdirectorios con codigo fuente.
        paths: Rutas resueltas del proyecto (VO inmutable).
        active_sprint_id: Sprint activo actual (o vacio si none).
    """
    project_id: ProjectId
    name: str = ""
    language: str = ""
    framework: str = ""
    repo_path: str = ""
    code_paths: list[str] = field(default_factory=list)
    paths: ProjectPaths | None = None
    active_sprint_id: str = ""

    @property
    def display_name(self) -> str:
        return self.name or self.project_id.value


# ═══════════════════════════════════════════════════════════════════════
# Abstract Repository
# ═══════════════════════════════════════════════════════════════════════


class ProjectRepository:
    """Interface — repositorio de proyectos.

    La implementacion concreta vive en infrastructure/repositories.py.
    """

    def load(self, project_id: ProjectId | str) -> Project | None:
        """Carga un proyecto por su ID. Retorna None si no existe."""
        raise NotImplementedError

    def save(self, project: Project) -> None:
        """Persiste un proyecto."""
        raise NotImplementedError

    def list_all(self) -> list[Project]:
        """Lista todos los proyectos registrados."""
        raise NotImplementedError

    def exists(self, project_id: ProjectId | str) -> bool:
        """Verifica si un proyecto existe."""
        raise NotImplementedError
