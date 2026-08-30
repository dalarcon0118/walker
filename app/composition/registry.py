"""app/composition/registry.py — Registro declarativo idempotente (estilo NestJS).

La composición es DECLARATIVA (ADR-066/067): cada módulo se AUTOREGISTRA vía
ModuleManifest (`app/module.py` declara los providers del host como factories
lazy que inyectan por id+contrato desde el ModuleRegistry único del proceso).
`register_all` es un punto DELGADO que solo:
  1. registra los servicios DI de libs
  2. registra los módulos MCP
  3. aplica los manifests (autoregistro; app.module registra tasks/side-effects
     y los providers engine/workflow/action_catalog/task_registry/...)
  4. puebla el context_registry con los providers de contexto de features
  5. resuelve `engine`/`workflow` por id+contrato (sin instanciar a mano)

Las factories lazy se construyen al primer get_service() (singleton).
NO hay glue manual ni monkeypatch: el container solo importa módulos y resuelve.
"""
from __future__ import annotations

import importlib
import logging
import os
import pkgutil
from typing import Any

from returns.pipeline import is_successful
from returns.result import safe

from shared.libs.module_system.registry import InMemoryModuleRegistry
from shared.core.engine.infrastructure.repositories import WorkflowRepository
from shared.core.engine.infrastructure.side_effects import SideEffectEngine
from shared.core.engine.infrastructure.task_registry import InMemoryTaskRegistry
from shared.core.engine.application.orchestrator import OrchestrationService

logger = logging.getLogger(__name__)


# ── Registro declarativo idempotente (ADR-046 Fase 2) ─────────────────────────


def register_all(
    *,
    context_registry: Any = None,
    task_registry: Any = None,
    side_effect_engine: Any = None,
    module_registry: "InMemoryModuleRegistry | None" = None,
    tracer: Any = None,
) -> tuple[Any, Any]:
    """Composición DECLARATIVA (estilo NestJS, ADR-066/067).

    El wiring NO se hace a mano: cada módulo se AUTOREGISTRA vía ModuleManifest.
    `app.module` declara SOLO los INGREDIENTES del host (workflow_store, actions,
    task_registry, side_effect_engine, tracer, context_registry) como factories
    lazy que inyectan por id+contrato desde el registry. Este punto es el **MAIN**
    de composición (entry point de la app): la INSTANCIA del engine se construye
    AQUÍ, explícitamente, con todos sus parámetros desde los providers (patrón
    servidor) — NO es un provider del módulo. Pasos:
      1. registra los servicios DI de libs
      2. registra los módulos MCP
      3. aplica los manifests (cada uno se autoregistra; app.module registra
         tasks/side-effects y los ingredients)
      4. puebla el context_registry con los providers de contexto de features
      5. construye la instancia del engine (`OrchestrationService(workflow_store=...,
         actions=..., ...)`) y la registra por id + el facade

    Devuelve (engine, workflow_facade) para que AppContainer construya el shell.
    """
    from shared.libs.module_system.registry import (
        module_registry as _default_module_registry,
    )

    module_registry = module_registry or _default_module_registry
    _register_libs_services(module_registry)
    _discover_and_register_mcp_modules(module_registry)
    _register_module_manifests(module_registry)
    _discover_and_register_providers(module_registry.get_service("context_registry"))

    # ── MAIN: instancia del engine construida EXPLÍCITAMENTE (patrón servidor) ──
    # El engine recibe TODOS sus parámetros desde los providers del registry.
    # No es un provider del módulo: `main` crea la instancia y la registra por id.
    from shared.core.engine.application.orchestrator import OrchestrationService
    from shared.core.engine.domain.ports import IWorkflowIO

    engine = OrchestrationService(
        workflow_store=module_registry.get_service("workflow_store"),
        actions=module_registry.get_service("action_catalog"),
        workflow_io=module_registry.resolve_port(IWorkflowIO),
        tracer=module_registry.get_service("tracer"),
        context_registry=module_registry.get_service("context_registry"),
    )
    module_registry.register_service("engine", engine)
    workflow_facade = _build_workflow_facade(engine)
    module_registry.register_service("workflow", workflow_facade)
    return engine, workflow_facade


