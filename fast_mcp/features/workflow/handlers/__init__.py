"""features/workflow/handlers/__init__.py — Handler registry + dispatch.

Maps tool names to handler callables. Each handler has the signature:

    def handler(ctx: WorkflowContext, **kwargs) -> dict

The registry provides consistent error handling (convention-001: never
raise from handler) and resolves WorkflowContext once per call.

Follows the same pattern as fast_mcp/features/memories/handlers/__init__.py.
"""
from __future__ import annotations

import importlib
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Type alias
Handler = Callable[..., dict[str, Any]]

# ── Handler registry (lazy imports) ──────────────────────────────────

_HANDLER_IMPORTS: dict[str, tuple[str, str]] = {
    "submit": ("fast_mcp.features.workflow.handlers.workflow", "handle_submit"),
    "transition": ("fast_mcp.features.workflow.handlers.workflow", "handle_transition"),
    "status": ("fast_mcp.features.workflow.handlers.workflow", "handle_status"),
    "help": ("fast_mcp.features.workflow.handlers.workflow", "handle_help"),
    "list_projects": ("fast_mcp.features.workflow.handlers.workflow", "handle_list_projects"),
}

# Public set of all handler names
HANDLER_NAMES: frozenset[str] = frozenset(_HANDLER_IMPORTS.keys())

# Cache of resolved handlers
_handlers: dict[str, Handler] = {}


def get_handler(name: str) -> Handler | None:
    """Get a handler by name (lazy import + cache)."""
    if name in _handlers:
        return _handlers[name]

    spec = _HANDLER_IMPORTS.get(name)
    if spec is None:
        return None

    module_path, attr_name = spec
    try:
        mod = importlib.import_module(module_path)
        handler = getattr(mod, attr_name)
        _handlers[name] = handler
        return handler
    except Exception as e:
        logger.warning(
            "workflow handlers: could not load '%s' from %s.%s (%s: %s)",
            name, module_path, attr_name, type(e).__name__, e,
        )
        return None


def dispatch(tool_name: str, ctx: Any, **kwargs) -> dict[str, Any]:
    """Dispatch a workflow tool call to its handler.

    Single entry point for all workflow tool dispatch.
    Consistent error handling: never raises, always returns dict.
    """
    handler = get_handler(tool_name)
    if handler is None:
        return {"error": f"Unknown workflow tool: {tool_name}"}

    try:
        return handler(ctx, **kwargs)
    except Exception as e:
        logger.warning(
            "workflow handler '%s' failed (%s: %s)",
            tool_name, type(e).__name__, e,
        )
        return {"error": str(e), "tool": tool_name}
