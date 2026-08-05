"""libs/mcp_server.py — McpServer: composite MCP protocol router.

Cada modulo del walker declara sus tools/resources via register(server).
McpServer compone todos los modulos y rutea JSON-RPC requests.

ARQUITECTURA COMPOSITE:
    McpServer
      ├─► cognitive_knowledge/mcp.py → tools (kg_search, kg_store) + resources
      ├─► code_analysis/mcp.py       → tools (cpg_business, analyze_code) + resources
      ├─► delta_tracking/mcp.py      → tools (capture_task, import_sprint) + resources
      ├─► event_tracing/mcp.py       → tools (trace_push) + resources
      └─► engine/mcp.py              → tools (submit, transition, status) + resources

Cada modulo registra: tools, resources, prompts. El servidor los agrega.
El schema de cada tool se descubre via recursos (walker://help/{tool}).
"""
from __future__ import annotations

from typing import Any, Callable


class McpServer:
    """Composite MCP protocol server. Routes JSON-RPC requests to registered modules."""

    def __init__(self):
        self._tools: list[dict] = []
        self._resources: list[dict] = []
        self._prompts: list[dict] = []
        self._tool_handlers: dict[str, Callable] = {}
        self._resource_handlers: dict[str, Callable] = {}

    # ── Registration API ──

    def tool(
        self,
        name: str,
        description: str,
        handler: Callable[[dict], dict],
        input_schema: dict | None = None,
    ) -> None:
        """Register a tool with optional input schema.

        If input_schema is None, the tool accepts any dict as query.
        The schema can be discovered at runtime via walker://help/{name}.
        """
        schema = input_schema or {"type": "object", "properties": {"query": {"type": "object"}}}
        self._tools.append({
            "name": name,
            "description": description,
            "inputSchema": schema,
        })
        self._tool_handlers[name] = handler

    def resource(
        self,
        uri_pattern: str,
        name: str,
        description: str,
        handler: Callable[[dict], dict],
    ) -> None:
        """Register a dynamic resource with URI pattern.

        URI patterns support {param} placeholders.
        The handler receives {"uri": full_uri, "params": {extracted}}.
        """
        self._resources.append({
            "uri": uri_pattern,
            "name": name,
            "description": description,
            "mimeType": "application/json",
        })
        self._resource_handlers[uri_pattern] = handler

    def prompt(self, name: str, description: str) -> None:
        self._prompts.append({"name": name, "description": description})

    # ── Composition ──

    def compose(self, module_register: Callable[["McpServer"], None]) -> "McpServer":
        """Register all tools/resources from a module. Returns self for chaining."""
        module_register(self)
        return self

    # ── Read-only access (for JSON-RPC responses) ──

    def list_tools(self) -> list[dict]:
        return self._tools

    def list_resources(self) -> list[dict]:
        return self._resources

    def list_prompts(self) -> list[dict]:
        return self._prompts

    # ── Routing ──

    def call_tool(self, name: str, args: dict) -> dict:
        handler = self._tool_handlers.get(name)
        if handler is None:
            return {"error": f"Tool '{name}' not found"}
        return handler(args)

    def read_resource(self, uri: str) -> dict:
        """Resolve a resource URI to its handler."""
        for pattern, handler in self._resource_handlers.items():
            params = self._match_uri(pattern, uri)
            if params is not None:
                return handler({"uri": uri, "params": params})
        return {"error": f"Resource '{uri}' not found"}

    @staticmethod
    def _match_uri(pattern: str, uri: str) -> dict | None:
        """Match URI pattern with {placeholders} against actual URI."""
        pattern_parts = pattern.split("/")
        uri_parts = uri.split("/")
        if len(pattern_parts) != len(uri_parts):
            return None
        params = {}
        for pp, up in zip(pattern_parts, uri_parts):
            if pp.startswith("{") and pp.endswith("}"):
                params[pp[1:-1]] = up
            elif pp != up:
                return None
        return params
