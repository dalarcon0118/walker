"""
bootstrap.py — Descubre y registra tasks/ y side_effects/ modulares.

Uso:
    from features.engine.bootstrap import bootstrap

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
    """Descubre tareas desde features/task_execution/tasks/."""
    _discover_and_register(
        package_path=None,
        register_fn_name="register",
        registry=registry,
        package_name="features.task_execution.tasks",
    )


def bootstrap_side_effects(engine: Any) -> None:
    """Descubre side-effects desde features/task_execution/side_effects/."""
    _discover_and_register(
        package_path=None,
        register_fn_name="register",
        registry=engine,
        package_name="features.task_execution.side_effects",
    )


def bootstrap_providers(registry: Any) -> None:
    """Descubre y registra providers de contexto desde cada feature.

    Cada feature con providers declara:
        features/<feature>/context/providers.py:register_providers(registry)

    El engine NO los conoce directamente: solo los consume si un nodo
    los declara en context.sources. Esto mantiene el engine como un
    mecanismo puro (no sabe QUÉ transporta).
    """
    _discover_and_register(
        package_path=None,
        register_fn_name="register_providers",
        registry=registry,
        package_name="features.code_analysis.context",
    )
    _discover_and_register(
        package_path=None,
        register_fn_name="register_providers",
        registry=registry,
        package_name="features.cognitive_knowledge.context",
    )
    _discover_and_register(
        package_path=None,
        register_fn_name="register_providers",
        registry=registry,
        package_name="features.spring.context",
    )
    _discover_and_register(
        package_path=None,
        register_fn_name="register_providers",
        registry=registry,
        package_name="features.event_tracing.context",
    )
    _discover_and_register(
        package_path=None,
        register_fn_name="register_providers",
        registry=registry,
        package_name="features.delta_tracking.context",
    )
    _discover_and_register(
        package_path=None,
        register_fn_name="register_providers",
        registry=registry,
        package_name="features.project.context",
    )


def build_context_registry() -> Any:
    """Construye el ContextProviderRegistry completo.

    = registry base del engine (diff, test_results — utilidades genéricas)
    + providers de negocio de cada feature (cpg, ckg, business, state,
      delta, trace, project) registrados EXTERNAMENTE.

    El engine recibe este registry ya armado; no sabe qué providers hay.
    """
    from .context import create_default_registry

    registry = create_default_registry()
    bootstrap_providers(registry)
    return registry


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

        # Inyectar tracer (TraceLogger) para registrar task_execute en el TraceGraph
        try:
            from .infrastructure.trace_log import TraceLogger
            tracer = TraceLogger()
        except Exception:
            tracer = None
        registry = InMemoryTaskRegistry(tracer=tracer)

    if se_engine is None:
        from .infrastructure.side_effects import SideEffectEngine

        se_engine = SideEffectEngine()

    bootstrap_tasks(registry)
    bootstrap_side_effects(se_engine)

    return registry, se_engine
