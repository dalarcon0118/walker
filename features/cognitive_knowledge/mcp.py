"""mcp.py — CKG MCP protocol module.

Declares tools (kg_search, kg_store) and resources (schema, help)
for the Cognitive Knowledge Graph. Auto-discovery via walker://schema/ckg.
"""
from __future__ import annotations

from libs.mcp_server import McpServer


def register(server: McpServer) -> None:
    # ── Tools ──
    server.tool(
        name="kg_search",
        description="Search project memory (CPG/CKG/Delta/Trace). PROTOCOL: call kg_schema first to discover indices, then kg_search with discovered fields. Read walker://help/kg_search for examples.",
        handler=_kg_search,
    )
    server.tool(
        name="kg_multi",
        description="Federated query across multiple indices. Read walker://help/kg_multi.",
        handler=_kg_multi,
    )
    server.tool(
        name="kg_store",
        description="Register knowledge (decision/convention/pattern) in project CKG. Read walker://help/kg_store for field format.",
        handler=_kg_store,
    )

    # ── Resources: schemas (auto-descubiertos en runtime) ──
    server.resource(
        "walker://schema/{index}",
        name="Index Schema",
        description="Discover fields, types, and fulltext config for any index: ckg, cpg, delta, trace. Use BEFORE kg_search to know what to query.",
        handler=_schema_resource,
    )
    server.resource(
        "walker://help/{tool}",
        name="Tool Help",
        description="Format and examples for any tool: kg_search, kg_store, kg_multi, etc.",
        handler=_help_resource,
    )


# ── Tool handlers ──

def _kg_search(args: dict) -> dict:
    from features.engine.application.task_dispatcher import dispatch_public_task
    return dispatch_public_task("kg_search", args.get("query", {}))


def _kg_multi(args: dict) -> dict:
    from features.engine.application.task_dispatcher import dispatch_public_task
    return dispatch_public_task("kg_multi", args.get("layers", args.get("query", {})))


def _kg_store(args: dict) -> dict:
    from features.engine.application.task_dispatcher import dispatch_public_task

    body = (
        args.get("body")
        or args.get("query")
        or args.get("search")
        or args
    )
    if not isinstance(body, dict):
        body = dict(body or {})
    return dispatch_public_task("kg_store", body)


# ── Resource handlers (dynamic, no schemas to maintain) ──

def _schema_resource(ctx: dict) -> dict:
    """walker://schema/{index} — dynamic field discovery."""
    index = ctx["params"].get("index", "ckg")
    try:
        from libs.cognitive_graph import CognitiveGraph
        import os
        from features.project import ProjectService
        resolved = ProjectService.resolve_from_registry()
        cog_dir = os.path.join(
            resolved.get("project_root", "."), ".cognitive",
            "projects", resolved.get("project_id", "walker"), "data"
        ) if resolved else "."
        cg = CognitiveGraph(cog_dir, auto_register=True)
        schema = cg.schema(index)
        return {"contents": [{"uri": ctx["uri"], "mimeType": "application/json",
                "text": str(schema)}]}
    except Exception as e:
        return {"contents": [{"uri": ctx["uri"], "mimeType": "application/json",
                "text": str({"error": str(e)})}]}


def _help_resource(ctx: dict) -> dict:
    """walker://help/{tool} — examples and format."""
    tool = ctx["params"].get("tool", "kg_search")
    examples = {
        "kg_search": '{"text":"auth"} or {"type":"decision","scope":"security"} or {"_index":"cpg","file":"src/auth.py"}',
        "kg_multi": '{"ckg":{"text":"auth"},"cpg":{"type":"class"}}',
        "kg_store": '{"type":"decision","statement":"Use JWT for auth","scope":"security","metadata":{"task_id":"TASK-042"}}',
        "kg_schema": '{"index":"ckg"} (omit for all indices)',
    }
    return {"contents": [{"uri": ctx["uri"], "mimeType": "application/json",
            "text": str({"tool": tool, "example": examples.get(tool, "See kg_schema for field discovery")})}]}
