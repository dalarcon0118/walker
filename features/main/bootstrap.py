"""features/main/bootstrap.py — Composition root final de Walker.

Arma el grafo completo:
- core/engine: infraestructura de workflow
- delta_tracking: modelo de desarrollo
- code_analysis: análisis de código
- cognitive_knowledge: conocimiento
- event_tracing: trazabilidad
- project: gestión de proyectos

Entry points (CLI, MCP, WorkflowAPI) consumen esta composición.
"""

from __future__ import annotations

import importlib
import logging
import os
import pkgutil
from dataclasses import dataclass, field
from typing import Any

from returns.pipeline import is_successful
from returns.result import safe

from core.engine.application.orchestrator import OrchestrationService
from core.engine.domain.models import WorkflowState
from core.engine.infrastructure.module_registry import InMemoryModuleRegistry
from core.engine.infrastructure.repositories import WorkflowRepository, WorkflowStateRepository, ScriptRunner
from core.engine.infrastructure.side_effects import SideEffectEngine
from core.engine.infrastructure.task_registry import InMemoryTaskRegistry

logger = logging.getLogger(__name__)


@dataclass
class AppComposition:
    """Composición final de la aplicación."""

    workflow: OrchestrationService
    task_registry: InMemoryTaskRegistry
    side_effect_engine: SideEffectEngine
    workflow_graph: Any = None
    module_registry: Any = None
    _chain_dir: str = ""


_COMPOSITION_SINGLETON: AppComposition | None = None


def reset_composition() -> None:
    """Resetea el singleton (solo para tests) y el service locator de core/engine."""
    global _COMPOSITION_SINGLETON
    _COMPOSITION_SINGLETON = None
    from core.engine.bootstrap import reset_module_registry

    reset_module_registry()


def _module_registry_singleton() -> Any:
    """Devuelve el ModuleRegistry de la app, construyéndolo la primera vez."""
    app = compose_app()
    if app.module_registry is None:
        raise RuntimeError("module_registry no inicializado en AppComposition")
    return app.module_registry


def get_module_service(name: str) -> Any:
    """Resuelve un servicio compartido por clave lógica (DI).

    Es el SSOT de resolución de servicios: delega en el ModuleRegistry
    del composition root único (features/main). Los callers internos de
    core/engine usan el facade neutro core.engine.bootstrap.get_module_service
    (service locator inyectado en compose_app) para no importar features/main.
    """
    return _module_registry_singleton().get_service(name)


def _build_tracer_core() -> Any:
    """Tracer best-effort (no bloquea el composition root)."""
    try:
        from core.engine.infrastructure.trace_log import TraceLogger
        return TraceLogger()
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


