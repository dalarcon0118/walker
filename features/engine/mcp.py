"""mcp.py — Engine MCP protocol module.

Declares workflow tools (submit, transition, status, etc.)
and dynamic public tasks from task registry.

Return-pattern: fallible operations return (result, errors) tuples.
No silent try/except — errors surface to register().
"""
from __future__ import annotations

from libs.mcp_server import McpServer


def register(server: McpServer) -> tuple[int, list[str]]:
    """Register dynamic public tasks from engine registry.

    Los tools REST del workflow (status, submit, transition, prepare, list,
    help) los registra mcp_adapter._register_rest_tools (F7, ADR-016) — son
    endpoints REST del adapter, no tasks del registry.

    Returns:
        (registered_count, errors) — errors from dynamic task discovery.
    """
    # ── Dynamic public tasks (from engine registry) ──
    count, errors = _register_public_tasks(server)
    return count, errors


def _register_public_tasks(server: McpServer) -> tuple[int, list[str]]:
    """Discover and register public tasks from engine registry.

    Return-pattern: returns (registered_count, errors).
    If bootstrap fails, returns (0, [error]) — never silent.
    """
    errors: list[str] = []

    try:
        from features.engine.application.workflow_api import bootstrap
    except ImportError as e:
        return 0, [f"workflow_api import failed: {e}"]

    try:
        from features.engine.infrastructure.task_registry import InMemoryTaskRegistry
        from features.engine.infrastructure.trace_log import TraceLogger
    except ImportError as e:
        return 0, [f"task_registry/trace_log import failed: {e}"]

    registry, _ = bootstrap(InMemoryTaskRegistry(tracer=TraceLogger()))
    if registry is None:
        return 0, ["bootstrap returned no registry"]

    public_tasks = registry.list_tasks_with_meta(scope="public") or []

    reserved = {"prepare", "list", "status", "submit", "transition", "help",
                "kg_search", "kg_multi", "kg_store",
                "cpg_business", "cpg_unused_functions", "analyze_code",
                "capture_task", "import_legacy_tasks", "import_legacy_history",
                "detect_legacy", "map_legacy_workflow", "trace_push",
                "cpg_dfg_scan", "cpg_index_ref", "scan_code_and_build_cpg",
                "project_metrics"}

    registered = 0
    skipped: list[str] = []
    for t in public_tasks:
        name = t.get("name", "")
        if not name:
            skipped.append(t.get("description", "?")[:40])
            continue
        if name in reserved:
            continue
        desc = t.get("description", f"Public task: {name}")
        server.tool(
            name=name,
            description=f"{desc} (public task). Read walker://help/{name}.",
            handler=_public_task_handler(name),
        )
        registered += 1

    if skipped:
        errors.append(f"skipped tasks without name: {', '.join(skipped)}")

    return registered, errors


def _public_task_handler(name: str):
    def handler(args: dict) -> dict:
        from features.engine.application.task_dispatcher import dispatch_public_task
        return dispatch_public_task(name, args.get("body", args))
    return handler
