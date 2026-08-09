"""mcp.py — Event Tracing MCP protocol module.

Declares Trace tools and routes. Auto-discovery via ModuleRegistry.
"""
from __future__ import annotations

from libs.mcp_server import McpServer

from features.engine.interface.contracts import ModuleDefinition, RestRoute, ToolDefinition


def register(server: McpServer) -> None:
    server.tool(
        name="trace_push",
        description="Push a trace event to execution history (debugging/monitoring).",
        handler=_trace_push_handler,
    )


def register_module(registry: Any) -> None:
    module = ModuleDefinition(
        name="event_tracing",
        tools=[
            ToolDefinition(
                name="trace_push",
                description="Push a trace event to execution history.",
                handler=_trace_push_handler,
                input_schema={
                    "type": "object",
                    "properties": {
                        "event_type": {"type": "string", "description": "Type of trace event"},
                        "source": {"type": "string", "description": "Event source"},
                        "session_id": {"type": "string", "description": "Session identifier"},
                        "agent_id": {"type": "string", "description": "Agent identifier"},
                        "task_id": {"type": "string", "description": "Task identifier"},
                        "timestamp": {"type": "string", "description": "Event timestamp"},
                        "data": {"type": "object", "description": "Event payload"},
                    },
                    "required": ["event_type"],
                    "additionalProperties": True,
                },
                metadata={"tags": ["trace", "events"]},
            ),
        ],
        routes=[
            RestRoute(path="{project_id}/trace_push", method="POST", flags_builder=lambda a: {"_payload": a.get("body", a)}),
        ],
        metadata={"description": "Event tracing module", "tags": ["trace"]},
    )
    registry.register_module(module)


def _trace_push_handler(args: dict) -> dict:
    from features.event_tracing.infrastructure.repository import push_event
    body = args.get("body", args) if isinstance(args, dict) else {}
    chain_dir = _resolve_chain_dir(body)
    if not chain_dir:
        return {"ok": False, "error": "No project context — provide _project_id or chain_dir"}
    return push_event(chain_dir, body)


def _resolve_chain_dir(body: dict) -> str:
    direct = body.get("chain_dir", "")
    if direct:
        return str(direct)
    from features.project import ProjectService
    project_id = body.get("_project_id", "") or ""
    if not project_id:
        resolved = ProjectService.resolve_from_registry()
        project_id = (resolved or {}).get("project_id", "")
    if not project_id:
        return ""
    return ProjectService.find_active_sprint(project_id) or ""
