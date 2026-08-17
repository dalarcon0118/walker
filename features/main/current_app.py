"""features/main/current_app.py — WalkerApp del proceso (inyectado por el entry point).

.. deprecated::
    Este módulo es un puente transitorio hacia AppContainer.
    Usar ``features.main.container.AppContainer`` directamente.
    Se eliminará en una versión futura (Fase 6 ADR-WALKER-050).

ADR-046: el entry point compone AppContainer.build() UNA vez y registra el
WalkerApp aquí; la capa de aplicación lo RESUELVE (get_current_app) en vez de
construir infraestructura por request.

Si no está registrado (tests que invocan handlers/adapter directo), resuelve
vía AppContainer.build() — singleton estable.
"""

from __future__ import annotations

import warnings
from typing import Any

_CURRENT_APP: Any = None


def set_current_app(app: Any) -> None:
    """Registra el WalkerApp compuesto UNA vez (entry point o build diferido)."""
    global _CURRENT_APP
    _CURRENT_APP = app


def get_current_app() -> Any:
    """Devuelve el WalkerApp del proceso (resolve, no build).

    .. deprecated:: Usar ``AppContainer.build()`` directamente.
    """
    warnings.warn(
        "current_app.get_current_app() está deprecated. "
        "Usar features.main.container.AppContainer.build() directamente.",
        DeprecationWarning,
        stacklevel=2,
    )
    global _CURRENT_APP
    if _CURRENT_APP is None:
        from features.main.app import WalkerApp
        from features.main.container import AppContainer

        _CURRENT_APP = WalkerApp(AppContainer.build())
    return _CURRENT_APP


def reset_current_app() -> None:
    """Invalida el WalkerApp del proceso (tests / reset_composition)."""
    global _CURRENT_APP
    _CURRENT_APP = None


__all__ = ["set_current_app", "get_current_app", "reset_current_app"]