def _discover_and_register_mcp_modules(registry: Any) -> None:
    """Escanea features/*/mcp.py y registra cada register_module(registry).

    ADR-045: el discovery de MCP modules es responsabilidad EXCLUSIVA de
    features/main y solo escanea features/ (nunca core/). El módulo MCP del
    engine (status/submit/transition/prepare/list/help) se registra
    EXPLÍCITAMENTE en compose_app() — features/main conoce core/engine.
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

    ANTES esto vivía en core/engine/bootstrap.py (_register_libs_services) —
    compitiendo con la inyección directa de features/main (desvío 3). El
    discovery de libs services es responsabilidad EXCLUSIVA de features/main.
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
        importlib.import_module("shared.core.path_resolver"),
    )
    registry.register_service(
        "mcp_server",
        lambda: importlib.import_module("shared.core.mcp_server").McpServer(),
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
    from shared.core.cognitive_graph import CognitiveGraph

    return CognitiveGraph(project_root or ".")


def _build_searcher(project_root: str = "") -> Any:
    """Factory del lector CQRS sobre CognitiveGraph (capa de lectura).

    Composición (único lugar donde se arma el grafo):
        CognitiveGraph(project_root) → CognitiveGraphSearcher(graph)

    El Searcher recibe el grafo inyectado (DI): no sabe cómo se construye.
    Se resuelve por clave "cognitive_searcher" en el registry.
    """
    from shared.core.cognitive_graph import CognitiveGraph
    from shared.core.cognitive_searcher import CognitiveGraphSearcher

    graph = CognitiveGraph(project_root or ".")
    return CognitiveGraphSearcher(graph)


def _build_prompt_manager(chain_dir: str = "") -> Any:
    """Factory del module service de prompts (DI, clave "prompt_manager").

    Devuelve un PromptManager (IPromptManager) para un chain_dir dado —
    igual que "cognitive_searcher" parametriza por project_root.
    """
    from features.main.prompts import PromptManager

    return PromptManager(chain_dir=chain_dir or os.getcwd(), project_dir=None)


def _build_workflow_facade(orch: OrchestrationService) -> Any:
    """Facade del servicio DI "workflow" (composition root único, capa_rout.md D1).

    Expone las 4 operaciones del CLI/MCP: submit, transition, status, prepare.
    - submit/transition delegan en el OrchestrationService del composition
      root (registries poblados con tasks reales — bug C2 resuelto).
    - status/prepare delegan en los orquestadores cross-feature de
      cli_interface.application.project_use_cases ("belong in cli_interface
      because they are cross-feature orchestrators").
    """

    def _make_ctx(chain_dir: str, **kwargs) -> Any:
        from shared.common.shared_domain import ExecutionContext

        return ExecutionContext.create(
            session_id=str(chain_dir).split("/")[-1],
            chain_dir=chain_dir,
            task_id=kwargs.pop("task_id", "") or "",
        )

    def _submit(chain_dir, fields=None, **kwargs):
        ctx = _make_ctx(chain_dir, **kwargs)
        return orch.submit(ctx, fields=fields or {}, **kwargs)

    def _transition(chain_dir, gate_name="", **kwargs):
        ctx = _make_ctx(chain_dir, **kwargs)
        return orch.transition(
            ctx,
            gate_name,
            reason=kwargs.get("reason", ""),
            skip_preconditions=kwargs.get("skip_preconditions", False),
        )

    def _status(project_id, repo_path, sprint_id=""):
        from features.main.cli_interface.application.project_use_cases import (
            CheckHealthUseCase,
        )

        return CheckHealthUseCase.execute(
            project_id=project_id, repo_path=repo_path, sprint_id=sprint_id
        )

    def _prepare(project_path, args_flags):
        from features.main.cli_interface.application.project_use_cases import (
            PrepareProjectUseCase,
        )

        return PrepareProjectUseCase.execute(
            project_path=project_path, args_flags=args_flags
        )

    from types import SimpleNamespace

    return SimpleNamespace(
        submit=_submit,
        transition=_transition,
        status=_status,
        prepare=_prepare,
    )


def compose_app(chain_dir: str = "") -> AppComposition:
    """Composition root final de Walker.

    Construye y retorna la aplicación completa:
    - core/engine: OrchestrationService (infraestructura)
    - features/*: tasks, side effects, providers, MCP modules
    - libs/*: servicios DI

    SINGLETON: devuelve la misma instancia de AppComposition en llamadas
    sucesivas con el mismo chain_dir.
    """
    global _COMPOSITION_SINGLETON
    if _COMPOSITION_SINGLETON is not None:
        return _COMPOSITION_SINGLETON

    # 1. Infraestructura base del engine
    tracer = _build_tracer_core()
    registry = InMemoryTaskRegistry(tracer=tracer)
    se_engine = SideEffectEngine(tracer=tracer)
    workflow_repo = WorkflowRepository()
    if chain_dir:
        workflow_repo.load(chain_dir)

    # 1a. ContextProviderRegistry completo: base del engine (task, state,
    # diff, test_results) + providers de negocio de features (business, ckg,
    # cpg, delta, project, reflection, trace). ADR-045: el composition root
    # es el ÚNICO que arma este registry — el engine no conoce features/.
    from core.engine.bootstrap import build_context_registry

    context_registry = build_context_registry()
    _discover_and_register_providers(context_registry)

    orch = OrchestrationService(
        workflow_repo=workflow_repo,
        state_repo=WorkflowStateRepository(),
        script_runner=ScriptRunner(),
        task_registry=registry,
        side_effect_engine=se_engine,
        tracer=tracer,
        context_registry=context_registry,
    )

    # 2. ModuleRegistry unificado (composition root final, ADR-045)
    module_registry = InMemoryModuleRegistry()

    # 3. Discovery y registro automático de componentes
    _register_libs_services(module_registry)
    _discover_and_register_mcp_modules(module_registry)

    # 3a. Módulo MCP del engine: features/main lo registra EXPLÍCITAMENTE
    # (ADR-045 — el discovery automático solo escanea features/, no core/).
    from core.engine.mcp import register_module as _register_engine_mcp

    _register_engine_mcp(module_registry)

    # 3b. Tasks y side effects de dominio (features/*/)
    _discover_and_register_tasks(registry)
    _discover_and_register_side_effects(se_engine)

    # 4. Exponer las instancias pobladas como servicios DI para que
    # get_module_service() las resuelva desde features/main.
    module_registry.register_service("task_registry", registry)
    module_registry.register_service("side_effect_engine", se_engine)
    module_registry.register_service("workflow", _build_workflow_facade(orch))

    # 5. Service locator neutro de core/engine (capa_rout.md D1): los callers
    # internos de core/engine resuelven servicios por get_module_service()
    # sin importar features/main. El resolutor lo inyecta el composition root.
    from core.engine.bootstrap import set_service_locator

    set_service_locator(module_registry.get_service)

    _COMPOSITION_SINGLETON = AppComposition(
        workflow=orch,
        task_registry=registry,
        side_effect_engine=se_engine,
        module_registry=module_registry,
    )
    return _COMPOSITION_SINGLETON