def _register_module_manifests(registry: Any) -> None:
    """Registra los ModuleManifest declarativos de las 5 capas (ADR-066 F5).

    Cada ``*/module.py`` declara su ``MODULE``; el registry aplica adapters/
    providers y valida aciclicidad. Los puertos del engine se resuelven luego
    con ``resolve_port()`` — ya no hay instanciación manual de adaptadores.
    """
    import importlib

    module_paths = [
        "features.code_analysis.module",
        "features.project.module",
        "features.delta_tracking.module",
        "features.main.prompts.module",
        "features.cognitive_knowledge.module",
        "features.event_tracing.module",
        "tasks.module",
        "protocols.mcp.module",
        "protocols.cli.cli_interface.module",
        "protocols.rest.module",
        "app.orchestration.module",
        "app.module",
    ]
    for path in module_paths:
        try:
            mod = importlib.import_module(path)
            manifest = getattr(mod, "MODULE", None)
            if manifest is not None:
                registry.register_manifest(manifest)
        except Exception as exc:  # pragma: no cover - defensivo
            logger.warning("main: no se pudo registrar manifest %s (%s)", path, exc)


def _discover_and_register_tasks(registry: InMemoryTaskRegistry) -> None:
    """Escanea features/*/tasks/ y registra cada register_*_tasks(registry)."""
    for pkg_name, pkg_dir in _feature_subpackages():
        tasks_dir = os.path.join(pkg_dir, "tasks")
        if not os.path.isdir(tasks_dir):
            continue
        module_path = f"{pkg_name}.tasks"
        try:
            mod = importlib.import_module(module_path)
            register_fn = getattr(mod, f"register_{pkg_name.split('.')[-1]}_tasks", None)
            if register_fn:
                register_fn(registry)
                logger.debug("main: tasks registradas desde %s", module_path)
        except Exception as exc:
            logger.warning("main: no se pudieron registrar tasks de %s (%s)", module_path, exc)


def _discover_and_register_side_effects(engine: SideEffectEngine) -> None:
    """Escanea features/*/side_effects/ y registra cada register_*_side_effects(engine)."""
    for pkg_name, pkg_dir in _feature_subpackages():
        se_dir = os.path.join(pkg_dir, "side_effects")
        if not os.path.isdir(se_dir):
            continue
        module_path = f"{pkg_name}.side_effects"
        try:
            mod = importlib.import_module(module_path)
            register_fn = getattr(mod, f"register_{pkg_name.split('.')[-1]}_side_effects", None)
            if register_fn:
                register_fn(engine)
                logger.debug("main: side effects registrados desde %s", module_path)
        except Exception as exc:
            logger.warning("main: no se pudieron registrar side effects de %s (%s)", module_path, exc)


# ── Discovery (helpers del registro) ─────────────────────────────────────────


def _build_tracer_core() -> Any:
    """Tracer best-effort (no bloquea el composition root)."""
    try:
        from shared.core.engine.infrastructure.trace_log import TraceLogger
        from app import path_resolver as _pr
        return TraceLogger(path_resolver=_pr, cognitive_graph_factory=_build_graph_from_path)
    except Exception as exc:
        logger.debug("main: tracer no disponible (%s)", exc)
        return None


def _feature_subpackages() -> list[tuple[str, str]]:
    """Devuelve [(pkg_name, abs_dir)] de los features registrables.

    Cubre DOS niveles (task_execution/prompts viven bajo features/main):
      - features/*                       (delta_tracking, code_analysis, ...)
      - features/main/*                  (task_execution, prompts — subcomponentes)
    """
    found: list[tuple[str, str]] = []
    features_pkg = importlib.import_module("features")
    features_dir = os.path.dirname(os.path.abspath(features_pkg.__file__))

    for _, mod_name, is_pkg in pkgutil.iter_modules(features_pkg.__path__):
        if not is_pkg or mod_name.startswith("_"):
            continue
        found.append((f"features.{mod_name}", os.path.join(features_dir, mod_name)))

    # Subcomponentes de features/main (task_execution, prompts, ...)
    main_pkg_path = os.path.join(features_dir, "main")
    if os.path.isdir(main_pkg_path):
        for _, sub_name, is_pkg in pkgutil.iter_modules([main_pkg_path]):
            if not is_pkg or sub_name.startswith("_"):
                continue
            found.append(
                (f"features.main.{sub_name}", os.path.join(main_pkg_path, sub_name))
            )
    return found


