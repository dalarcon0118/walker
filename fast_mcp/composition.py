"""fast_mcp.composition — El ÚNICO punto de cableado entre contexts (§3bis).

Cada context expone su proveedor; este módulo los construye e inyecta por
constructor. La superficie (phases/) solo consume objetos ya cableados.
Regla de coherencia 1: aquí SÍ se cruzan contexts; en ningún otro módulo.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fast_mcp.features.project.application.use_cases import ProjectCtx
from fast_mcp.features.project.infrastructure.layout import ProjectLayout
from fast_mcp.shared_kernel.registry_store import RegistryStore

logger = logging.getLogger(__name__)


# ── Handler registration (composition root — R1/§3bis) ────────────────
# Delta + trace handler registries are populated HERE, at import time.
# This is the ONLY place cross-feature wiring is legal.

def _register_delta_handlers():
    """Register delta_tracking handlers into the dispatch registry."""
    from fast_mcp.features.delta_tracking.handlers import register_handler
    from fast_mcp.features.delta_tracking.handlers.sprint import (
        handle_sprint_planning,
        handle_resolve_project_sprint,
    )
    register_handler("sprint_planning", handle_sprint_planning)
    register_handler("resolve_project_sprint", handle_resolve_project_sprint)
    logger.debug("composition: delta handlers registered")


def _register_trace_handlers():
    """Register event_tracing handlers into the dispatch registry."""
    from fast_mcp.features.event_tracing.handlers import register_trace_handlers
    from fast_mcp.features.event_tracing.handlers.trace import (
        handle_trace_message,
        handle_trace_push,
    )
    register_trace_handlers({
        "trace_push": handle_trace_push,
        "trace_message": handle_trace_message,
    })
    logger.debug("composition: trace handlers registered")


# Run registration at import time (composition root)
_register_delta_handlers()
_register_trace_handlers()


# ── Session-level tool runners (no project context required) ─────────

def session_tool_runner(name: str, root: Path | None = None):
    """Runner for tools that work without project context (help, list_projects)."""

    def runner(**kwargs) -> dict:
        if name == "help":
            return _handle_help(kwargs)
        if name == "list_projects":
            return _handle_list_projects(kwargs)
        if name == "project_for_path":
            return _handle_project_for_path(kwargs)
        return {"error": f"Unknown session tool: {name}"}

    return runner


def _handle_help(kwargs: dict) -> dict:
    """List available tools (no project context needed)."""
    tool_filter = kwargs.get("tool", "")
    try:
        from fast_mcp.server import build_server
        server = build_server()
        # Get tools from the FastMCP server's internal registry
        tools = []
        for tool_name in sorted(server._tool_manager._tools.keys()):
            if tool_filter and tool_filter != tool_name:
                continue
            tool_obj = server._tool_manager._tools[tool_name]
            tools.append({
                "name": tool_name,
                "description": getattr(tool_obj, "description", ""),
            })
        return {"tools": tools, "count": len(tools)}
    except Exception as e:
        return {"error": f"Could not list tools: {e}"}


def _handle_project_for_path(kwargs: dict) -> dict:
    """Resuelve {project_id, repo_path} desde un path (git-like).

    Lo usa el plugin global para saber en qué proyecto está corriendo la sesión;
    sin esto tendría que adivinar por nombre de directorio.
    """
    from fast_mcp.shared_kernel.project_support import ProjectService

    path = kwargs.get("path") or kwargs.get("directory") or "."
    try:
        found = ProjectService.resolve_from_registry(str(path))
    except Exception as exc:  # noqa: BLE001 — se reporta, no se rompe
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    if not found:
        return {"ok": False, "error": f"sin proyecto registrado para {path}"}
    return {"ok": True, "project_id": found.get("project_id", ""),
            "repo_path": found.get("repo_path", "")}


def _handle_list_projects(kwargs: dict) -> dict:
    """List registered projects (no project context needed)."""
    try:
        from fast_mcp.shared_kernel.registry_store import RegistryStore
        store = RegistryStore()
        data = store.read()
        projects_dict = data.get("projects", {})
        return {
            "ok": True,
            "projects": list(projects_dict.keys()),
            "count": len(projects_dict),
        }
    except Exception as e:
        return {"ok": False, "error": f"Could not list projects: {e}"}


@dataclass(frozen=True)
class Contexts:
    """Contextos del server (se extiende por fase: memories, workflow...)."""

    project: ProjectCtx


def build(root: Path | None = None) -> Contexts:
    """Cablea los contexts. `root` inyectable para tests (raíz propia)."""
    store = RegistryStore(root)
    project = ProjectCtx(store=store, layout=ProjectLayout(store, root))
    return Contexts(project=project)


def project_tool_runner(name: str, root: Path | None = None):
    """Runner de tool del context project, con contexto YA cableado.

    Es lo que la superficie (phases/) registra en FastMCP: phases nunca
    importa features — recibe el callable cerrado sobre su contexto
    (regla de coherencia §3bis 2 y 3).
    """
    from fast_mcp.features.project.application import use_cases

    ctx = build(root).project

    def runner(**kwargs) -> dict:
        if name == "prepare":
            return use_cases.prepare_use_case(
                ctx, project_path=kwargs.get("path", ""), args=kwargs
            )
        if name == "list":
            return use_cases.list_use_case(ctx, kwargs)
        if name == "status":
            return use_cases.status_use_case(ctx, kwargs)
        raise ValueError(f"tool '{name}' sin use case en context project")

    return runner


# ── Memory tool runners (Phase 2 — handler registry) ────────────────────


def _no_project_context_error() -> dict:
    """Error fail-fast (INV-016) ACCIONABLE para las tools que exigen proyecto.

    NO resuelve el proyecto de forma implícita (prohibido: fail-fast, sin
    fallback por CWD - AGENTS.md). En su lugar le dice al agente exactamente
    qué hacer: pasar el parámetro EXPLÍCITO `project_id` y enumera los
    proyectos disponibles para que sepa valores válidos.
    """
    from fast_mcp.shared_kernel.registry_store import RegistryStore

    reg = RegistryStore().read().get("projects", {})
    avail = ", ".join(sorted(reg)) if reg else "(no hay proyectos registrados)"
    return {"error": (
        "No project context. Esta tool MCP exige el parámetro EXPLÍCITO "
        "'project_id' en la llamada (fail-fast: el proyecto NO se infiere del "
        "directorio de trabajo; no hay fallback). Pase 'project_id' con uno de "
        f"estos proyectos registrados: [{avail}]. Si no hay ninguno, registre "
        "uno primero con la tool 'walker_prepare' pasando 'path' al repo. "
        "Ver proyectos con 'walker_list'."
    )}


def _resolve_chain_dir(kwargs: dict) -> str:
    """Resolve chain_dir from kwargs (project_id or _project_id).

    Returns the REPO ROOT (where the actual code lives), not the layout dir
    (.cognitive/projects/<pid>). This is what handlers need to resolve file paths.
    """
    from fast_mcp.shared_kernel.paths import get_project_dir

    project_id = kwargs.get("project_id") or kwargs.get("_project_id") or ""
    if not project_id:
        return ""

    # Step 1: project_id → layout dir (.cognitive/projects/<pid>)
    try:
        project_dir = get_project_dir(project_id)
        if project_dir.is_dir():
            # Step 2: read repo_path from project.json (the actual code root)
            manifest = project_dir / "data" / "project.json"
            if manifest.is_file():
                import json
                try:
                    data = json.loads(manifest.read_text())
                    repo_path = data.get("project", {}).get("location", {}).get("repo_path", "")
                    if repo_path and os.path.isdir(repo_path):
                        return repo_path
                except Exception:
                    pass
            return str(project_dir)
    except Exception:
        pass

    # Step 3: fallback — try resolve_paths directly (if already a path)
    try:
        from fast_mcp.shared_kernel.project_support import ProjectService
        paths = ProjectService.resolve_paths(project_id)
        return str(paths.project_root) if paths.project_root else project_id
    except Exception:
        pass

    return project_id


def memory_tool_runner(name: str, root: Path | None = None):
    """Runner de tool del context memories, con contexto YA cableado.

    Regla de coherencia §3bis 2 y 3: phases nunca importa features —
    recibe el callable cerrado sobre su contexto.

    Dispatches to fast_mcp.memories.handlers.dispatch() —
    each tool handler is in its own module (SRP, OCP).
    """
    from fast_mcp.memories.handlers import dispatch
    from fast_mcp.memories.context import MemoryContext

    def runner(**kwargs) -> dict:
        chain_dir = _resolve_chain_dir(kwargs)
        if not chain_dir:
            return _no_project_context_error()

        ctx = MemoryContext.resolve(chain_dir)
        if ctx is None:
            return {"error": f"Could not resolve project context for '{chain_dir}'"}

        return dispatch(name, ctx, **kwargs)

    return runner


# ── Workflow tool runners (Phase 3 — handler registry) ──────────────────

def workflow_tool_runner(name: str, root: Path | None = None):
    """Runner de tool del context workflow, con contexto YA cableado.

    Regla de coherencia §3bis 2 y 3: phases nunca importa features —
    recibe el callable cerrado sobre su contexto.

    Dispatches to fast_mcp.features.workflow.handlers.dispatch().
    Resolves WorkflowContext per call (not cached).
    """
    from fast_mcp.features.workflow.handlers import dispatch
    from fast_mcp.features.workflow.context import WorkflowContext

    def runner(**kwargs) -> dict:
        chain_dir = _resolve_chain_dir(kwargs)
        if not chain_dir:
            return _no_project_context_error()

        # Inject the TaskRegistry (Sprint 5 — engine anémico). Cross-feature
        # wiring is legal ONLY here (composition root, R1/§3bis): the workflow
        # feature never imports the tasks feature itself.
        sprint_service = _build_sprint_service(chain_dir)
        task_registry = _build_task_registry(sprint_service=sprint_service)

        # W5 (ADR-076): inyectar adapter CPG para task handlers (fail-fast,
        # no guessing). Si no hay proyecto resolvible o el adapter falla, se
        # deja como None: los handlers lo tratan como ausente (opcional).
        cpg_adapter = None
        if chain_dir:
            try:
                from fast_mcp.features.code_analysis.application.services.cpg_service import CpgService
                cpg_service = CpgService.from_chain_dir(chain_dir)
                if cpg_service:
                    cpg_adapter = cpg_service.get_analysis_adapter(chain_dir)
            except Exception as exc:
                logger.debug("No se pudo construir cpg_adapter para task handlers (%s)", exc)

        # Enriquecimiento de prompts con el conocimiento del proyecto: el
        # searcher CKG se construye AQUÍ (composition root) y se inyecta; la
        # feature workflow no importa cognitive_knowledge (R1).
        ckg_searcher = None
        if chain_dir:
            try:
                from fast_mcp.features.cognitive_knowledge import ckg_service
                from fast_mcp.shared_kernel.project_support import ProjectService

                paths = ProjectService.resolve_paths(chain_dir)
                ckg_searcher = ckg_service(str(paths.data_dir))
            except Exception as exc:
                logger.debug("No se pudo construir ckg_searcher para prompts (%s)", exc)

        # Tracer REAL para el motor: sin esto `has_kg_query`/`has_skill_load`
        # devuelven False siempre y los gates que dependen de la traza son
        # insatisfacibles (medido 2026-11-17: el gate `required_tools` de
        # IMPLEMENT.WORK bloqueaba para siempre).
        workflow_tracer = None
        if chain_dir:
            try:
                from fast_mcp.features.event_tracing.application.services.trace_factory import (
                    build_trace_service,
                )
                from fast_mcp.features.event_tracing.infrastructure.cognitive_tracer import (
                    CognitiveTracer,
                )

                service = build_trace_service(chain_dir)
                workflow_tracer = CognitiveTracer(service) if service else None
            except Exception as exc:  # noqa: BLE001
                logger.warning("tracer del workflow no disponible (%s: %s)", type(exc).__name__, exc)

        ctx = WorkflowContext.resolve(
            chain_dir,
            task_registry=task_registry,
            cpg_adapter=cpg_adapter,
            ckg_searcher=ckg_searcher,
            tracer=workflow_tracer,
        )
        if ctx is None:
            return {"error": f"Could not resolve workflow for '{chain_dir}'"}

        return dispatch(name, ctx, **kwargs)

    return runner


# ── Delta tool runners (Sprint 5.2 — handler registry) ─────────────

def delta_tool_runner(name: str, root: Path | None = None):
    """Runner de tool del context delta_tracking, con contexto YA cableado.

    Regla de coherencia §3bis 2 y 3: phases nunca importa features —
    recibe el callable cerrado sobre su contexto.

    Dispatches to fast_mcp.features.delta_tracking.handlers.dispatch_delta().
    """
    from fast_mcp.features.delta_tracking.handlers import dispatch_delta

    def runner(**kwargs) -> dict:
        chain_dir = _resolve_chain_dir(kwargs)
        if not chain_dir:
            return _no_project_context_error()

        # Repo-backed service injected by the composition root (R1/§3bis):
        # handlers never self-construct an anemic SprintService (INV-016).
        sprint_service = _build_sprint_service(chain_dir)
        return dispatch_delta(name, {**kwargs, "_sprint_service": sprint_service})

    return runner


# ── Trace tool runners (Sprint 5.3 — handler registry) ─────────────────
def trace_tool_runner(name: str, root: Path | None = None):
    """Runner de tool del context event_tracing, con contexto YA cableado.

    Regla de coherencia §3bis 2 y 3: phases nunca importa features —
    recibe el callable cerrado sobre su contexto.

    Dispatches to fast_mcp.features.event_tracing.handlers.dispatch_trace().
    """
    from fast_mcp.features.event_tracing.handlers import dispatch_trace

    def runner(**kwargs) -> dict:
        chain_dir = _resolve_chain_dir(kwargs)
        if not chain_dir:
            return _no_project_context_error()

        # Wiring único en trace_factory (cacheado por proyecto): el tracer del
        # workflow y este runner deben compartir el mismo CognitiveGraph, si no
        # la guarda de generación rechaza writes cruzados y se pierden eventos.
        from fast_mcp.features.event_tracing.application.services.trace_factory import (
            build_trace_service,
        )

        trace_svc = build_trace_service(chain_dir)
        if trace_svc is None:
            return {"error": "Could not initialize CognitiveGraph"}
        ctx = {"trace_service": trace_svc, "chain_dir": chain_dir}

        return dispatch_trace(name, ctx, **kwargs)

    return runner


# ── Task/SideEffect registry (Sprint 5.4) ─────────────────────────────

def _build_delta_repo(chain_dir: str):
    """Build the repo-backed DeltaRepository (composition root only).

    Creates the CognitiveGraph via the canonical factory (config-driven
    sqlite, ADR-063/INV-016 fail-fast) and registers the 'delta' index.
    Cross-feature wiring is legal ONLY here (R1/§3bis).

    INV-016 (regla 3): `build_cognitive_graph` exige el data_dir YA resuelto
    (contrato de su docstring). `chain_dir` aquí es el REPO ROOT (contrato de
    `_resolve_chain_dir`) — pasarle el repo root a `load_memory_connection`
    ancla la DB al lugar equivocado (vector histórico de la base parásita
    `<repo>/cognitive.sqlite3`). Se resuelve el data_dir canónico del proyecto
    vía ProjectService.resolve_paths() (ADR-WALKER-054 D4).
    """
    from fast_mcp.shared_kernel.cognitive_graph.config import load_memory_connection
    from fast_mcp.shared_kernel.cognitive_graph import build_cognitive_graph
    from fast_mcp.shared_kernel.cognitive_graph.schemas import (
        PRODUCTION_INDEX_SCHEMAS,
    )
    from fast_mcp.shared_kernel.project_support import ProjectService
    from fast_mcp.features.delta_tracking.infrastructure.cognitive_delta_repo import (
        CognitiveDeltaRepository,
    )

    paths = ProjectService.resolve_paths(chain_dir)
    conn = load_memory_connection(paths.data_dir)
    cg = build_cognitive_graph(paths.data_dir, conn=conn)
    try:
        cg.register_index("delta", PRODUCTION_INDEX_SCHEMAS["delta"])
    except Exception:
        pass  # already registered
    return CognitiveDeltaRepository(cg)


def _build_sprint_service(chain_dir: str):
    """Build the repo-backed SprintService for the given chain_dir.

    Sprint 5.2: the delta index is now wired — sprint/spec mutations persist
    to the CognitiveGraph (no more anemic in-memory service). Fail-fast
    (INV-016): if the CognitiveGraph can't be built the error propagates to
    the caller (structured error); never a silent no-op write.
    """
    if not chain_dir:
        return None
    from fast_mcp.features.delta_tracking.application.services.sprint_service import (
        SprintService,
    )

    return SprintService(project_root=chain_dir, repo=_build_delta_repo(chain_dir))


def _emit_task_after(name, ctx, result) -> None:
    """Notifica `task.after` al bus de hooks del proyecto del task.

    Composition es el único lugar que puede cruzar features (R1); el registry
    recibe esta función inyectada y no conoce ni el bus ni las rutas.
    """
    chain_dir = getattr(ctx, "chain_dir", "")
    if not chain_dir:
        return
    try:
        from fast_mcp.shared_kernel.hooks import HookBus
        from fast_mcp.shared_kernel.project_support import ProjectService

        # `ctx.chain_dir` puede ser el layout cognitivo
        # (<repo>/.cognitive/projects/<pid>), no la raíz del repo: los hooks se
        # declaran en <repo>/.cognitive/hooks y el trust en <repo>/.cognitive/
        # config.json, así que hay que resolver el project_root.
        try:
            project_root = str(ProjectService.resolve_paths(chain_dir).project_root or chain_dir)
        except Exception:  # noqa: BLE001 — sin layout, se usa chain_dir tal cual
            project_root = chain_dir

        HookBus.load(project_root).emit("task.after", {
            "task": name,
            "task_id": getattr(ctx, "task_id", ""),
            "session_id": getattr(ctx, "session_id", ""),
            "ok": bool(result.ok),
            "error": result.error,
            "chain_dir": chain_dir,
        })
    except Exception:  # noqa: BLE001 — observador, nunca parte del task
        pass


def _build_task_registry(sprint_service=None):
    """Build and populate a TaskRegistry with all registered handlers."""
    from fast_mcp.features.code_analysis.application.use_cases.sync_cpg import (
        sync_project_cpg,
    )
    from fast_mcp.features.tasks.domain.task_registry import TaskRegistry
    from fast_mcp.features.tasks.handlers import register_all_tasks

    registry = TaskRegistry(on_task_done=_emit_task_after)
    # cpg_sync: composition es el único que puede cruzar features (R1); el
    # handler de tasks la recibe inyectada en vez de importar code_analysis.
    register_all_tasks(
        registry, sprint_service=sprint_service, cpg_sync=sync_project_cpg
    )
    return registry


def _build_side_effect_registry():
    """Build and populate a SideEffectRegistry with all registered handlers."""
    from fast_mcp.features.tasks.domain.side_effect_registry import SideEffectRegistry
    from fast_mcp.features.tasks.side_effects import register_all_side_effects

    registry = SideEffectRegistry()
    register_all_side_effects(registry)
    return registry


def task_tool_runner(name: str, root: Path | None = None):
    """Runner de tool del context tasks, con contexto YA cableado.

    Regla de coherencia §3bis 2 y 3: phases nunca importa features —
    recibe el callable cerrado sobre su contexto.

    Dispatches via execute_public_task → TaskRegistry.
    """
    from fast_mcp.features.workflow.application.task_dispatcher import execute_public_task

    def runner(**kwargs) -> dict:
        chain_dir = _resolve_chain_dir(kwargs)
        if not chain_dir:
            return _no_project_context_error()

        # Build TaskRegistry (lazy, per call)
        sprint_service = _build_sprint_service(chain_dir)
        registry = _build_task_registry(sprint_service=sprint_service)

        project_id = kwargs.get("project_id", "") or kwargs.get("_project_id", "")
        task_data = {k: v for k, v in kwargs.items() if k not in ("project_id",)}

        return execute_public_task(
            name=name,
            project_id=project_id,
            chain_dir=chain_dir,
            task_data=task_data,
            registry=registry,
        )

    return runner