"""Holder leaf para el cliente MCP.

Romp el ciclo de importación de composición:

    protocols.cli.cli_interface (pkg) -> handlers -> mcp_client -> mcp_adapter -> pkg

Los handlers consumen el "cliente MCP" vía este holder en lugar de importar
protocols.cli.cli_interface.application.mcp_client (que importa mcp_adapter y
cerraría el ciclo). El holder es una HOJA: NO importa mcp_client ni mcp_adapter.

Se popula en tiempo de ejecución (nunca en import-time):
  - CLI: protocols.cli.cli_interface.__init__.main() llama get_mcp_client()
    (de mcp_client.py) al arrancar; eso construye un MCPClient y lo registra.
  - Server: protocols.mcp.mcp_adapter._get_server() registra el McpServer
    tras construirlo (el server expone list_tools/call_tool, la misma API que
    usan handle_help / handle_mcp_tools / handle_mcp_routes).
"""
from __future__ import annotations

from typing import Any

_mcp_client: Any | None = None


def set_mcp_client(client: Any) -> None:
    """Registra la instancia cliente/server para los handlers."""
    global _mcp_client
    _mcp_client = client


def get_mcp_client() -> Any | None:
    """Devuelve la instancia registrada (MCPClient en CLI, McpServer en server)."""
    return _mcp_client
