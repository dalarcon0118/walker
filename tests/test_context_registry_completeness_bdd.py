"""tests/test_context_registry_completeness_bdd.py — Completitud del ContextProviderRegistry.

Este test es el guardián de INV-010/INV-012: valida que el registry construido
por build_context_registry() incluye TODOS los providers esperados del sistema.

Cualquier omisión (como task/state faltando en el registry) se detecta aquí,
sin tener que ejercitar el camino feliz del ContextComposer.
"""
from __future__ import annotations

import pytest

from features.engine.bootstrap import build_context_registry


EXPECTED_PROVIDERS = {
    "business",
    "ckg",
    "cpg",
    "delta",
    "diff",
    "project",
    "reflection",
    "state",
    "task",
    "test_results",
    "trace",
}


def test_build_context_registry_completeness():
    registry = build_context_registry()
    available = set(registry.available())
    missing = EXPECTED_PROVIDERS - available
    extra = available - EXPECTED_PROVIDERS

    assert not missing, (
        f"Faltan providers en build_context_registry(): {sorted(missing)}. "
        "Verificar que bootstrap_providers() registra todos los módulos."
    )
    assert not extra, (
        f"Providers inesperados en build_context_registry(): {sorted(extra)}. "
        "Actualizar EXPECTED_PROVIDERS si son legítimos."
    )
    assert len(available) == len(EXPECTED_PROVIDERS), (
        f"Registry tiene {len(available)} providers, se esperaban {len(EXPECTED_PROVIDERS)}"
    )
