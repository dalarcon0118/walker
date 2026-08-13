"""tests/test_context_registry_completeness_bdd.py — Completitud del ContextProviderRegistry.

Este test es el guardián de INV-010/INV-012: valida que el registry construido
por el composition root (features/main.compose_app) incluye TODOS los providers
esperados del sistema.

ADR-045: core/engine.build_context_registry() es NEUTRO (solo task, state, diff,
test_results). Los providers de negocio (business, ckg, cpg, delta, project,
reflection, trace) los registra features/main — el ÚNICO que conoce features/.
Cualquier omisión se detecta aquí, sin ejercitar el camino feliz del Composer.
"""
from __future__ import annotations

import pytest

from features.main.bootstrap import compose_app


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
    app = compose_app()
    registry = app.workflow._context_registry
    assert registry is not None, "composition root sin context_registry"
    available = set(registry.available())
    missing = EXPECTED_PROVIDERS - available
    extra = available - EXPECTED_PROVIDERS

    assert not missing, (
        f"Faltan providers en el composition root: {sorted(missing)}. "
        "Verificar que features/main registra todos los providers de negocio."
    )
    assert not extra, (
        f"Providers inesperados: {sorted(extra)}. "
        "Actualizar EXPECTED_PROVIDERS si son legítimos."
    )
    assert len(available) == len(EXPECTED_PROVIDERS), (
        f"Registry tiene {len(available)} providers, se esperaban {len(EXPECTED_PROVIDERS)}"
    )
