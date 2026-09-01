"""infrastructure/noop_side_effects.py — No-op ISideEffectEngine implementation.

Stub: logs compensation attempts, executes nothing. Used until tasks
are ported (Sprint 5). The real SideEffectEngine has Saga pattern,
idempotency, and handler registry (~500 LOC).
"""
from __future__ import annotations

from typing import Any

from returns.result import Success

from ..domain.ports import ISideEffectEngine


class NoOpSideEffectEngine(ISideEffectEngine):
    """Stub: compensation handler returns Success with skipped=True.

    The domain expects Result[T, E] from run_compensation_handler.
    This stub always returns Success to indicate "nothing to compensate".
    """

    def run_compensation_handler(self, handler_name: str, ctx: Any) -> Any:
        return Success({
            "ok": True,
            "skipped": True,
            "handler": handler_name,
            "message": "Side effects not ported yet (Sprint 5)",
        })
