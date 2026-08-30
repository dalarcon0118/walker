"""app/container.py — Re-export público del composition root (compatibilidad).

Define la API pública del container (AppContainer, RequestScope, register_all,
_build_searcher_for_chain_dir, reset_composition) — antes vivía en
features/main/container.py (hub). Toda la base apunta a `app.container`; el
SSOT del container real está en app/composition/*.
"""
from app.composition.container import AppContainer  # noqa: F401
from app.composition.lifecycle import RequestScope  # noqa: F401
from app.composition.registry import (  # noqa: F401
    _build_searcher_for_chain_dir,
    register_all,
)
from app.bootstrap import reset_composition  # noqa: F401

__all__ = [
    "AppContainer",
    "RequestScope",
    "register_all",
    "_build_searcher_for_chain_dir",
    "reset_composition",
]