"""Tests unitarios — ScopeGuard (ADR-WALKER-060, Fase 1).

Guarda de visibilidad determinista: check(task_name, channel, registry) → bool.
Se testea aislada (pure function) con un stub de registry mínimo.

Escenarios (ADR §4, failure scenario #1):
  1. task internal + channel cli      → bloqueada
  2. task internal + channel mcp      → bloqueada
  3. task internal + channel system   → permitida
  4. task system  + channel cli       → bloqueada
  5. task public  + channel cli       → permitida
  6. task inexistente + channel cli   → permitida (execute() falla estándar)
"""
from __future__ import annotations

import pytest

from shared.core.engine.domain.guards import ScopeGuard
from shared.core.engine.domain.registry import TaskRegistry


class _StubRegistry(TaskRegistry):
    """Stub mínimo: solo implementa get_task_info()."""

    def __init__(self, metadata: dict | None = None):
        self._meta = metadata or {}

    def get_task_info(self, name: str) -> dict | None:
        return self._meta.get(name)

    # ── Métodos abstractos no usados en el test ─────────────────────────────
    def register(self, name, fn, metadata=None): raise NotImplementedError
    def execute(self, name, ctx, data=None): raise NotImplementedError
    def list_tasks(self): raise NotImplementedError
    def list_tasks_with_meta(self): raise NotImplementedError
    def get_task(self, name): raise NotImplementedError
    def discover(self): raise NotImplementedError


# (scope, channel) → esperado permitida
CASES = [
    ("internal", "cli", False),
    ("internal", "mcp", False),
    ("internal", "system", True),
    ("system", "cli", False),
    ("system", "mcp", False),
    ("system", "system", True),
    ("public", "cli", True),
    ("public", "mcp", True),
    ("public", "system", True),
]


@pytest.mark.parametrize("scope,channel,expected", CASES)
def test_scope_guard_check(scope: str, channel: str, expected: bool):
    registry = _StubRegistry({"some_task": {"scope": scope}})
    assert ScopeGuard.check("some_task", channel, registry) is expected


def test_scope_guard_inexistent_task_is_allowed():
    """Task inexistente → check() devuelve True (execute() emitirá error estándar)."""
    registry = _StubRegistry({})
    assert ScopeGuard.check("no_existe", "cli", registry) is True
    assert ScopeGuard.check("no_existe", "system", registry) is True


def test_scope_guard_default_scope_is_public():
    """Sin metadata de scope → se asume 'public' (permitida en cualquier channel)."""
    registry = _StubRegistry({"task": {}})
    assert ScopeGuard.check("task", "cli", registry) is True
    assert ScopeGuard.check("task", "mcp", registry) is True


def test_scope_guard_is_blocked_inverse():
    registry = _StubRegistry({"secret": {"scope": "internal"}})
    assert ScopeGuard.is_blocked("secret", "cli", registry) is True
    assert ScopeGuard.is_blocked("secret", "system", registry) is False