def _discover_and_register_providers(registry: Any) -> None:
    """Escanea features/*/context/providers.py y registra cada register_providers(registry)."""
    for pkg_name, pkg_dir in _feature_subpackages():
        providers_path = os.path.join(pkg_dir, "context", "providers.py")
        if not os.path.isfile(providers_path):
            continue
        module_path = f"{pkg_name}.context.providers"
        try:
            mod = importlib.import_module(module_path)
            register_fn = getattr(mod, "register_providers", None)
            if register_fn:
                register_fn(registry)
                logger.debug("main: providers registrados desde %s", module_path)
        except Exception as exc:
            logger.warning("main: no se pudieron registrar providers de %s (%s)", module_path, exc)

    # Registro explícito de providers en subdirectorios de task_execution
    # (features/main/task_execution/context/providers.py no está en el path estándar).
    try:
        mod = importlib.import_module("tasks.context.providers")
        register_fn = getattr(mod, "register_providers", None)
        if register_fn:
            register_fn(registry)
            logger.debug("main: providers registrados desde tasks.context.providers")
    except Exception as exc:
        logger.warning("main: no se pudo registrar task_intelligence provider (%s)", exc)


def _discover_and_register_mcp_modules(registry: Any) -> None:
    """Escanea features/*/mcp.py y registra cada register_module(registry).

    ADR-045: el discovery de MCP modules es responsabilidad EXCLUSIVA de
    features/main y solo escanea features/ (nunca core/). El módulo MCP del
    engine (status/submit/transition/prepare/list/help) se registra
    EXPLÍCITAMENTE en register_all() — features/main conoce core/.
    """
    for base in ["features"]:
        pkg = importlib.import_module(base)
        base_dir = os.path.dirname(os.path.abspath(pkg.__file__))

        for _, mod_name, is_pkg in pkgutil.iter_modules(pkg.__path__):
            if not is_pkg or mod_name.startswith("_"):
                continue
            mcp_path = os.path.join(base_dir, mod_name, "mcp.py")
            if not os.path.isfile(mcp_path):
                continue
            module_path = f"{base}.{mod_name}.mcp"
            outcome = _register_mcp_module_core(module_path, registry)
            if not is_successful(outcome):
                error = outcome.failure()
                logger.warning(
                    "main: no se pudo registrar el modulo MCP '%s' (%s: %s)",
                    module_path,
                    type(error).__name__,
                    error,
                )


@safe
def _register_mcp_module_core(mcp_path: str, registry: Any):
    """Core puro: importa features/<mod>.mcp y llama register_module (puede lanzar)."""
    mod = importlib.import_module(mcp_path)
    register_fn = getattr(mod, "register_module", None)
    if register_fn:
        register_fn(registry)


