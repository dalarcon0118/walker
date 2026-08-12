"""tests/test_context_composer_unhappy_path_bdd.py — Camino no feliz del ContextComposer.

Valida que el composer maneja explícitamente providers faltantes o fallidos,
sin caer en fallos silenciosos ni omitir warnings.
"""
from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from features.engine.context.registry import ContextProviderRegistry
from features.engine.context.composer import ContextComposer
from libs.shared_domain import ExecutionContext


def _make_ctx(chain_dir: str = "/tmp/test-chain") -> ExecutionContext:
    return ExecutionContext.create(
        session_id="test-session",
        chain_dir=chain_dir,
        task_id="test-task",
    )


def test_composer_logs_warning_when_provider_missing(caplog, monkeypatch):
    """Si un provider declarado en sources no está registrado, el composer
    debe loguear un warning visible (no silencioso)."""
    mock_qm = MagicMock()
    mock_qm.query_layer.return_value = {"ok": False, "error": "no project"}
    monkeypatch.setattr(
        "features.engine.context.composer.QueryManager",
        lambda *a, **k: mock_qm,
    )

    registry = ContextProviderRegistry()
    # No registrar ningún provider: todos serán "missing"
    composer = ContextComposer(registry)

    node_config = {
        "sources": [{"provider": "missing_provider", "config": {}}],
        "focus": {"role": "tester"},
    }

    with caplog.at_level(logging.WARNING):
        result = composer.compose(node_config, "Template", _make_ctx())

    assert "missing_provider" in caplog.text
    assert "not registered" in caplog.text
    # El compositor no debe crashear; devuelve un contexto vacío/válido
    assert result is not None
    assert result.system_message != ""


def test_composer_handles_provider_collect_failure(caplog, monkeypatch):
    """Si un provider lanza excepción en collect(), el composer debe
    loguear el error y continuar con los providers restantes."""

    class FailingProvider:
        def instructions(self, config, ctx):
            return "Failing provider"

        def collect(self, config, ctx, query_manager=None):
            raise RuntimeError("provider boom")

    mock_qm = MagicMock()
    mock_qm.query_layer.return_value = {"ok": False, "error": "no project"}

    monkeypatch.setattr(
        "features.engine.context.composer.QueryManager",
        lambda *a, **k: mock_qm,
    )

    registry = ContextProviderRegistry()
    registry.register("failing", FailingProvider())
    composer = ContextComposer(registry)

    node_config = {
        "sources": [
            {"provider": "failing", "config": {}},
            {"provider": "missing", "config": {}},
        ],
        "focus": {"role": "tester"},
    }

    with caplog.at_level(logging.ERROR):
        result = composer.compose(node_config, "Template", _make_ctx())

    assert "failing" in caplog.text or "missing" in caplog.text
    assert result is not None


def test_composer_respects_budget_even_with_missing_providers(monkeypatch):
    """El budget del ContextWindowManager debe aplicarse incluso cuando
    algunos providers fallan o faltan."""
    registry = ContextProviderRegistry()
    composer = ContextComposer(registry)

    mock_qm = MagicMock()
    mock_qm.query_layer.return_value = {"ok": False, "error": "no project"}

    monkeypatch.setattr(
        "features.engine.context.composer.QueryManager",
        lambda *a, **k: mock_qm,
    )

    node_config = {
        "sources": [{"provider": "nonexistent", "config": {}}],
        "focus": {"role": "tester"},
        "budget": {"max_tokens": 100},
    }

    result = composer.compose(node_config, "Template", _make_ctx())
    assert result is not None
    assert result.budget is not None
    assert result.budget.max_tokens == 100
