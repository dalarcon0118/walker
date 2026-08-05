"""
domain/ports.py — Abstract interfaces (ports) for the Project domain.

The domain defines WHAT it needs; infrastructure implements HOW.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .entities import Project
from .values import ProjectId


class ProjectRepository(ABC):
    """Abstract repository for Project aggregates.

    The domain only depends on this interface.
    Concrete implementations live in infrastructure/.
    """

    @abstractmethod
    def save(self, project: Project) -> tuple["Project | None", list[str]]:
        """Persist a Project. Returns (saved, errors)."""
        pass

    @abstractmethod
    def load(self, project_id: ProjectId) -> tuple["Project | None", list[str]]:
        """Load a Project by id. Returns (project, errors)."""
        pass

    @abstractmethod
    def list_all(self) -> tuple[list["Project"], list[str]]:
        """List all registered projects. Returns (projects, errors)."""
        pass

    @abstractmethod
    def exists(self, project_id: ProjectId) -> tuple[bool, list[str]]:
        """Check if a project exists. Returns (exists, errors)."""
        pass
