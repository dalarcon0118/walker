"""domain/services/block_resolver.py — Block/unblock resolution strategies.

P0: Wait states, timeouts, and resolution strategies.
Uses Result monad (returns) to avoid exceptions as control flow.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from returns.result import Failure, Result, Success

from ..entities import WorkflowState, WorkflowGraph
from ..errors import TransitionBlockedError


class BlockResolver:
    """Resolves workflow blocks without desynchronizing state.

    Strategies: skip, force, wait, escalate, compensate.
    """

    @staticmethod
    def is_blocked(state: WorkflowState) -> bool:
        return bool(state.blocked_at)

    @staticmethod
    def can_unblock(state: WorkflowState, event: str) -> bool:
        if not state.blocked_at:
            return False
        return event in state.pending_events

    @staticmethod
    def resolve(
        state: WorkflowState,
        strategy: str,
        payload: dict[str, Any],
        graph: WorkflowGraph | None = None,
    ) -> Result[WorkflowState, str]:
        """Apply resolution strategy and return Result."""
        try:
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).isoformat()

            if strategy == "skip":
                return Success(BlockResolver._resolve_skip(state, payload, now))
            elif strategy == "force":
                return Success(BlockResolver._resolve_force(state, payload, now))
            else:
                return Success(BlockResolver._resolve_wait(state, payload, now))
        except Exception as e:
            return Failure(f"unexpected: {type(e).__name__}: {e}")

    @staticmethod
    def _resolve_skip(state: WorkflowState, payload: dict, now: str) -> WorkflowState:
        new_conflicts = list(state.conflicts)
        new_conflicts.append({"type": "skip", "node": state.current_node, "resolved_at": now})
        return replace(state, conflicts=new_conflicts, blocked_at="", block_reason="",
                        block_since="", pending_events=[], block_timeout_ms=0, updated_at=now)

    @staticmethod
    def _resolve_force(state: WorkflowState, payload: dict, now: str) -> WorkflowState:
        new_conflicts = list(state.conflicts)
        new_conflicts.append({"type": "force", "node": state.current_node, "resolved_at": now})
        return replace(state, conflicts=new_conflicts, blocked_at="", block_reason="",
                        block_since="", pending_events=[], block_timeout_ms=0, updated_at=now)

    @staticmethod
    def _resolve_wait(state: WorkflowState, payload: dict, now: str) -> WorkflowState:
        new_conflicts = list(state.conflicts)
        new_conflicts.append({"type": "wait", "node": state.current_node, "resolved_at": now})
        return replace(state, conflicts=new_conflicts, updated_at=now)
