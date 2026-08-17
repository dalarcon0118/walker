"""features/main/container.py — Re-export de composition.container para compatibilidad.

Fase 2 (ADR-WALKER-050): el contenido real se migra a
``features.main.composition.container``, ``registry`` y ``lifecycle``.
Este archivo mantiene la compatibilidad de imports existentes hasta que
toda la base de código apunte al paquete ``composition``.
"""
from features.main.composition.container import AppContainer  # noqa: F401
from features.main.composition.lifecycle import RequestScope  # noqa: F401
from features.main.composition.registry import (  # noqa: F401
    _build_searcher_for_chain_dir,
    register_all,
)
from features.main.bootstrap import reset_composition  # noqa: F401

__all__ = [
    "AppContainer",
    "RequestScope",
    "register_all",
    "_build_searcher_for_chain_dir",
    "reset_composition",
]
