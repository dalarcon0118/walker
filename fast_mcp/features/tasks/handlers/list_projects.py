"""handlers/list_projects.py — acción del sub-estado START.LIST.

El nodo `data/workflow/nodes/start.node.json` declara
`"action": "list_projects"`, pero esa acción no existía en el TaskRegistry:
todo `submit` fallaba con
`TaskRegistry: action 'list_projects' not found for 'START.LIST'`.

Devuelve el registro de proyectos (mismo shape que el tool MCP homónimo) para
que el agente elija uno o cree otro.
"""
from __future__ import annotations

from typing import Any

from fast_mcp.features.tasks.domain.task_registry import ExecutionContext, TaskResult


def list_projects(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Lista los proyectos registrados. Sin IO de negocio: solo el registry."""
    try:
        from fast_mcp.shared_kernel.registry_store import RegistryStore

        registry = RegistryStore().read()
        projects = registry.get("projects", {})
    except Exception as exc:  # el nodo es bootstrap: un fallo debe ser explícito
        return TaskResult.failure(f"list_projects: {exc}")

    return TaskResult.success(data={
        "count": len(projects),
        "projects": [
            {
                "project_id": pid,
                "name": p.get("name", ""),
                "path": p.get("repo_path", ""),
            }
            for pid, p in projects.items()
        ],
        "chain_dir": ctx.chain_dir,
    })


def register(registry) -> None:
    """Registra la acción que consume START.LIST."""
    registry.register("list_projects", list_projects, metadata={
        "description": "START.LIST: lista los proyectos registrados para que el agente elija",
        "tags": ["start", "bootstrap"],
        "scope": "internal",
    })
