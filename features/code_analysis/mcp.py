"""mcp.py — Code Analysis MCP protocol module.

Declares CPG tools (cpg_business, analyze_code, kg_search for CPG)
and resources. Auto-discovery via walker://schema/cpg.
"""
from __future__ import annotations

from libs.mcp_server import McpServer


def register(server: McpServer) -> None:
    server.tool(
        name="cpg_business",
        description="CPG business layer: reusable pure functions, business flows, architecture patterns. Read walker://help/cpg_business.",
        handler=_cpg_business,
    )
    server.tool(
        name="cpg_unused_functions",
        description="Detect unused functions via CPG (dead code detection).",
        handler=_public_task("cpg_unused_functions"),
    )
    server.tool(
        name="analyze_code",
        description="Full code analysis via CPG: symbols, signatures, imports, business layer. Read walker://help/analyze_code.",
        handler=_analyze_code,
    )
    server.tool(
        name="cpg_dfg_scan",
        description="Data flow scan: hardcoded secrets, taint paths, uninitialized vars.",
        handler=_public_task("cpg_dfg_scan"),
    )
    server.tool(
        name="cpg_index_ref",
        description="Index a reference library as CPG reference index.",
        handler=_public_task("cpg_index_ref"),
    )
    server.tool(
        name="scan_code_and_build_cpg",
        description="Full CPG rebuild from source code. Use after major changes or first setup.",
        handler=_public_task("scan_code_and_build_cpg"),
    )


def _cpg_business(args: dict) -> dict:
    from features.engine.application.task_dispatcher import dispatch_public_task
    return dispatch_public_task("cpg_business", args.get("query", args))


def _analyze_code(args: dict) -> dict:
    from features.engine.application.task_dispatcher import dispatch_public_task
    area = args.get("area", args.get("query", ""))
    if isinstance(area, str):
        area = {"area": area}
    return dispatch_public_task("analyze_code", area)


def _public_task(name: str):
    def handler(args: dict) -> dict:
        from features.engine.application.task_dispatcher import dispatch_public_task
        return dispatch_public_task(name, args.get("body", args))
    return handler
