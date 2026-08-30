"""
bootstrap.py — Composition root + factories del engine.

DI migration (ADR-WALKER-058/059):
    get_module_service(), resolve_service(), get_module_registry() fueron
    ELIMINADOS. Todos los callers ahora reciben dependencias por constructor
    injection desde el composition root (app/bootstrap.py).

    set_service_locator() y reset_module_registry() permanecen para que
    el composition root inyecte el resolutor de servicios.
    resolve_locator() es el PUNTO UNICO de lectura del locator (DRY):
    repos y glue de infraestructura resuelven servicios por clave logica
    sin duplicar el patron ni exportar _SERVICE_LOCATOR.

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

# ── Service locator (solo composition root) ──────────────────────────────────
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


def resolve_locator(name: str) -> Any:
    """Fallback DI por contrato (ADR-045/058): resuelve un servicio del
    composition root via el service locator neutro.

    Punto UNICO de lectura de _SERVICE_LOCATOR (DRY): repos cognitivos y
    glue de infraestructura resuelven 'cognitive_graph'/'cognitive_searcher'
    sin duplicar el patron ni importar features/ (frontera core/engine).
    Fail-open: None si el locator no esta inyectado, el servicio no existe
    o su resolucion lanza — nunca propaga (convention-001).
    """
    fn = _SERVICE_LOCATOR.get("fn")
    if not callable(fn):
        return None
    try:
        return fn(name)
    except Exception:
        return None


# ── Package discovery (interno) ──────────────────────────────────────────────


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


# ── Context registry builder ─────────────────────────────────────────────────


def build_context_registry() -> Any:
    """Construye el ContextProviderRegistry base del engine.

    Incluye providers genéricos (task, state, diff, test_results) via
    discovery automático de shared/core/engine/context/.

    Los providers de negocio de features se registran desde features/main
    (composition root).
    """
    from .context import create_default_registry

    registry = create_default_registry()

    # Bootstrap providers internos (antes bootstrap_providers, inlined ADR-WALKER-058)
    _discover_and_register(
        register_fn_name="register_providers",
        registry=registry,
        package_name="shared.core.engine.context",
    )

    return registry


# ── Tracer factory ────────────────────────────────────────────────────────────


@safe
def _build_tracer_core() -> Any:
    """Core puro: construye el TraceLogger por defecto (puede lanzar)."""
    from .infrastructure.trace_log import TraceLogger

    return TraceLogger()


# ── Bootstrap factory ELIMINADO (ADR-045) ─────────────────────────────────────
# El composition root (app.bootstrap.compose_app → register_all) construye y
# registra los registries poblados. Para resolverlos usa el service locator
# neutro: resolve_locator("task_registry") / resolve_locator("side_effect_engine").
# No existe más factory que fabrique registries vacíos (fail-fast en
# OrchestrationService si no se inyectan vía composition root).


# ── Domain port registration (DIP: domain → up inversion) ──────────────────────
# El dominio (domain/) define PUERTOS y los resuelve via domain.ports_registry;
# este composition root (nivel application del engine) registra los ADAPTADORES
# concretos (infrastructure/application). Así domain/ NO importa nunca
# infrastructure/ ni application/ (regla domain-first / clean architecture).
#
# Se ejecuta al importar este módulo (engine/__init__ lo importa), de modo que
# cualquier import del engine —incluidos tests que usan el dominio directamente—
# tenga los puertos disponibles. El composition root final (app/composition)
# puede re-registrar ISideEffectEngine con el SideEffectEngine totalmente cableado
# (register_port reemplaza el default).


def register_engine_domain_ports() -> None:
    """Registra los adaptadores concretos de los puertos del dominio.

    Puertos invertidos (ADR-WALKER-067, fase 1):
      - IStateStore      <- WorkflowStateRepository (infra)  [antes: domain/state.py]
      - IConfig          <- ConfigAdapter (infra)            [antes: domain/session.py]
      - IContextValidator<- ContextResolver (app)           [antes: domain/entities.py]
      - ISideEffectEngine<- SideEffectEngine (infra)        [antes: compensation_engine.py]
      - ISessionStore    <- SessionStore (infra)            [antes: domain/session.py]
    """
    from .domain.ports_registry import register_port
    from .domain.ports import (
        IStateStore,
        IConfig,
        IContextValidator,
        ISideEffectEngine,
        ISessionStore,
    )
    from .infrastructure.repositories import WorkflowStateRepository
    from .infrastructure.config import ConfigAdapter
    from .infrastructure.side_effects import SideEffectEngine
    from .infrastructure.session_store import SessionStore
    from .application.context_resolver import ContextResolver

    register_port(IStateStore, WorkflowStateRepository())
    register_port(IConfig, ConfigAdapter())
    register_port(IContextValidator, ContextResolver())
    register_port(ISideEffectEngine, SideEffectEngine())
    register_port(ISessionStore, SessionStore)


# Safety net: disponible en cualquier import del engine (tests, CLI, MCP).
register_engine_domain_ports()
