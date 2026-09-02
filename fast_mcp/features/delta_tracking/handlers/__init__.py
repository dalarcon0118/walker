"""handlers/__init__.py — Handler registry + dispatch for delta tracking.

Same pattern as memories/workflow handlers: flat dispatch dict,
error handling with try/except, returns dict (MCP protocol).
"""
from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Handler registry: name → handler function
_HANDLERS: dict[str, Callable[[dict], dict[str, Any]]] = {}


def register_handler(name: str, handler: Callable[[dict], dict[str, Any]]):
    """Register a handler for a delta_tracking tool."""
    _HANDLERS[name] = handler


def dispatch_delta(tool_name: str, arguments: dict) -> dict[str, Any]:
    """Dispatch a delta_tracking tool call.

    Returns dict suitable for MCP tool response (content list).
    """
    handler = _HANDLERS.get(tool_name)
    if not handler:
        return {"ok": False, "error": f"Unknown delta tool: {tool_name}"}

    try:
        result = handler(arguments)
        return result
    except Exception as e:
        logger.exception("delta handler %s failed", tool_name)
        return {"ok": False, "error": str(e)}
