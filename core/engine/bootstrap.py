"""
bootstrap.py — Factories neutras de infraestructura del engine.

Uso:
    from core.engine.bootstrap import bootstrap

    registry, se_engine = bootstrap()
    # registry tiene las tasks genéricas del engine
    # se_engine tiene los side-effects genéricos del engine

Nota: este módulo NO escanea features/ ni libs/. Eso es responsabilidad
exclusiva de features/main (composition root final, ADR-045).
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from typing import Any

from returns.pipeline import is_successful
from returns.result import safe

logger = logging.getLogger(__name__)

# ── Service locator neutro (capa_rout.md D1, ADR-045) ──────────────────────
# core/engine es infraestructura pura: NO conoce features/ ni libs/.
# features/main (composition root) inyecta el resolutor de servicios DI via
# set_service_locator(). Los callers internos de core/engine resuelven por
# get_module_service() sin importar features/main (facade neutro).
_SERVICE_LOCATOR: dict[str, Any] = {"fn": None}


def set_service_locator(fn: Any) -> None:
    """Inyecta el resolutor de servicios DI (lo llama features/main)."""
    _SERVICE_LOCATOR["fn"] = fn


def reset_module_registry() -> None:
    """Invalida el service locator (hot reload, capa_rout.md D1).

    El reloader (infrastructure/reloader.py) la invoca tras recargar modulos
    de features/ para que el container DI se reconstruya con el siguiente
    compose_app(). features/main.reset_composition() también la llama.
    """
    _SERVICE_LOCATOR["fn"] = None


def get_module_service(name: str) -> Any:
    """Resuelve un servicio compartido de libs/ por clave lógica (DI).

    Uso en features (en vez de `from libs.infrastructure import X`):
        searcher_factory = get_module_service("cognitive_searcher")
        pr = get_module_service("path_resolver").get_walker_root()

    El resolutor lo inyecta features/main.compose_app() (composition root
    final, ADR-045). KeyError si el locator no está poblado o la clave no
    existe.
    """
    fn = _SERVICE_LOCATOR["fn"]
    if fn is None:
        raise KeyError(
            f"Servicio no registrado: {name} (service locator no inyectado; "
            "llama features.main.compose_app())"
        )
    return fn(name)


def get_module_registry() -> Any:
    """Devuelve el ModuleRegistry canónico de la app (poblado por features/main).

    Facade neutro para tool_catalog y otros callers de core/engine que
    necesitan listar tools/rutas sin conocer features/main.
    """
    return get_module_service("module_registry")


def resolve_service(name: str, default: Any = None) -> Any:
    """Resuelve un servicio DI degradando a `default` (ADR-051 G5).

    Facade neutro del engine: los módulos de core/engine consumen features
    SOLO via service locator (0 imports de features.*). Ante un entorno sin
    composition root inyectado (tests aislados, bootstrap parcial) degrada a
    `default` de forma explícita — nunca lanza KeyError silencioso.
    """
    try:
        return get_module_service(name)
    except Exception:
        return default


def _discover_and_register(
    register_fn_name: str, registry: Any, package_name: str
) -> None:
    """Descubre módulos en un package y llama a su función register().

    Result pattern (convention-001): por módulo, el core @safe nunca lanza;
    ante fallo de registro se loggea warning y el resto del package sigue
    registrándose.
    """
    spec = importlib.util.find_spec(package_name)
    if spec is None or spec.submodule_search_locations is None:
        return

    for finder, name, is_pkg in pkgutil.iter_modules(spec.submodule_search_locations):
        if name.startswith("_"):
            continue
        outcome = _register_module_core(package_name, name, register_fn_name, registry)
        if not is_successful(outcome):
            error = outcome.failure()
            logger.warning(
                "bootstrap: no se pudo registrar '%s.%s' (%s: %s)",
                package_name, name, type(error).__name__, error,
            )


@safe
def _register_module_core(
    package_name: str, name: str, register_fn_name: str, registry: Any
) -> None:
    """Core puro: importa un módulo y llama a su register() (puede lanzar)."""
    module = importlib.import_module(f"{package_name}.{name}")
    register_fn = getattr(module, register_fn_name, None)
    if register_fn:
        register_fn(registry)


def bootstrap_providers(registry: Any) -> None:
    """Registra providers de contexto base del engine.

    Solo escanea core/engine/context/ (providers genéricos: task, state,
    diff, test_results). Los providers de negocio de cada feature se
    registran desde features/main.
    """
    _discover_and_register(
        register_fn_name="register_providers",
        registry=registry,
        package_name="core.engine.context",
    )


def build_context_registry() -> Any:
    """Construye el ContextProviderRegistry base del engine.

    = registry base del engine (diff, test_results — utilidades genéricas)
    + providers base del engine (task, state, diff, test_results).

    Los providers de negocio de features se registran desde features/main.
    """
    from .context import create_default_registry

    registry = create_default_registry()
    bootstrap_providers(registry)
    return registry


@safe
def _build_tracer_core() -> Any:
    """Core puro: construye el TraceLogger por defecto (puede lanzar)."""
    from .infrastructure.trace_log import TraceLogger

    return TraceLogger()


def bootstrap(registry: Any = None, se_engine: Any = None, separated: bool = False) -> Any:
    """Bootstrap neutro: construye TaskRegistry y SideEffectEngine genéricos.

    Este módulo NO escanea features/ ni libs/ (ADR-045). El TaskRegistry y
    el SideEffectEngine se construyen VACÍOS (sin tasks de dominio): las
    tasks/side effects de la app los registra features/main.compose_app()
    (composition root final) o el caller los inyecta explícitamente.

    Args:
        registry: Instancia de TaskRegistry. Si None, crea una default (vacía).
        se_engine: Instancia de SideEffectEngine. Si None, crea una default.
        separated: Si True, devuelve (domain_registry, system_registry, se_engine).

    Returns:
        Si separated=False: Tuple de (registry, se_engine).
        Si separated=True: Tuple de (domain_registry, system_registry, se_engine).
    """
    tracer: Any = None
    if registry is None:
        from .infrastructure.task_registry import InMemoryTaskRegistry

        tracer_outcome = _build_tracer_core()
        if is_successful(tracer_outcome):
            tracer = tracer_outcome.value_or(None)
        else:
            error = tracer_outcome.failure()
            logger.warning(
                "bootstrap: tracer no disponible (%s: %s); registry sin tracer",
                type(error).__name__, error,
            )
        registry = InMemoryTaskRegistry(tracer=tracer)

    if se_engine is None:
        from .infrastructure.side_effects import SideEffectEngine

        se_engine = SideEffectEngine(tracer=tracer)

    if separated:
        from .infrastructure.domain_task_registry import DomainTaskRegistry
        from .infrastructure.system_task_registry import SystemTaskRegistry

        domain_registry = DomainTaskRegistry(tracer=tracer)
        system_registry = SystemTaskRegistry(tracer=tracer)

        for name, fn in registry._tasks.items():
            meta = registry._metadata.get(name, {})
            scope = meta.get("scope", "public")
            if scope in ("internal", "domain"):
                domain_registry.register(name, fn, meta)
            else:
                system_registry.register(name, fn, meta)

        return domain_registry, system_registry, se_engine

    return registry, se_engine
