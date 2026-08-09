"""cli_interface/application/mcp_client.py — Cliente MCP para el CLI.

Permite que los handlers del CLI consuman el MCP server internamente
en lugar de llamar a REST directo. Así el CLI es un cliente MCP más.

Uso:
  result = mcp_client.call_tool("status", {"project_id": "walker"})
  result = mcp_client.call_tool("submit", {"body": {...}})
"""
from __future__ import annotations

from typing import Any


class MCPClient:
    """Cliente MCP interno para el CLI."""

    def __init__(self, server: Any | None = None):
        self._server = server

    def call_tool(self, name: str, args: dict) -> dict:
        """Llama a una tool MCP y retorna el resultado."""
        try:
            if self._server is None:
                from features.cli_interface.mcp_adapter import _get_server
                self._server = _get_server()
            result = self._server.call_tool(name, args)
            if isinstance(result, dict):
                return result
            return {"result": result}
        except Exception as e:
            return {"error": str(e)}

    def list_tools(self) -> list[dict]:
        """Lista todas las tools MCP disponibles."""
        try:
            if self._server is None:
                from features.cli_interface.mcp_adapter import _get_server
                self._server = _get_server()
            return self._server.list_tools()
        except Exception:
            return []

    def get_tool_schema(self, name: str) -> dict | None:
        """Obtiene el schema de una tool MCP."""
        try:
            tools = self.list_tools()
            for tool in tools:
                if tool.get("name") == name:
                    return tool.get("inputSchema")
        except Exception:
            pass
        return None


# Singleton para evitar múltiples instancias del McpServer
_mcp_client: MCPClient | None = None


def get_mcp_client() -> MCPClient:
    """Obtiene el cliente MCP singleton."""
    global _mcp_client
    if _mcp_client is None:
        _mcp_client = MCPClient()
    return _mcp_client
