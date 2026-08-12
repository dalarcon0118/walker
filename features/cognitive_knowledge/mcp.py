"""mcp.py — CKG MCP protocol module.

Declares tools and resources for the Cognitive Knowledge Graph.
Auto-discovery via ModuleRegistry.
"""
from __future__ import annotations

from libs.mcp_server import McpServer

from features.engine.interface.contracts import ModuleDefinition, RestRoute, ToolDefinition


def register(server: McpServer) -> None:
    server.tool(
        name="ckg_analyze",
        description="Unified CKG analysis: search, save, active, supersede, context",
        handler=_public_task("ckg_analyze"),
    )


def register_module(registry: Any) -> None:
    module = ModuleDefinition(
        name="cognitive_knowledge",
        tools=[
            ToolDefinition(
                name="ckg_analyze",
                description="Unified CKG analysis: search, save, active, supersede, context",
                handler=_public_task("ckg_analyze"),
                input_schema={
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["search", "save", "active", "supersede", "context"], "description": "Action to perform"},
                        "query": {"type": "string", "description": "Search query"},
                        "text": {"type": "string", "description": "Search text (alias for query)"},
                        "nodes": {"type": "array", "items": {"type": "object"}, "description": "Nodes to save"},
                        "type": {"type": "string", "description": "Filter by type"},
                        "old_id": {"type": "string", "description": "Old node ID to supersede"},
                        "new_id": {"type": "string", "description": "New node ID"},
                        "statement": {"type": "string", "description": "Statement for new decision"},
                        "files": {"type": "array", "items": {"type": "string"}, "description": "Files for context"},
                        "max_results": {"type": "integer", "description": "Max results"},
                    },
                    "required": ["action"],
                    "additionalProperties": True,
                },
                metadata={"tags": ["ckg", "unified", "analysis"]},
            ),
            ToolDefinition(name="kg_search", description="Search project memory (CKG).", handler=_public_task("kg_search"), metadata={"tags": ["kg", "search"]}),
            ToolDefinition(name="kg_multi", description="Federated query across multiple indices.", handler=_public_task("kg_multi"), metadata={"tags": ["kg", "federated"]}),
            ToolDefinition(name="save_to_ckg", description="Register knowledge in project CKG.", handler=_public_task("save_to_ckg"), metadata={"tags": ["kg", "store"]}),
        ],
        routes=[
            RestRoute(path="{project_id}/ckg_analyze", method="POST", flags_builder=lambda a: {"_payload": a.get("params", a)}),
            RestRoute(path="{project_id}/kg_search", method="POST", flags_builder=_kg_search_flags),
            RestRoute(path="{project_id}/kg_multi", method="POST", flags_builder=lambda a: {"_payload": a.get("layers", a.get("query", {}))}),
            RestRoute(path="{project_id}/save_to_ckg", method="POST", flags_builder=lambda a: {"_payload": a.get("body", a)}),
        ],
        metadata={"description": "Cognitive Knowledge Graph module", "tags": ["kg", "ckg"]},
    )
    registry.register_module(module)


def _kg_search(args: dict) -> dict:
    from features.engine.application.task_dispatcher import dispatch_public_task
    return dispatch_public_task("kg_search", args.get("query", {}))


def _kg_search_flags(args: dict) -> dict:
    """flags_builder del route kg_search (fix del fallo silencioso).

    ANTES: {"_payload": a.get("query", {})} descartaba `index` (y todo lo
    demás excepto query) — el agente que pedía index="delta" consultaba el
    índice DEFAULT (ckg) sin error ni aviso. AHORA se preserva el payload
    completo del tool (query/index/_index/_project_id) para que la task
    kg_search resuelva el índice solicitado.
    """
    payload = dict(args)
    payload.pop("body", None)
    return {"_payload": payload}


def _kg_multi(args: dict) -> dict:
    from features.engine.application.task_dispatcher import dispatch_public_task
    return dispatch_public_task("kg_multi", args.get("layers", args.get("query", {})))


def _save_to_ckg(args: dict) -> dict:
    from features.engine.application.task_dispatcher import dispatch_public_task
    body = args.get("body") or args.get("query") or args.get("search") or args
    if not isinstance(body, dict):
        body = dict(body or {})
    return dispatch_public_task("save_to_ckg", body)


def _public_task(name: str):
    def handler(args: dict) -> dict:
        from features.engine.application.task_dispatcher import dispatch_public_task
        return dispatch_public_task(name, args.get("body", args))
    return handler
