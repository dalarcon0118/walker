"""domain/guards.py — Deterministic visibility guards (NestJS-style).

Pure functions, no side effects. A guard ALWAYS returns a boolean
derivable from its input.
"""
from __future__ import annotations

from typing import Any

__all__ = ["ScopeGuard"]


class ScopeGuard:
    """Visibility guard: check(scope, channel) is deterministic.

    Does not raise: if the task doesn't exist in the registry, returns True
    so that `execute()` emits the standard 'not registered' error.
    """

    BLOCKED_SCOPES: frozenset[str] = frozenset({"internal", "system"})
    ALLOWED_CHANNELS: frozenset[str] = frozenset({"system"})

    @staticmethod
    def check(task_name: str, channel: str, registry: Any) -> bool:
        """Returns True if the task CAN execute in the given channel."""
        info = registry.get_task_info(task_name) if hasattr(registry, "get_task_info") else None
        if info is None:
            return True  # unknown task → execute() fails with standard error
        scope = info.get("scope", "public")
        if scope in ScopeGuard.BLOCKED_SCOPES and channel not in ScopeGuard.ALLOWED_CHANNELS:
            return False
        return True

    @staticmethod
    def is_blocked(task_name: str, channel: str, registry: Any) -> bool:
        return not ScopeGuard.check(task_name, channel, registry)