def _register_libs_services(registry: Any) -> None:
    """Registra servicios transversales de shared/ como adapters DI (claves lógicas).

    ADR-045: los features reciben estos servicios inyectados por clave en vez
    de depender de la estructura interna de shared/ (inversión de control).
    Las factories lazy se construyen al primer get_service() (singleton).
    """
    registry.register_service("module_registry", registry)
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
        _build_path_resolver,
        lazy=True,
    )
    registry.register_service(
        "project_service",
        _build_project_service,
        lazy=True,
    )
    registry.register_service(
        "delta_spring_service",
        _build_delta_spring_service,
        lazy=True,
    )
    registry.register_service(
        "delta_conflict_resolver",
        _build_delta_conflict_resolver,
        lazy=True,
    )
    registry.register_service(
        "mcp_server",
        lambda: importlib.import_module("protocols.mcp.mcp_server").McpServer(),
        lazy=True,
    )
    registry.register_service(
        "prompt_manager",
        _build_prompt_manager,
    )
    # ── Application Services de features expuestos al engine (ADR-051 G5/G6) ──
    # El engine consume features SOLO por service locator (inversión de
    # dependencia): factories que retornan el callable/clase del servicio.
    registry.register_service(
        "sprint_service",
        _build_sprint_factory,
        lazy=True,
    )
    registry.register_service(
        "ckg_service",
        _build_ckg_factory,
        lazy=True,
    )
    registry.register_service(
        "code_analysis",
        _build_code_analysis,
        lazy=True,
    )
    registry.register_service(
        "trace_service",
        _build_trace_service,
        lazy=True,
    )
    registry.register_service(
        "trace_reflection",
        _build_trace_reflection,
        lazy=True,
    )
    registry.register_service(
        "gate_briefing",
        _build_gate_briefing,
        lazy=True,
    )
    registry.register_service(
        "prompt_collector",
        _build_prompt_collector,
        lazy=True,
    )
    registry.register_service(
        "cli_handlers",
        _build_cli_handlers,
        lazy=True,
    )
    registry.register_service(
        "cli_request",
        _build_cli_request,
        lazy=True,
    )
    # ── Services de app/orchestration/ (capa cross-feature, ADR-051 §2.6) ──
    registry.register_service(
        "prepare_project_service",
        _build_prepare_project_service,
        lazy=True,
    )
    registry.register_service(
        "check_health_service",
        _build_check_health_service,
        lazy=True,
    )
    registry.register_service(
        "intelligence_service",
        _build_intelligence_service,
        lazy=True,
    )
    registry.register_service(
        "adr_orchestrator_service",
        _build_adr_orchestrator_service,
        lazy=True,
    )


def _build_path_resolver() -> Any:
    """Factory del module service de paths (DI, clave "path_resolver").

    Envuelve el módulo app.path_resolver (utilidad de paths del walker, host)
    en un proxy de instancia para cumplir la regla DI: factories retornan
    instancias, no módulos.
    """
    mod = importlib.import_module("app.path_resolver")

    class _Proxy:
        def __getattr__(self, name: str) -> Any:
            return getattr(mod, name)

    return _Proxy()


def _build_graph(conn: "MemoryConnection | str") -> Any:
    """Factory del orquestador CognitiveGraph (escritura + búsqueda).

    ADR-WALKER-061 (F4): firma canónica Callable[[MemoryConnection], Any].
    El backend (FileBackend) y el CognitiveGraph se construyen a partir de la
    conexión resuelta (config), no de un path crudo. Un DbBackend futuro se
    enchufa por config sin tocar este factory.

    Por backward-compat (callers del engine que pasan un path str) también
    acepta un str, que se resuelve a MemoryConnection vía memory_data_dir.
    """
    from shared.libs.cognitive_graph import CognitiveGraph
    from shared.libs.cognitive_graph.connection import MemoryConnection
    from shared.libs.cognitive_graph.file_backend import FileBackend
    from app.cognitive_registry import (
        register_cognitive_indexes,
        memory_data_dir,
    )

    if isinstance(conn, str):
        conn = MemoryConnection(uri="file://" + memory_data_dir(conn))
    # ADR-WALKER-061 (F6): el adapter se resuelve por config (conn.backend),
    # no hardcodeado. FileBackend hoy; DbBackend mañana vía env.
    from shared.libs.cognitive_graph.backends import resolve_storage_backend
    from app.memory_schemas import register_default_memory_indexes
    backend = resolve_storage_backend(conn)
    graph = CognitiveGraph(
        backend=backend, conn=conn, register_defaults=register_default_memory_indexes
    )
    register_cognitive_indexes(graph)
    return graph


