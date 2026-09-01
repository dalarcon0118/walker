"""features/workflow/handlers/workflow.py — Workflow tool handlers.

Each handler is a thin function that delegates to OrchestrationService.
Handlers never raise (caught by dispatch()). They return dicts (MCP contract).
"""
from __future__ import annotations

from typing import Any

from ..domain.execution import ExecutionContext


def _make_ctx(chain_dir: str, kwargs: dict) -> ExecutionContext:
    """Build ExecutionContext from chain_dir + kwargs."""
    return ExecutionContext.create(
        chain_dir=chain_dir,
        task_id=kwargs.get("task_id", ""),
        session_id=kwargs.get("session_id", ""),
    )


def _extract_fields(kwargs: dict) -> dict[str, Any]:
    """Extract submit fields from kwargs (merge _payload into top-level)."""
    fields = dict(kwargs.get("_payload", {}))
    for k, v in kwargs.items():
        if not k.startswith("_") and k not in ("task_id", "session_id"):
            fields[k] = v
    return fields


def handle_submit(ctx: Any, **kwargs: Any) -> dict:
    """Submit data to the current sub-state.

    Pipeline: validate → record → persist → trace.
    """
    exec_ctx = _make_ctx(ctx.chain_dir, kwargs)
    fields = _extract_fields(kwargs)
    return ctx.orchestrator.submit(exec_ctx, fields=fields or None)


def handle_transition(ctx: Any, **kwargs: Any) -> dict:
    """Transition to the next node via a gate.

    Pipeline: validate gate → apply → persist → trace.
    """
    gate = kwargs.get("gate", "")
    if not gate:
        return {"ok": False, "error": "Missing required parameter: gate"}

    exec_ctx = _make_ctx(ctx.chain_dir, kwargs)
    reason = kwargs.get("reason", "")
    return ctx.orchestrator.transition(exec_ctx, gate=gate, reason=reason)


def handle_status(ctx: Any, **kwargs: Any) -> dict:
    """Get current workflow status (read-only snapshot)."""
    exec_ctx = _make_ctx(ctx.chain_dir, kwargs)
    return ctx.orchestrator.status(exec_ctx)


def handle_help(ctx: Any, **kwargs: Any) -> dict:
    """Get help: available tools + current state."""
    exec_ctx = _make_ctx(ctx.chain_dir, kwargs)
    return ctx.orchestrator.help(exec_ctx)


def handle_list_projects(ctx: Any, **kwargs: Any) -> dict:
    """List available projects from the registry."""
    try:
        from fast_mcp.features.project.infrastructure.registry_store import RegistryStore
        store = RegistryStore()
        projects = store.list_projects()
        return {
            "count": len(projects),
            "projects": [
                {
                    "project_id": p.get("project_id", ""),
                    "path": p.get("path", ""),
                    "name": p.get("name", ""),
                }
                for p in projects
            ],
        }
    except Exception as e:
        return {"error": f"Could not list projects: {e}"}
