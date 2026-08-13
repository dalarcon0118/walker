"""mcp.py — Code Analysis MCP protocol module.

Declares CPG tools and routes. Auto-discovery via ModuleRegistry.
"""
from __future__ import annotations

from shared.core.mcp_server import McpServer

from core.engine.interface.contracts import ModuleDefinition, RestRoute, ToolDefinition


def register(server: McpServer) -> None:
    server.tool(
        name="cpg_analyze",
        description="Unified CPG analysis: business layer, dead code, security, review, metrics, build, context",
        handler=_public_task("cpg_analyze"),
    )


def register_module(registry: Any) -> None:
    """Registra el módulo code_analysis en el ModuleRegistry."""
    module = ModuleDefinition(
        name="code_analysis",
        tools=[
            ToolDefinition(
                name="cpg_analyze",
                description="Unified CPG analysis: business layer, dead code, security, review, metrics, build, context",
                handler=_public_task("cpg_analyze"),
                input_schema={
                    "type": "object",
                    "properties": {
                        "scope": {"type": "string", "description": "Scope: file path or directory"},
                        "depth": {"type": "string", "enum": ["business", "business_layer"], "description": "Analysis depth"},
                        "detect": {"type": "string", "enum": ["dead_code", "security"], "description": "Detection mode"},
                        "review": {"type": "boolean", "description": "Enable code review"},
                        "metrics": {"type": "boolean", "description": "Enable metrics"},
                        "build": {"type": "boolean", "description": "Build/rebuild CPG"},
                    },
                    "additionalProperties": True,
                },
                metadata={"tags": ["cpg", "unified", "analysis"]},
            ),
        ],
        routes=[
            RestRoute(
                path="{project_id}/cpg_analyze",
                method="POST",
                flags_builder=lambda a: {"_payload": a.get("params", a)},
            ),
        ],
        metadata={"description": "Code analysis module: CPG, business layer, dead code, security scanning", "tags": ["cpg", "code"]},
    )
    registry.register_module(module)


def _public_task(name: str):
    def handler(args: dict) -> dict:
        from core.engine.application.task_dispatcher import dispatch_public_task
        return dispatch_public_task(name, args.get("body", args))
    return handler
