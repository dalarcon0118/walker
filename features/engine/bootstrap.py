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
import logging
import os
import pkgutil
from typing import Any

from returns.pipeline import is_successful
from returns.result import safe

logger = logging.getLogger(__name__)


def _discover_and_register(
    register_fn_name: str, registry: Any, package_name: str
) -> None:
    """Descubre modulos en un package y llama a su funcion register().

    Result pattern (convention-001): por modulo, el core @safe nunca lanza;
    ante fallo de registro se loggea warning (gap SPEC-005: ya no se traga
    excepciones en silencio) y el resto del package sigue registrandose.
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
    """Core puro: importa un modulo y llama a su register() (puede lanzar)."""
    module = importlib.import_module(f"{package_name}.{name}")
    register_fn = getattr(module, register_fn_name, None)
    if register_fn:
        register_fn(registry)


def bootstrap_tasks(registry: Any) -> None:
    """Descubre tareas desde features/task_execution/tasks/."""
    _discover_and_register(
        register_fn_name="register",
        registry=registry,
        package_name="features.task_execution.tasks",
    )


def bootstrap_side_effects(engine: Any) -> None:
    """Descubre side-effects desde features/task_execution/side_effects/."""
    _discover_and_register(
        register_fn_name="register",
        registry=engine,
        package_name="features.task_execution.side_effects",
    )


def bootstrap_providers(registry: Any) -> None:
    """Descubre y registra providers de contexto desde cada feature.

    Cada feature con providers declara:
        features/<feature>/context/providers.py:register_providers(registry)

    El engine expone sus propios providers base (task, state, diff,
    test_results) a través de features.engine.context.providers.
    """
    _discover_and_register(
        register_fn_name="register_providers",
        registry=registry,
        package_name="features.engine.context",
    )
    _discover_and_register(
        register_fn_name="register_providers",
        registry=registry,
        package_name="features.code_analysis.context",
    )
    _discover_and_register(
        register_fn_name="register_providers",
        registry=registry,
        package_name="features.cognitive_knowledge.context",
    )
    _discover_and_register(
        register_fn_name="register_providers",
        registry=registry,
        package_name="features.event_tracing.context",
    )
    _discover_and_register(
        register_fn_name="register_providers",
        registry=registry,
        package_name="features.delta_tracking.context",
    )
    _discover_and_register(
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


@safe
def _build_tracer_core() -> Any:
    """Core puro: construye el TraceLogger por defecto (puede lanzar)."""
    from .infrastructure.trace_log import TraceLogger

    return TraceLogger()


def bootstrap(registry: Any = None, se_engine: Any = None, separated: bool = False) -> Any:
    """Bootstrap completo: descubre y registra tasks + side-effects + modules.

    Args:
        registry: Instancia de TaskRegistry. Si None, crea una default.
        se_engine: Instancia de SideEffectEngine. Si None, crea una default.
        separated: Si True, devuelve (domain_registry, system_registry, se_engine).
                   Si False (default), devuelve (registry, se_engine) para backward compatibility.

    Returns:
        Si separated=False: Tuple de (registry, se_engine).
        Si separated=True: Tuple de (domain_registry, system_registry, se_engine).
    """
    tracer: Any = None
    if registry is None:
        from .infrastructure.task_registry import InMemoryTaskRegistry

        # Result pattern (convention-001): el core @safe nunca lanza; el
        # tracer es best-effort y su fallo queda loggeado, no silenciado.
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

    bootstrap_tasks(registry)
    bootstrap_side_effects(se_engine)
    # El ModuleRegistry es INDEPENDIENTE del TaskRegistry (ver docstring de
    # bootstrap_modules): registra los servicios DI y los módulos MCP de la
    # app. Pasarle el registry de tasks hacia que register_module fallara con
    # AttributeError — antes tragado por el except silencioso del gap
    # SPEC-005 — y los módulos MCP quedaban sin registrar por esta ruta.
    bootstrap_modules()

    # Composition root único (capa_rout.md D1, Gap 3): bootstrap() es el
    # constructor canónico del grafo — expone los registries que construye
    # al ModuleRegistry para DI. Los consumidores resuelven por
    # get_module_service("task_registry"/"system_task_registry"/
    # "side_effect_engine"), NUNCA construyendo registries vacíos a mano.
    # Se registran como INSTANCIAS (no lazy): si una factory lazy del
    # cold-start ya cacheó otro registry en _services, este SOBRESCRIBE con
    # el canónico del bootstrap actual — un solo registry en producción.
    try:
        mr = _module_registry_singleton()
        mr.register_service("task_registry", registry)
        mr.register_service("side_effect_engine", se_engine)
        mr.register_service("system_task_registry", registry)
    except Exception as e:
        logger.warning(
            "bootstrap: no se pudieron exponer los registries al ModuleRegistry (%s: %s)",
            type(e).__name__, e,
        )

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


# Registro de módulos de la APP (singleton por proceso). Se pobla la primera vez
# que bootstrap_modules() corre sin registry. Los features lo usan para resolver
# servicios de libs por clave (DI / IoC) sin importar internos.
_APP_MODULE_REGISTRY: list[Any] = [None]


def _module_registry_singleton() -> Any:
    """Devuelve el ModuleRegistry de la app, construyéndolo la primera vez."""
    return bootstrap_modules()


def reset_module_registry() -> None:
    """Invalida el singleton del ModuleRegistry (hot reload).

    El reloader (infrastructure/reloader.py) la invoca tras recargar modulos
    de features/ para que el container DI y el catálogo de módulos MCP se
    reconstruyan (capa_rout.md D1: un solo composition root, sin caches
    privados que queden stale).
    """
    _APP_MODULE_REGISTRY[0] = None


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

    SINGLETON ESTABLE (capa_rout.md D1): llamado sin registry, devuelve el
    MISMO ModuleRegistry de la app (_APP_MODULE_REGISTRY) para todos los
    consumidores — get_module_service, mcp_adapter, tool_catalog y
    bootstrap(). Antes cada llamada reconstruía un registry nuevo (el
    singleton se pisaba en cada bootstrap), por lo que el catálogo se
    componía en 3 sitios que podían divergir.

    IMPORTANTE: este registry es independiente del TaskRegistry. Recibe un
    IModuleRegistry opcional (o el singleton). Nunca reuses el registry de
    tasks aquí: los servicios DI y los módulos composite solo viven en el
    ModuleRegistry. reset_module_registry() invalida el singleton (hot reload).

    Los features resuelven servicios por clave lógica (get_service) en vez de
    importar implementaciones concretas de libs/.
    """
    from .infrastructure.module_registry import InMemoryModuleRegistry

    if registry is not None:
        # Registry inyectado (tests/alternativas): se le registran los
        # módulos MCP y se devuelve tal cual, sin tocar el singleton.
        _discover_mcp_modules(registry)
        return registry
    if _APP_MODULE_REGISTRY[0] is None:
        module_registry = InMemoryModuleRegistry()
        _register_libs_services(module_registry)
        _APP_MODULE_REGISTRY[0] = module_registry
        _discover_mcp_modules(module_registry)
    return _APP_MODULE_REGISTRY[0]


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
    # lazy=True: el composition de use cases se resuelve UNA vez y se cachea;
    # los handlers lo usan sin params (a diferencia de cognitive_searcher/
    # prompt_manager que se parametrizan por proyecto en cada llamada).
    registry.register_service(
        "workflow",
        _build_workflow_services,
        lazy=True,
    )
    # Composition root del motor (capa_rout.md D1): el TaskRegistry y el
    # SideEffectEngine son dependencias del workflow. Se registran como
    # servicios lazy (bootstrap_tasks/bootstrap_side_effects) para que
    # _build_workflow_services los inyecte en los use cases. ANTES esto
    # quedaba vacío en producción (OrchestrationService() default creaba
    # DomainTaskRegistry sin tasks) y el fallback silencioso de
    # SubmitDataUseCase._execute_task hacía que el flujo CLI/MCP avanzara
    # sin ejecutar las actions reales (plan_sprint, spec_task, ...).
    registry.register_service(
        "task_registry",
        _build_task_registry,
        lazy=True,
    )
    registry.register_service(
        "side_effect_engine",
        _build_side_effect_engine,
        lazy=True,
    )


