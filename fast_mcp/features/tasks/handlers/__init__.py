"""handlers/__init__.py — Handler registry + dispatch for tasks feature."""
from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

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
