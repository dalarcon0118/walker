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

import os
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
    """Bootstrap completo: descubre y registra tasks + side-effects + modules.

    Args:
        registry: Instancia de TaskRegistry. Si None, crea una default.
        se_engine: Instancia de SideEffectEngine. Si None, crea una default.

    Returns:
        Tuple de (registry, se_engine) con todo registrado.
    """
    tracer: Any = None
    if registry is None:
        from .infrastructure.task_registry import InMemoryTaskRegistry

        try:
            from .infrastructure.trace_log import TraceLogger
            from .domain.ports import ITracer
            tracer: ITracer | None = TraceLogger()
        except Exception:
            tracer = None
        registry = InMemoryTaskRegistry(tracer=tracer)

    if se_engine is None:
        from .infrastructure.side_effects import SideEffectEngine

        se_engine = SideEffectEngine(tracer=tracer)

    bootstrap_tasks(registry)
    bootstrap_side_effects(se_engine)
    bootstrap_modules(registry)

    return registry, se_engine


# Registro de módulos de la APP (singleton por proceso). Se pobla la primera vez
# que bootstrap_modules() corre sin registry. Los features lo usan para resolver
# servicios de libs por clave (DI / IoC) sin importar internos.
_APP_MODULE_REGISTRY: list[Any] = [None]


def _module_registry_singleton() -> Any:
    """Devuelve el ModuleRegistry de la app, construyéndolo la primera vez."""
    if _APP_MODULE_REGISTRY[0] is None:
        bootstrap_modules()
    return _APP_MODULE_REGISTRY[0]


def get_module_service(name: str) -> Any:
    """Resuelve un servicio compartido de libs por clave lógica (DI).

    Uso en features (en vez de `from libs.infrastructure import X`):
        searcher_factory = get_module_service("cognitive_searcher")
        pr = get_module_service("path_resolver").get_walker_root()

    KeyError si la clave no está registrada. Convierte la dependencia a los
    internos de libs en una dependencia al contrato del registry.
    """
    return _module_registry_singleton().get_service(name)


def bootstrap_modules(registry: Any = None) -> Any:
    """Construye el ModuleRegistry composite + container DI de servicios libs.

    IMPORTANTE: este registry es independiente del TaskRegistry. Recibe un
    IModuleRegistry opcional (o InMemoryModuleRegistry default). Nunca reuses
    el registry de tasks aquí: los servicios DI y los módulos composite solo
    viven en el ModuleRegistry.

    Los features resuelven servicios por clave lógica (get_service) en vez de
    importar implementaciones concretas de libs/.
    """
    from .infrastructure.module_registry import InMemoryModuleRegistry

    module_registry = registry or InMemoryModuleRegistry()
    if registry is None:
        _register_libs_services(module_registry)
        _APP_MODULE_REGISTRY[0] = module_registry
    _discover_mcp_modules(module_registry)
    return module_registry


def _register_libs_services(registry: Any) -> None:
    """Registra servicios transversales de libs/ como adapters DI (claves lógicas).

    Los features reciben estos servicios inyectados por clave en vez de
    depender de la estructura interna de libs/ (inversión de control).
    Las factories lazy se construyen al primer get_service() (singleton).
    """
    importlib.import_module("libs.path_resolver")  # Asegura imports tempranos
    registry.register_service(
        "cognitive_searcher",
        _build_searcher,
    )
    registry.register_service(
        "cognitive_graph",
        _build_graph,
    )
    registry.register_service(
        "path_resolver",
        importlib.import_module("libs.path_resolver"),
    )
    registry.register_service(
        "mcp_server",
        lambda: importlib.import_module("libs.mcp_server").McpServer(),
        lazy=True,
    )
    registry.register_service(
        "prompt_manager",
        _build_prompt_manager,
    )


def _build_graph(project_root: str = "") -> Any:
    """Factory del orquestador CognitiveGraph (escritura + búsqueda).

    Se resuelve por clave "cognitive_graph". Composición: CognitiveGraph
    auto-registra sus índices por defecto (ckg/cpg/delta/trace).
    """
    from libs.cognitive_graph import CognitiveGraph

    return CognitiveGraph(project_root or ".")


def _build_searcher(project_root: str = "") -> Any:
    """Factory del lector CQRS sobre CognitiveGraph (capa de lectura).

    Composición (única lugar donde se arma el grafo):
        CognitiveGraph(project_root) → CognitiveGraphSearcher(graph)

    El Searcher recibe el grafo inyectado (DI): no sabe cómo se construye.
    Se resuelve por clave "cognitive_searcher" en el registry.
    """
    from libs.cognitive_graph import CognitiveGraph
    from libs.infrastructure import CognitiveGraphSearcher

    graph = CognitiveGraph(project_root or ".")
    return CognitiveGraphSearcher(graph)


def _build_prompt_manager(chain_dir: str = "") -> Any:
    """Factory del module service de prompts (DI, clave "prompt_manager").

    Devuelve un PromptManager (IPromptManager) para un chain_dir dado —
    igual que "cognitive_searcher" parametriza por project_root. Los llaman
    con su chain_dir y reciben la implementación del puerto; NUNCA importan
    features.prompts directamente (el contrato es IPromptManager).
    """
    from features.prompts import PromptManager

    return PromptManager(chain_dir=chain_dir or os.getcwd(), project_dir=None)


def _discover_mcp_modules(registry: Any) -> None:
    """Busca features/*/mcp.py y registra su register_module."""
    import importlib
    import pkgutil

    features_pkg = importlib.import_module("features")
    for _, mod_name, is_pkg in pkgutil.iter_modules(features_pkg.__path__):
        if mod_name.startswith("_"):
            continue
        mcp_path = f"features.{mod_name}.mcp"
        try:
            mod = importlib.import_module(mcp_path)
            register_fn = getattr(mod, "register_module", None)
            if register_fn:
                register_fn(registry)
        except Exception:
            pass