def _build_graph_from_path(chain_dir: str = "") -> Any:
    """Wrapper path->MemoryConnection para TraceLogger (shared/core/engine).

    TraceLogger invoca el factory con un path (no conoce MemoryConnection);
    este wrapper resuelve el data_dir y construye la conexión desde CONFIG
    (env). Vive en features/main (no en shared/core/engine) para no acoplar
    el engine a features (ADR-WALKER-045/058).
    """
    from app.cognitive_registry import memory_data_dir
    from shared.libs.cognitive_graph.config import load_memory_connection

    conn = load_memory_connection(memory_data_dir(chain_dir))
    return _build_graph(conn)


def _build_searcher(chain_dir: str = "") -> Any:
    """Factory del lector CQRS sobre CognitiveGraph (capa de lectura)."""
    from shared.libs.cognitive_graph import CognitiveGraph
    from shared.libs.cognitive_graph.searcher import CognitiveGraphSearcher
    from app.cognitive_registry import (
        register_cognitive_indexes,
        memory_data_dir,
    )
    from app.memory_schemas import register_default_memory_indexes

    graph = CognitiveGraph(memory_data_dir(chain_dir), register_defaults=register_default_memory_indexes)
    register_cognitive_indexes(graph)
    return CognitiveGraphSearcher(graph)


def _build_project_service() -> Any:
    """Factory del module service de proyecto (DI, clave "project_service").

    ADR-045: el engine resuelve ProjectService por service locator en vez de
    importar features.project en import-time (acoplamiento invertido).
    """
    from features.project.application.services.project_service import ProjectService

    return ProjectService()


def _build_delta_spring_service() -> Any:
    """Factory del module service de delta (DI, clave "delta_spring_service").

    ADR-045: expone la factory spring_service de delta_tracking como servicio
    DI para que el engine lo resuelva por service locator (no por import).
    """
    from features.delta_tracking.application.services.spring_service import SpringService

    return SpringService(project_root="")


def _build_sprint_factory() -> Any:
    """Factory del service de sprint (DI, clave "sprint_service").

    Expone la factory `spring_service(chain_dir=...|project_root=...)` de
    delta_tracking como callable DI: el engine la invoca con el contexto
    del chain_dir/project (patrón cognitive_searcher).
    """
    from features.delta_tracking import spring_service

    return spring_service


def _build_ckg_factory() -> Any:
    """Factory del service de CKG (DI, clave "ckg_service").

    Expone la factory `ckg_service(project_root=...)` de cognitive_knowledge
    como callable DI.
    """
    from features.cognitive_knowledge import ckg_service

    return ckg_service


def _build_code_analysis(chain_dir: str = "") -> Any:
    """Factory del Application Service de code_analysis (DI, clave "code_analysis").

    Nest-like (ADR-052/053): el service es inyectado por clave a los
    consumidores (adapter/tasks) — la única orquestación de la lógica del
    feature. `chain_dir` se resuelve al arranque/uso igual que los demás
    providers (patrón cognitive_graph).
    """
    from features.code_analysis.application.services.code_analysis_service import (
        CodeAnalysisService,
    )

    return CodeAnalysisService(chain_dir)


def _build_trace_service() -> Any:
    """Factory del Application Service de event_tracing (DI, clave "trace_service")."""
    from features.event_tracing.application.services.trace_service import TraceService

    return TraceService


def _build_trace_reflection() -> Any:
    """Factory del contexto de reflexión (DI, clave "trace_reflection")."""
    from features.event_tracing import build_reflection_context

    return build_reflection_context


def _build_gate_briefing() -> Any:
    """Factory del briefing de gates (DI, clave "gate_briefing")."""
    from features.main.prompts.gate_briefing import what_happened_from_state

    return what_happened_from_state


def _build_prompt_collector() -> Any:
    """Factory del collector de prompts (DI, clave "prompt_collector")."""
    from features.main.prompts.collector import collect_all

    return collect_all


