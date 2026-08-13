"""mcp.py — Project MCP protocol module.

Declares project tools and routes. Auto-discovery via ModuleRegistry.
"""
from __future__ import annotations

from shared.core.mcp_server import McpServer

from core.engine.interface.contracts import ModuleDefinition, RestRoute, ToolDefinition


def register(server: McpServer) -> None:
    server.tool(
        name="project_analyze",
        description="Unified project analysis: list, detect, init, health",
        handler=_public_task("project_analyze"),
    )


def register_module(registry: Any) -> None:
    module = ModuleDefinition(
        name="project",
        tools=[
            ToolDefinition(
                name="project_analyze",
                description="Unified project analysis: list, detect, init, health",
                handler=_public_task("project_analyze"),
                input_schema={
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["list", "detect", "init", "health"], "description": "Action to perform"},
                        "project_id": {"type": "string", "description": "Project identifier"},
                        "path": {"type": "string", "description": "Path for detect/init"},
                        "name": {"type": "string", "description": "Project name for init"},
                        "language": {"type": "string", "description": "Language for init"},
                        "framework": {"type": "string", "description": "Framework for init"},
                    },
                    "required": ["action"],
                    "additionalProperties": True,
                },
                metadata={"tags": ["project", "unified"]},
            ),
            ToolDefinition(name="list_projects", description="List all available projects with sprint status.", handler=_public_task("list_projects"), input_schema={"type": "object"}, metadata={"tags": ["project", "list"]}),
        ],
        routes=[
            RestRoute(path="{project_id}/project_analyze", method="POST", flags_builder=lambda a: {"_payload": a.get("params", a)}),
            RestRoute(path="{project_id}/list_projects", method="POST", flags_builder=lambda a: {"_payload": a.get("body", a)}),
        ],
        metadata={"description": "Project management module", "tags": ["project"]},
    )
    registry.register_module(module)


def _public_task(name: str):
    def handler(args: dict) -> dict:
        from core.engine.application.task_dispatcher import dispatch_public_task
        body = args.get("body", args)
        if name == "project_analyze":
            body = _map_project_analyze(body)
        return dispatch_public_task(name, body)
    return handler


def _map_project_analyze(body: dict) -> dict:
    action = body.get("action", "")
    if action == "list":
        return {}
    if action == "detect":
        return {"path": body.get("path", "")}
    if action == "init":
        return {
            "project_id": body.get("project_id", ""),
            "name": body.get("name", ""),
            "repo_path": body.get("path", ""),
            "language": body.get("language", "python"),
            "framework": body.get("framework", "unknown"),
        }
    if action == "health":
        return {}
    return body
