"""domain/side_effect_registry.py — SideEffectRegistry (implements ISideEffectEngine).

Replaces NoOpSideEffectEngine with a real handler registry.
Side effects are registered by name and dispatched by the engine.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from fast_mcp.shared_kernel.ports import ISideEffectEngine

logger = logging.getLogger(__name__)


class SideEffectRegistry(ISideEffectEngine):
    """Real side effect registry.

    Implements ISideEffectEngine from workflow/domain/ports.
    Handlers are registered by name and dispatched on side_effect events.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, Callable] = {}

    def register(self, name: str, handler: Callable) -> None:
        """Register a side effect handler."""
        self._handlers[name] = handler
        logger.debug("SideEffectRegistry: registered '%s'", name)

    def execute(
        self,
        name: str,
        chain_dir: str,
        params: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Execute a side effect by name. Never raises."""
        handler = self._handlers.get(name)
        if handler is None:
            return {"ok": False, "error": f"unknown side effect: {name}"}
        try:
            result = handler(chain_dir=chain_dir, params=params or {}, **kwargs)
            if isinstance(result, dict):
                return result
            return {"ok": True, "data": result}
        except Exception as exc:
            logger.exception("SideEffectRegistry: '%s' failed", name)
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def run_compensation_handler(self, handler_name: str, ctx: Any) -> Any:
        """Execute a compensation handler by name (BPMN compensation)."""
        return self.execute(handler_name, getattr(ctx, 'chain_dir', ''), {})

    def has(self, name: str) -> bool:
        return name in self._handlers

    def list_effects(self) -> list[str]:
        return list(self._handlers.keys())