def _build_task_registry() -> Any:
    """Factory del TaskRegistry poblado (bootstrap_tasks).

    Composition root único (capa_rout.md D1): todo código que necesite un
    TaskRegistry lo obtiene de bootstrap() o de get_module_service("task_registry")
    — NUNCA instanciando InMemoryTaskRegistry()/DomainTaskRegistry() directo
    (orchestrator.py:101 creaba uno vacío por default).
    """
    from .infrastructure.task_registry import InMemoryTaskRegistry

    tracer_outcome = _build_tracer_core()
    tracer = tracer_outcome.value_or(None) if is_successful(tracer_outcome) else None
    registry = InMemoryTaskRegistry(tracer=tracer)
    bootstrap_tasks(registry)
    return registry


def _build_side_effect_engine() -> Any:
    """Factory del SideEffectEngine poblado (bootstrap_side_effects)."""
    from .infrastructure.side_effects import SideEffectEngine

    tracer_outcome = _build_tracer_core()
    tracer = tracer_outcome.value_or(None) if is_successful(tracer_outcome) else None
    se_engine = SideEffectEngine(tracer=tracer)
    bootstrap_side_effects(se_engine)
    return se_engine


def _build_workflow_services() -> Any:
    """Factory del composition de operaciones del workflow (submit/transition/status/prepare).

    Composition root único (capa_rout.md D1): los handlers (CLI/MCP) resuelven
    las 4 operaciones via get_module_service("workflow"), NUNCA importando use
    cases sueltos ni instanciando WorkflowRepository()/WorkflowStateRepository()/
    ScriptRunner() a mano por handler (antes: handle_submit/handle_transition
    con composición inline y status/prepare con imports directos). Si mañana
    cambia la firma de los use cases o los repos default, solo se toca esta
    factory.

    NOTA dirección de dependencia (única excepción engine→cli_interface):
    status()/prepare() delegan en los orquestadores cross-feature de
    cli_interface.application.project_use_cases (su docstring: "belong in
    cli_interface because they are cross-feature orchestrators"). El
    composition root es la única capa que conoce el grafo completo — mismo
    patrón lazy que engine/mcp.py usa con los handlers. NO mover esos imports:
    sería invertir la dirección correcta.

    NOTA cold-start: la PRIMERA resolución del servicio dispara
    bootstrap_modules() completo (registra todos los mcp.py) + los use cases
    CLI; tras eso queda cacheado (lazy=True).

    submit()/transition() construyen una instancia FRESCA del
    OrchestrationService por llamada: SubmitDataUseCase/TransitionUseCase
    (lazy internos del orchestrator) acumulan self.events durante execute()
    (y _flush_events no la limpia) — compartir una misma instancia singleton
    entre requests haría crecer la lista sin límite y, si alguien cableara
    event_log/event_dispatcher, re-despacharía eventos de submits previos.
    Los repos (estatales solo por chain_dir) SÍ son compartidos.

    Gap 2 (composition root real): ANTES esto construía SubmitDataUseCase/
    TransitionUseCase sueltos con composición a mano. AHORA construye el
    OrchestrationService COMPLETO (gates required_tools, side effects,
    block resolver, traces) inyectando el TaskRegistry y el SideEffectEngine
    del DI — el camino CLI/MCP ejecuta el mismo engine que WorkflowAPI.
    """
    from types import SimpleNamespace

    from features.engine.application.orchestrator import OrchestrationService
    from features.engine.infrastructure.repositories import (
        WorkflowRepository,
        WorkflowStateRepository,
        ScriptRunner,
    )

    tracer = _build_tracer_core().value_or(None)
    workflow_repo = WorkflowRepository()
    state_repo = WorkflowStateRepository()
    script_runner = ScriptRunner()

    # Composition root del motor (capa_rout.md D1): el OrchestrationService
    # recibe el TaskRegistry y el SideEffectEngine poblados por bootstrap.
    # ANTES estos quedaban sin inyectar: el submit CLI/MCP avanzaba SIN
    # ejecutar las actions reales (plan_sprint, spec_task, ...) — bug C2.
    task_registry = _module_registry_singleton().get_service("task_registry")
    side_effect_engine = _module_registry_singleton().get_service("side_effect_engine")

    def _new_orch() -> OrchestrationService:
        """Instancia FRESCA del orchestrator (events acumulados, ver docstring)."""
        return OrchestrationService(
            workflow_repo=workflow_repo,
            state_repo=state_repo,
            script_runner=script_runner,
            task_registry=task_registry,
            side_effect_engine=side_effect_engine,
            tracer=tracer,
        )

    def _submit(chain_dir, fields=None, **kwargs):
        from libs.shared_domain import ExecutionContext

        ctx = ExecutionContext.create(
            session_id=str(chain_dir).split("/")[-1],
            chain_dir=chain_dir,
            task_id=kwargs.pop("task_id", "") or "",
        )
        return _new_orch().submit(ctx, fields=fields or {}, **kwargs)

    def _transition(chain_dir, gate_name="", **kwargs):
        from libs.shared_domain import ExecutionContext

        ctx = ExecutionContext.create(
            session_id=str(chain_dir).split("/")[-1],
            chain_dir=chain_dir,
            task_id=kwargs.pop("task_id", "") or "",
        )
        return _new_orch().transition(
            ctx, gate_name, reason=kwargs.get("reason", ""),
            skip_preconditions=kwargs.get("skip_preconditions", False),
        )

    def _status(project_id, repo_path, sprint_id=""):
        # Los orquestadores cross-feature de status viven en cli_interface
        # (project_use_cases.py: "belong in cli_interface because they are
        # cross-feature orchestrators"). El composition root es la única capa
        # que conoce el grafo completo (mismo patrón lazy que engine/mcp.py
        # usa con los handlers): los handlers NO importan use cases sueltos.
        from features.cli_interface.application.project_use_cases import (
            CheckHealthUseCase,
        )

        return CheckHealthUseCase.execute(
            project_id=project_id, repo_path=repo_path, sprint_id=sprint_id
        )

    def _prepare(project_path, args_flags):
        from features.cli_interface.application.project_use_cases import (
            PrepareProjectUseCase,
        )

        return PrepareProjectUseCase.execute(
            project_path=project_path, args_flags=args_flags
        )

    return SimpleNamespace(
        submit=_submit,
        transition=_transition,
        status=_status,
        prepare=_prepare,
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
    """Busca features/*/mcp.py y registra su register_module.

    Result pattern (convention-001): por modulo, el core @safe nunca lanza;
    ante fallo de registro se loggea warning sin romper el bootstrap. Los
    features sin mcp.py (task_execution, prompts) no son un fallo: solo se
    intenta registrar cuando el archivo existe.
    """
    import importlib
    import pkgutil

    features_pkg = importlib.import_module("features")
    features_dir = os.path.dirname(os.path.abspath(features_pkg.__file__))
    for _, mod_name, is_pkg in pkgutil.iter_modules(features_pkg.__path__):
        if mod_name.startswith("_"):
            continue
        mcp_path = f"features.{mod_name}.mcp"
        if not os.path.exists(os.path.join(features_dir, mod_name, "mcp.py")):
            continue
        outcome = _register_mcp_module_core(mcp_path, registry)
        if not is_successful(outcome):
            error = outcome.failure()
            logger.warning(
                "bootstrap: no se pudo registrar el modulo MCP '%s' (%s: %s)",
                mcp_path, type(error).__name__, error,
            )


@safe
def _register_mcp_module_core(mcp_path: str, registry: Any) -> None:
    """Core puro: importa features/<mod>.mcp y llama register_module (puede lanzar)."""
    mod = importlib.import_module(mcp_path)
    register_fn = getattr(mod, "register_module", None)
    if register_fn:
        register_fn(registry)
