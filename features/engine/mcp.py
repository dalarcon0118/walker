"""mcp.py — Engine MCP protocol module.

Declares workflow tools (submit, transition, status, etc.)
and dynamic public tasks from task registry.
"""
from __future__ import annotations

from libs.mcp_server import McpServer


def register(server: McpServer) -> None:
    # ── Workflow tools ──
    server.tool(
        name="prepare", description="Initialize project at path (creates .cognitive/).",
        handler=_call_handler("prepare"),
    )
    server.tool(
        name="list", description="List projects managed by walker.",
        handler=_call_handler("list"),
    )
    server.tool(
        name="status",
        description="Project workflow state + memory stats: node, sub_state, instructions, gates, CPG/CKG/Delta counts.",
        handler=_call_handler("status"),
    )
    server.tool(
        name="submit",
        description="Send data to current sub-state. ADVANCES the workflow. Body: node fields.",
        handler=_call_handler("submit"),
    )
    server.tool(
        name="transition",
        description="Transition through a gate (admin/emergency).",
        handler=_call_handler("transition"),
    )
    server.tool(
        name="help",
        description="Walker protocol and available tasks.",
        handler=_call_handler("help"),
    )

    # ── Dynamic public tasks (from engine registry) ──
    _register_public_tasks(server)


def _call_handler(name: str):
    def handler(args: dict) -> dict:
        from features.cli_interface.application.handlers import _run_public_task
        return _run_public_task(name, args.get("body", args))
    return handler


def _register_public_tasks(server: McpServer) -> None:
    """Discover public tasks from engine registry at registration time."""
    try:
        from features.engine.application.workflow_api import bootstrap
        from features.engine.infrastructure.task_registry import InMemoryTaskRegistry
        from features.engine.infrastructure.trace_log import TraceLogger
        registry, _ = bootstrap(InMemoryTaskRegistry(tracer=TraceLogger()))
        public_tasks = registry.list_tasks_with_meta(scope="public") or []
        reserved = {"prepare", "list", "status", "submit", "transition", "help",
                    "kg_search", "kg_multi", "kg_store",
                    "cpg_business", "cpg_unused_functions", "analyze_code",
                    "capture_task", "import_legacy_tasks", "import_legacy_history",
                    "detect_legacy", "map_legacy_workflow", "trace_push",
                    "cpg_dfg_scan", "cpg_index_ref", "scan_code_and_build_cpg",
                    "project_metrics"}
        for t in public_tasks:
            name = t.get("name", "")
            if not name or name in reserved:
                continue
            desc = t.get("description", f"Public task: {name}")
            server.tool(
                name=name,
                description=f"{desc} (public task). Read walker://help/{name}.",
                handler=_public_task_handler(name),
            )
    except Exception:
        pass


def _public_task_handler(name: str):
    def handler(args: dict) -> dict:
        from features.cli_interface.application.handlers import _run_public_task
        return _run_public_task(name, args.get("body", args))
    return handler
