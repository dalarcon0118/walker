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
    """Extract submit fields from kwargs (merge _payload into top-level).

    Alias `intent` → `decision`: el nodo START declara `intent` en su
    `input_schema` y `decision` en el `output_schema`, y su template manda
    `intent`. Sin el alias, el payload *documentado* no cruza el gate: medido,
    `{"intent":"project_selected","project_id":...}` dejaba el workflow en
    START.SELECT aunque el submit fuera ok.
    """
    fields = dict(kwargs.get("_payload", {}))
    for k, v in kwargs.items():
        if not k.startswith("_") and k not in ("task_id", "session_id"):
            fields[k] = v
    if not str(fields.get("decision") or "").strip() and str(fields.get("intent") or "").strip():
        fields["decision"] = fields["intent"]
    return fields


def handle_submit(ctx: Any, **kwargs: Any) -> dict:
    """Submit data to the current sub-state.

    Pipeline: validate → record → persist → trace.
    """
    exec_ctx = _make_ctx(ctx.chain_dir, kwargs)
    fields = _extract_fields(kwargs)
    return ctx.orchestrator.submit(exec_ctx, fields=fields or None)


def handle_advance(ctx: Any, **kwargs: Any) -> dict:
    """Avanza al siguiente sub-estado del nodo actual (intra-nodo)."""
    exec_ctx = _make_ctx(ctx.chain_dir, kwargs)
    fields = _extract_fields(kwargs)
    return ctx.orchestrator.advance(exec_ctx, fields=fields or None)


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
    """Get current workflow status (read-only; view=light|full)."""
    exec_ctx = _make_ctx(ctx.chain_dir, kwargs)
    return ctx.orchestrator.status(exec_ctx, view=str(kwargs.get("view") or "light"))


def handle_help(ctx: Any, **kwargs: Any) -> dict:
    """Get help: available tools + current state (view=light|full)."""
    exec_ctx = _make_ctx(ctx.chain_dir, kwargs)
    return ctx.orchestrator.help(exec_ctx, view=str(kwargs.get("view") or "light"))


def handle_list_projects(ctx: Any, **kwargs: Any) -> dict:
    """List available projects from the registry."""
    try:
        from fast_mcp.shared_kernel.registry_store import RegistryStore
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


def handle_execute_task(ctx: Any, **kwargs: Any) -> dict:
    """Execute a task by name via execute_public_task.

    Uses the TaskRegistry from WorkflowContext (Sprint 5.4).
    Registry is injected by composition.py — never imported here (R1).
    """
    from ..application.task_dispatcher import execute_public_task

    task_name = kwargs.get("task_name", "") or kwargs.get("name", "")
    if not task_name:
        return {"ok": False, "error": "Missing required parameter: task_name"}

    project_id = kwargs.get("project_id", "") or kwargs.get("_project_id", "")
    task_data = {k: v for k, v in kwargs.items() if k not in ("task_name", "name")}

    # Get registry and cpg_adapter from context (injected by composition.py, never imported — R1)
    registry = getattr(ctx, "task_registry", None)
    cpg_adapter = getattr(ctx, "cpg_adapter", None)

    return execute_public_task(
        name=task_name,
        project_id=project_id,
        chain_dir=ctx.chain_dir,
        task_data=task_data,
        registry=registry,
        cpg_adapter=cpg_adapter,
    )