def _build_cli_handlers() -> Any:
    """Factory de los handlers CLI del workflow (DI, clave "cli_handlers").

    El módulo MCP del engine (core/engine/mcp.py) delega en los handlers de
    cli_interface; main los inyecta por DI para invertir la dependencia
    (ADR-051 G5): core/engine no importa features.main.

    FIX ciclo de imports: la resolución es DINÁMICA (import_module) para no
    crear arista estática registry → handlers. Con el ImportFrom clásico, el
    grafo estático cerraba container → registry → handlers → container y el
    hot-reload topológico detectaba el SCC como ciclo.
    """
    import importlib

    handlers_mod = importlib.import_module(
        "protocols.cli.cli_interface.application.handlers"
    )
    return {
        "status": handlers_mod.handle_status,
        "submit": handlers_mod.handle_submit,
        "transition": handlers_mod.handle_transition,
        "prepare": handlers_mod.handle_prepare,
        "list": handlers_mod.handle_list_projects,
        "help": handlers_mod.handle_help,
    }


def _build_cli_request() -> Any:
    """Factory del contrato Request de cli_interface (DI, clave "cli_request")."""
    from protocols.cli.cli_interface.infrastructure.contracts import Request

    return Request


def _build_prepare_project_service() -> Any:
    """Factory del orquestador de prepare (DI, clave "prepare_project_service").

    app/orchestration/ es la capa cross-feature (ADR-051 §2.6); main la registra
    como servicio DI sin importarla en el engine.
    """
    from app.orchestration.prepare_project_service import PrepareProjectService

    return PrepareProjectService


def _build_check_health_service() -> Any:
    """Factory del orquestador de health (DI, clave "check_health_service")."""
    from app.orchestration.check_health_service import CheckHealthService

    return CheckHealthService


def _build_intelligence_service() -> Any:
    """Factory del orquestador de inteligencia cross-memoria (DI, clave
    "intelligence_service").

    Devuelve la factory `build_intelligence_service(chain_dir)` — el caller
    la invoca con el chain_dir (patrón cognitive_searcher).
    """
    from app.orchestration.intelligence_service import build_intelligence_service

    return build_intelligence_service


def _build_adr_orchestrator_service() -> Any:
    """Factory del orquestador de ADR (DI, clave "adr_orchestrator_service").

    app/orchestration/ es la capa cross-feature (ADR-051 §2.6); main la registra
    como servicio DI sin importarla en el engine.
    """
    from app.orchestration.adr_orchestrator_service import AdrOrchestratorService

    return AdrOrchestratorService


def _build_delta_conflict_resolver() -> Any:
    """Factory del module service de conflictos de delta (DI, clave
    "delta_conflict_resolver").

    ADR-045: expone ConflictResolver (detección de conflictos del workflow,
    vive en delta_tracking.domain) como servicio DI para que el engine lo
    resuelva por service locator — no por import directo al domain del feature.
    """
    from features.delta_tracking.domain.services.conflict_resolver import (
        ConflictResolver,
    )

    return ConflictResolver()


def _build_prompt_manager(chain_dir: str = "") -> Any:
    """Factory del module service de prompts (DI, clave "prompt_manager")."""
    from features.main.prompts import PromptManager

    return PromptManager(chain_dir=chain_dir or os.getcwd(), project_dir=None)


def _build_searcher_for_chain_dir(chain_dir: str) -> Any:
    """Implementa el puerto del ContextComposer (ADR-045): construye el
    searcher para un chain_dir.

    MAIN conoce features (ProjectService) — es quien resuelve project_root y
    construye el searcher. El engine recibe esto inyectado, no lo busca.
    """
    from features.project import ProjectService

    try:
        paths = ProjectService.resolve_paths(chain_dir)
        if not paths.project_root:
            return None
        return _build_searcher(str(paths.project_root))
    except Exception:
        return None


def _build_workflow_facade(orch: OrchestrationService) -> Any:
    """Facade del servicio DI "workflow" (composition root único, capa_rout.md D1).

    Expone las 4 operaciones del CLI/MCP: submit, transition, status, prepare.
    - submit/transition delegan en el OrchestrationService del composition
      root (registries poblados con tasks reales — bug C2 resuelto).
    - status/prepare delegan en los orquestadores cross-feature de
      cli_interface.application.orchestrators ("belong in cli_interface
      because they are cross-feature orchestrators").
    """
    from app.api.workflow_facade import WorkflowFacade

    return WorkflowFacade(orch)
