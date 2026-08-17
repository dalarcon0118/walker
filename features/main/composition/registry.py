"""features/main/composition/registry.py — Registro declarativo idempotente.

Consolida discovery de providers, MCP modules, tasks, side effects y servicios
DI en un único punto (register_all). Las factories lazy se construyen al
primer get_service() (singleton).
"""
from __future__ import annotations

import importlib
import logging
import os
import pkgutil
from typing import Any

from returns.pipeline import is_successful
from returns.result import safe

from core.engine.infrastructure.module_registry import InMemoryModuleRegistry
from core.engine.infrastructure.repositories import ScriptRunner, WorkflowRepository
from core.engine.infrastructure.side_effects import SideEffectEngine
from core.engine.infrastructure.task_registry import InMemoryTaskRegistry
from core.engine.application.orchestrator import OrchestrationService

logger = logging.getLogger(__name__)


# ── Registro declarativo idempotente (ADR-046 Fase 2) ─────────────────────────


def register_all(
    *,
    context_registry: Any,
    task_registry: InMemoryTaskRegistry,
    side_effect_engine: SideEffectEngine,
    module_registry: InMemoryModuleRegistry,
    workflow_facade: Any,
) -> None:
    """Registro declarativo idempotente: consolidación de los 4 discovery.

    Consolida _discover_and_register_providers/tasks/side_effects/mcp_modules
    del legacy en UN único punto de registro aditivo. Los duplicados se
    verifican ANTES de registrar (ContextProviderRegistry idempotente) —
    nunca se pisan providers ni se emiten warnings "already registered".
    """
    _discover_and_register_providers(context_registry)
    _register_libs_services(module_registry)
    _discover_and_register_mcp_modules(module_registry)

    # 3a. Módulo MCP del engine: features/main lo registra EXPLÍCITAMENTE
    # (ADR-045 — el discovery automático solo escanea features/, no core/).
    from core.engine.mcp import register_module as _register_engine_mcp

    _register_engine_mcp(module_registry)

    # 3b. Tasks y side effects del workflow — concepto de MAIN, viven
    # SOLO en features/main/task_execution (ADR-047 Fase 3). El discovery
    # por convención en features/*/tasks/ se elimina: main registra
    # declarativamente sus tasks y side effects. Los features son módulos
    # puros: exportan servicios y casos de uso, no tasks.
    from features.main.task_execution.tasks import register_task_execution_tasks
    from features.main.task_execution.side_effects import (
        register_task_execution_side_effects,
    )

    register_task_execution_tasks(task_registry)
    register_task_execution_side_effects(side_effect_engine)

    # 3c. Servicios DI poblados (get_module_service/resolve desde features/main)
    module_registry.register_service("task_registry", task_registry)
    module_registry.register_service("side_effect_engine", side_effect_engine)
    module_registry.register_service("workflow", workflow_facade)


# ── Discovery (helpers del registro) ─────────────────────────────────────────


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
        mod = importlib.import_module("features.main.task_execution.context.providers")
        register_fn = getattr(mod, "register_providers", None)
        if register_fn:
            register_fn(registry)
            logger.debug("main: providers registrados desde features.main.task_execution.context.providers")
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
        lambda: importlib.import_module("shared.core.mcp_server").McpServer(),
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
    # ── Services de orchestration/ (capa cross-feature, ADR-051 §2.6) ──
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

    Envuelve el módulo shared.core.path_resolver en un proxy de instancia
    para cumplir la regla DI: factories retornan instancias, no módulos.
    """
    mod = importlib.import_module("shared.core.path_resolver")

    class _Proxy:
        def __getattr__(self, name: str) -> Any:
            return getattr(mod, name)

    return _Proxy()


def _build_graph(project_root: str = "") -> Any:
    """Factory del orquestador CognitiveGraph (escritura + búsqueda)."""
    from shared.core.cognitive_graph import CognitiveGraph
    from libs.cognitive_graph.cognitive_registry import register_cognitive_indexes

    graph = CognitiveGraph(project_root or ".")
    register_cognitive_indexes(graph)
    return graph


def _build_searcher(project_root: str = "") -> Any:
    """Factory del lector CQRS sobre CognitiveGraph (capa de lectura)."""
    from shared.core.cognitive_graph import CognitiveGraph
    from shared.core.cognitive_searcher import CognitiveGraphSearcher
    from libs.cognitive_graph.cognitive_registry import register_cognitive_indexes

    graph = CognitiveGraph(project_root or ".")
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
    """
    from features.main.cli_interface.application.handlers import (
        handle_help,
        handle_list_projects,
        handle_prepare,
        handle_status,
        handle_submit,
        handle_transition,
    )

    return {
        "status": handle_status,
        "submit": handle_submit,
        "transition": handle_transition,
        "prepare": handle_prepare,
        "list": handle_list_projects,
        "help": handle_help,
    }


def _build_cli_request() -> Any:
    """Factory del contrato Request de cli_interface (DI, clave "cli_request")."""
    from features.main.cli_interface.infrastructure.contracts import Request

    return Request


def _build_prepare_project_service() -> Any:
    """Factory del orquestador de prepare (DI, clave "prepare_project_service").

    orchestration/ es la capa cross-feature (ADR-051 §2.6); main la registra
    como servicio DI sin importarla en el engine.
    """
    from orchestration.prepare_project_service import PrepareProjectService

    return PrepareProjectService


def _build_check_health_service() -> Any:
    """Factory del orquestador de health (DI, clave "check_health_service")."""
    from orchestration.check_health_service import CheckHealthService

    return CheckHealthService


def _build_intelligence_service() -> Any:
    """Factory del orquestador de inteligencia cross-memoria (DI, clave
    "intelligence_service").

    Devuelve la factory `build_intelligence_service(chain_dir)` — el caller
    la invoca con el chain_dir (patrón cognitive_searcher).
    """
    from orchestration.intelligence_service import build_intelligence_service

    return build_intelligence_service


def _build_adr_orchestrator_service() -> Any:
    """Factory del orquestador de ADR (DI, clave "adr_orchestrator_service").

    orchestration/ es la capa cross-feature (ADR-051 §2.6); main la registra
    como servicio DI sin importarla en el engine.
    """
    from orchestration.adr_orchestrator_service import AdrOrchestratorService

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
        from core.engine.bootstrap import get_module_service

        searcher_factory = get_module_service("cognitive_searcher")
        return searcher_factory(str(paths.project_root))
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
    from features.main.api.workflow_facade import WorkflowFacade

    return WorkflowFacade(orch)
