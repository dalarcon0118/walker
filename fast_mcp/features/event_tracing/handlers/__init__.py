"""handlers/__init__.py — Registry + dispatch for event_tracing handlers.

Same pattern as workflow/memories: dispatch function + error handling.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

_HANDLERS: dict[str, Callable] = {}


def register_trace_handlers(handlers: dict[str, Callable]) -> None:
    """Register event_tracing handlers (called once at startup)."""
    _HANDLERS.update(handlers)


def dispatch_trace(name: str, ctx: dict, **kwargs: Any) -> dict:
    """Dispatch a trace tool call to its handler."""
    handler = _HANDLERS.get(name)
    if handler is None:
        return {"error": f"unknown trace handler: {name}"}
    try:
        return handler(ctx, **kwargs)
    except Exception as exc:
        logger.exception("trace handler %s failed", name)
        return {"error": f"{type(exc).__name__}: {exc}"}
