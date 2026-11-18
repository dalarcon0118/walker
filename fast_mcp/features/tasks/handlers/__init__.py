"""handlers/__init__.py — Handler registry + dispatch for tasks feature.

Provides:
  - register_all_tasks(registry): register all task handlers into a TaskRegistry
  - dispatch_tasks(tool_name, arguments): MCP dispatch for task tools
"""
from __future__ import annotations

import importlib
import logging
from typing import Any, Callable

from returns.pipeline import is_successful
from returns.result import safe

logger = logging.getLogger(__name__)

# ── MCP dispatch registry ────────────────────────────────────────
_HANDLERS: dict[str, Callable[[dict], dict[str, Any]]] = {}


def register_handler(name: str, handler: Callable[[dict], dict[str, Any]]):
    """Register a handler for a tasks tool."""
    _HANDLERS[name] = handler


def dispatch_tasks(tool_name: str, arguments: dict) -> dict[str, Any]:
    """Dispatch a tasks tool call."""
    handler = _HANDLERS.get(tool_name)
    if not handler:
        return {"ok": False, "error": f"Unknown task tool: {tool_name}"}
    try:
        return handler(arguments)
    except Exception as e:
        logger.exception("task handler %s failed", tool_name)
        return {"ok": False, "error": str(e)}


# ── TaskRegistry bootstrap ───────────────────────────────────────

_HANDLER_MODULES = [
    "implement",
    "session",
    "sprint_status",
    "verify",
    "capture",
    "sprint_planning",
    "observe",
    "state_construction",
    "info_acquisition",
    "monitoring",
    "list_projects",
    "sync_code_index",
    "baseline",
    "knowledge",
    # Acciones del grafo de diseño/ejecución. Cada módulo registra las suyas.
    "design",
    "review",
]


@safe
def _register_module_core(name: str, registry, sprint_service: Any = None, cpg_sync: Any = None) -> None:
    """Import a handler module and call its register()."""
    module = importlib.import_module(f"fast_mcp.features.tasks.handlers.{name}")
    register_fn = getattr(module, "register", None)
    if not register_fn:
        return
    # Only sprint_planning consumes sprint_service; sync_code_index consumes
    # cpg_sync (la función la provee code_analysis vía composition). El resto
    # conserva la firma registry-only (R1: no cross-feature import).
    if name == "sprint_planning" and sprint_service is not None:
        register_fn(registry, sprint_service=sprint_service)
    elif name == "sync_code_index" and cpg_sync is not None:
        register_fn(registry, cpg_sync=cpg_sync)
    else:
        register_fn(registry)


def register_all_tasks(registry, sprint_service: Any = None, cpg_sync: Any = None) -> None:
    """Discover and register all task handlers into a TaskRegistry.

    Args:
        registry: TaskRegistry to populate.
        sprint_service: SprintService-like object (injected by composition
            root) forwarded to sprint_planning handlers.
        cpg_sync: `sync_project_cpg(chain_dir) -> dict` (inyectado por el
            composition root) para el bootstrap idempotente del CPG.
    """
    for name in _HANDLER_MODULES:
        outcome = _register_module_core(
            name, registry, sprint_service=sprint_service, cpg_sync=cpg_sync
        )
        if not is_successful(outcome):
            error = outcome.failure()
            logger.warning(
                "tasks: failed to register handler '%s' (%s: %s)",
                name,
                type(error).__name__,
                error,
            )
