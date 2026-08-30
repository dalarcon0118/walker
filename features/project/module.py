"""features/project/module.py — Manifiesto del bounded context `project` (ADR-066 D4).

El adaptador del puerto `IProjectContext` vive aquí (cerca del `ProjectService`
que delega), no en el pegamento central `app/engine_ports.py`.
"""
from __future__ import annotations

from typing import Any

from shared.core.engine.domain.ports import IProjectContext
from features.project import ProjectService
from shared.libs.module_system.contracts import ModuleManifest


class ProjectContextAdapter(IProjectContext):
    """Implementa IProjectContext delegando a ProjectService."""

    def __init__(self, project_service: Any):
        self._project_service = project_service

    def resolve_paths(self, chain_dir: str) -> Any:
        return self._project_service.resolve_paths(chain_dir)

    def get_workflow_json_path(self, chain_dir: str) -> str:
        paths = self._project_service.resolve_paths(chain_dir)
        return getattr(paths, "workflow_json_path", "")

    def list_available(self) -> list[dict]:
        return self._project_service.list_available()

    def find_active_sprint(self, project_id: str) -> str:
        return self._project_service.find_active_sprint(project_id)


MODULE = ModuleManifest(
    name="project",
    imports=["shared"],
    adapters=[(IProjectContext, ProjectContextAdapter)],
    exports=["project_context"],
)
