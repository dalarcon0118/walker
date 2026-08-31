"""
tasks/list_projects.py — Lista todos los proyectos disponibles.

Task registrada como 'list_projects' en el TaskRegistry.
Usa ProjectService para descubrir proyectos.
"""
from __future__ import annotations

from typing import Any

from shared.core.engine.domain.execution import ExecutionContext, TaskResult


def list_projects_handler(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Scan .cognitive/projects/ and return list with sprint status."""
    from features.project import ProjectService

    projects = ProjectService.list_available()
    return TaskResult.success({"projects": projects})


def register(registry: Any) -> None:
    """Registra list_projects en el TaskRegistry."""
    registry.register(
        "list_projects",
        list_projects_handler,
        metadata={
            "description": "List all available projects with sprint status",
            "usage": "Used by START.LIST node action",
            "tags": ["project", "discovery"],
        },
    )
