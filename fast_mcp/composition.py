"""fast_mcp.composition — El ÚNICO punto de cableado entre contexts (§3bis).

Cada context expone su proveedor; este módulo los construye e inyecta por
constructor. La superficie (phases/) solo consume objetos ya cableados.
Regla de coherencia 1: aquí SÍ se cruzan contexts; en ningún otro módulo.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fast_mcp.features.project.application.use_cases import ProjectCtx
from fast_mcp.features.project.infrastructure.layout import ProjectLayout
from fast_mcp.features.project.infrastructure.registry_store import RegistryStore

logger = logging.getLogger(__name__)


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
    """Resolve chain_dir from kwargs (project_id or _project_id)."""
    from fast_mcp.shared_kernel.project_support import ProjectService

    project_id = kwargs.get("project_id") or kwargs.get("_project_id") or ""
    if project_id:
        try:
            paths = ProjectService.resolve_paths(project_id)
            return str(paths.project_root) if paths.project_root else project_id
        except Exception:
            pass
    return project_id


def memory_tool_runner(name: str, root: Path | None = None):
    """Runner de tool del context memories, con contexto YA cableado.

    Regla de coherencia §3bis 2 y 3: phases nunca importa features —
    recibe el callable cerrado sobre su contexto.

    Dispatches to fast_mcp.features.memories.handlers.dispatch() —
    each tool handler is in its own module (SRP, OCP).
    """
    from fast_mcp.features.memories.handlers import dispatch
    from fast_mcp.features.memories.context import MemoryContext

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

        ctx = WorkflowContext.resolve(chain_dir)
        if ctx is None:
            return {"error": f"Could not resolve workflow for '{chain_dir}'"}

        return dispatch(name, ctx, **kwargs)

    return runner
