"""app/bootstrap.py — Composition root final de Walker.

El wiring real vive en UN ÚNICO punto: ``app.composition.registry.register_all``
(invocado por ``AppContainer.build`` en ``app/composition/container.py``).

Este módulo solo expone compat-shims para no romper imports legados:
  - ``compose_app``       → delega en ``AppContainer.build`` (misma API pública
    que el antiguo ``AppComposition``: ``.workflow``, ``.task_registry``,
    ``.side_effect_engine``, ``.module_registry``).
  - ``reset_composition`` → delega en ``AppContainer.reset``.

Así se elimina el segundo composition root que existía en paralelo y que
divergía del canónico (ADR-045 D1).
"""

from __future__ import annotations

from typing import Any


def compose_app(chain_dir: str = "") -> Any:
    """Composition root final de Walker (thin wrapper sobre AppContainer.build).

    Devuelve el ``AppContainer`` del composition root único. Mantiene la firma
    histórica para callers legacy (app.app.WalkerApp, project_use_cases,
    tests).
    """
    from app.composition.container import AppContainer

    return AppContainer.build(chain_dir=chain_dir)


def reset_composition() -> None:
    """Resetea el singleton del composition root (solo tests / hot reload)."""
    from app.composition.container import AppContainer

    AppContainer.reset()
