"""mcp.py — Event Tracing MCP protocol module.

Declares Trace tools (trace_push) and resources.
"""
from __future__ import annotations

from libs.mcp_server import McpServer


def register(server: McpServer) -> None:
    server.tool(
        name="trace_push",
        description="Push a trace event to execution history (debugging/monitoring).",
        handler=_public_task("trace_push"),
    )


def _public_task(name: str):
    def handler(args: dict) -> dict:
        from features.engine.application.task_dispatcher import dispatch_public_task
        return dispatch_public_task(name, args.get("body", args))
    return handler
