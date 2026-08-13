"""mcp.py — CKG MCP protocol module.

Declares tools and resources for the Cognitive Knowledge Graph.
Auto-discovery via ModuleRegistry.

NAMING (convención global — ver docs/design/memory-tools-naming-convention.md
y features/cognitive_knowledge/README.md):
  - La tool se llama por la OPERACIÓN de negocio (documenting/recalling),
    no por la infraestructura (ckg_/kg_).
  - Una tool por caso de uso: NO reintroducir dispatchers stringly-typed
    (ckg_analyze action=... está en vías de eliminación).
  - _project_id es SELECTOR de proyecto, nunca metadata de nodo.
Migración por fases: registrar nombre semántico nuevo + alias viejo deprecado.
"""
from __future__ import annotations

from shared.core.mcp_server import McpServer

from core.engine.interface.contracts import ModuleDefinition, RestRoute, ToolDefinition


def register(server: McpServer) -> None:
    # DEPRECATED: ckg_analyze es un dispatcher stringly-typed que duplica a los
    # casos de uso (SaveToCkgUseCase → save_to_ckg/documenting_knowledge,
    # GatherCkgContextUseCase → kg_search/recalling_knowledge). Se elimina por
    # convención de naming semántico; mantener SOLO como compat mientras
    # migran los clients del protocolo (ADR-023 §3, memory-tools-naming-convention).
    server.tool(
        name="ckg_analyze",
        description="Unified CKG analysis: search, save, active, supersede, context",
        handler=_public_task("ckg_analyze"),
    )


def register_module(registry: Any) -> None:
    module = ModuleDefinition(
        name="cognitive_knowledge",
        tools=[
            # DEPRECATED (dispatcher stringly-typed): se elimina por convención de
            # naming semántico — una tool por caso de uso (ver docstring del módulo).
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
                metadata={"tags": ["ckg", "unified", "analysis"], "deprecated": True},
            ),
            # Naming semántico (convención): kg_search → objetivo recalling_knowledge.
            ToolDefinition(name="kg_search", description="Search project memory (CKG).", handler=_public_task("kg_search"), metadata={"tags": ["kg", "search"]}),
            ToolDefinition(name="kg_multi", description="Federated query across multiple indices.", handler=_public_task("kg_multi"), metadata={"tags": ["kg", "federated"]}),
            # Naming semántico: save_to_ckg → objetivo documenting_knowledge.
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
    from core.engine.application.task_dispatcher import dispatch_public_task
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
    from core.engine.application.task_dispatcher import dispatch_public_task
    return dispatch_public_task("kg_multi", args.get("layers", args.get("query", {})))


def _save_to_ckg(args: dict) -> dict:
    from core.engine.application.task_dispatcher import dispatch_public_task
    body = args.get("body") or args.get("query") or args.get("search") or args
    if not isinstance(body, dict):
        body = dict(body or {})
    return dispatch_public_task("save_to_ckg", body)


def _public_task(name: str):
    def handler(args: dict) -> dict:
        from core.engine.application.task_dispatcher import dispatch_public_task
        return dispatch_public_task(name, args.get("body", args))
    return handler
