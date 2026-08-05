"""mcp.py — Delta Tracking MCP protocol module.

Declares Delta tools (capture_task, import/export) and resources.
"""
from __future__ import annotations

from libs.mcp_server import McpServer


def register(server: McpServer) -> None:
    server.tool(
        name="capture_task",
        description="Capture a task from context and add it as a spec to the active sprint. Read walker://help/capture_task.",
        handler=_public_task("capture_task"),
    )
    server.tool(
        name="import_legacy_tasks",
        description="Import legacy sprint tasks into the DeltaGraph.",
        handler=_public_task("import_legacy_tasks"),
    )
    server.tool(
        name="import_legacy_history",
        description="Import legacy sprint history into the DeltaGraph.",
        handler=_public_task("import_legacy_history"),
    )
    server.tool(
        name="detect_legacy",
        description="Detect legacy sprints not yet imported.",
        handler=_public_task("detect_legacy"),
    )
    server.tool(
        name="map_legacy_workflow",
        description="Map legacy workflow state to new format.",
        handler=_public_task("map_legacy_workflow"),
    )


def _public_task(name: str):
    def handler(args: dict) -> dict:
        from features.cli_interface.application.handlers import _run_public_task
        return _run_public_task(name, args.get("body", args))
    return handler
