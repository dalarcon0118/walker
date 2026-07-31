"""
bootstrap.py — Descubre y registra tasks/ y side_effects/ modulares.

Uso:
    from _walker.workflow_engine.bootstrap import bootstrap

    registry, se_engine = bootstrap()
    # registry tiene todas las tareas registradas
    # se_engine tiene todos los side-effects registrados
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import Any


def _discover_and_register(
    package_path: str, register_fn_name: str, registry: Any, package_name: str
) -> None:
    """Descubre modulos en un package y llama a su funcion register()."""
    spec = importlib.util.find_spec(package_name)
    if spec is None or spec.submodule_search_locations is None:
        return

    for finder, name, is_pkg in pkgutil.iter_modules(spec.submodule_search_locations):
        if name.startswith("_"):
            continue
        try:
            module = importlib.import_module(f"{package_name}.{name}")
            register_fn = getattr(module, register_fn_name, None)
            if register_fn:
                register_fn(registry)
        except Exception:
            pass  # Silently skip modules that fail to register


def bootstrap_tasks(registry: Any) -> None:
    """Descubre tasks/ y registra todas las tareas."""
    _discover_and_register(
        package_path=None,
        register_fn_name="register",
        registry=registry,
        package_name="tasks",
    )


def bootstrap_side_effects(engine: Any) -> None:
    """Descubre side_effects/ y registra todos los handlers."""
    _discover_and_register(
        package_path=None,
        register_fn_name="register",
        registry=engine,
        package_name="side_effects",
    )


def bootstrap(registry: Any = None, se_engine: Any = None) -> tuple[Any, Any]:
    """Bootstrap completo: descubre y registra tasks + side-effects.

    Args:
        registry: Instancia de TaskRegistry. Si None, crea una default.
        se_engine: Instancia de SideEffectEngine. Si None, crea una default.

    Returns:
        Tuple de (registry, se_engine) con todo registrado.
    """
    if registry is None:
        from .infrastructure.task_registry import InMemoryTaskRegistry

        registry = InMemoryTaskRegistry()

    if se_engine is None:
        from .infrastructure.side_effects import SideEffectEngine

        se_engine = SideEffectEngine()

    bootstrap_tasks(registry)
    bootstrap_side_effects(se_engine)

    return registry, se_engine
