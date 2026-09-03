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
    from fast_mcp.features.event_tracing.handlers.trace import handle_trace_push
    register_trace_handlers({"trace_push": handle_trace_push})
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
            return {"error": "No project context. Run 'walker prepare <path>' first."}

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
            return {"error": "No project context. Run 'walker prepare <path>' first."}

        # Inject the TaskRegistry (Sprint 5 — engine anémico). Cross-feature
        # wiring is legal ONLY here (composition root, R1/§3bis): the workflow
        # feature never imports the tasks feature itself.
        sprint_service = _build_sprint_service(chain_dir)
        task_registry = _build_task_registry(sprint_service=sprint_service)

        ctx = WorkflowContext.resolve(chain_dir, task_registry=task_registry)
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
        return dispatch_delta(name, kwargs)

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
            return {"error": "No project context. Run 'walker prepare <path>' first."}

        # Lazy-import TraceService to avoid circular deps
        from fast_mcp.features.event_tracing.infrastructure.cognitive_trace_repo import (
            CognitiveTraceRepository,
        )
        from fast_mcp.features.event_tracing.application.services.trace_service import (
            TraceService,
        )

        # Build CG for the project and ensure trace index is registered
        try:
            from fast_mcp.shared_kernel.cognitive_graph import CognitiveGraph
            cg = CognitiveGraph(chain_dir)
            # Register trace index if not already present
            try:
                cg.register_index("trace", {
                    "fields": {
                        "event_type": "keyword",
                        "source": "keyword",
                        "task_id": "keyword",
                        "sprint_id": "keyword",
                        "node": "keyword",
                        "tool": "keyword",
                    },
                })
            except Exception:
                pass  # already registered
        except Exception as exc:
            return {"error": f"Could not initialize CognitiveGraph: {exc}"}

        repo = CognitiveTraceRepository(cg)
        trace_svc = TraceService(repo)
        ctx = {"trace_service": trace_svc, "chain_dir": chain_dir}

        return dispatch_trace(name, ctx, **kwargs)

    return runner


# ── Task/SideEffect registry (Sprint 5.4) ─────────────────────────────

def _build_sprint_service(chain_dir: str):
    """Build a SprintService for the given chain_dir (composition root only).

    The SprintService lives in the delta_tracking feature. Composition.py is
    the ONLY place allowed to cross-import it (R1/§3bis); it is then injected
    into the tasks handlers via closure. Uses the in-memory service (the
    repo-backed delta index wiring is a separate, deferred task).
    """
    if not chain_dir:
        return None
    try:
        from fast_mcp.features.delta_tracking.application.services.sprint_service import (
            SprintService,
        )

        return SprintService(project_root=chain_dir)
    except Exception as exc:  # noqa: BLE001
        logger.warning("composition: SprintService unavailable for %s (%s)", chain_dir, exc)
        return None


def _build_task_registry(sprint_service=None):
    """Build and populate a TaskRegistry with all registered handlers."""
    from fast_mcp.features.tasks.domain.task_registry import TaskRegistry
    from fast_mcp.features.tasks.handlers import register_all_tasks

    registry = TaskRegistry()
    register_all_tasks(registry, sprint_service=sprint_service)
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
            return {"error": "No project context. Run 'walker prepare <path>' first."}

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