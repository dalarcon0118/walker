"""
domain/errors.py — Domain errors del Bounded Context "Protocol".

Pure — no IO, no imports de otras features.
"""

from __future__ import annotations


class ProtocolError(Exception):
    """Base error del protocolo de entrada."""
    pass


class UnknownToolError(ProtocolError):
    """El tool invocado no está registrado."""

    def __init__(self, tool_name: str) -> None:
        self.tool_name = tool_name
        super().__init__(f"Unknown tool '{tool_name}'")


class ToolAlreadyExistsError(ProtocolError):
    """Registro de tool con nombre duplicado (shadowing)."""

    def __init__(self, tool_name: str) -> None:
        self.tool_name = tool_name
        super().__init__(f"Tool '{tool_name}' already registered")


class UnsupportedMethodError(ProtocolError):
    """Método JSON-RPC no soportado (usado por mcp_adapter._handle_request)."""

    def __init__(self, method: str) -> None:
        self.method = method
        super().__init__(f"Method '{method}' not supported")
