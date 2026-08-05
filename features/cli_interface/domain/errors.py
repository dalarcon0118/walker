"""
domain/errors.py — Domain errors del Bounded Context "Protocol".

Pure — no IO, no imports de otras features.
"""

from __future__ import annotations


class ProtocolError(Exception):
    """Base error del protocolo de entrada."""
    pass


class HandshakeError(ProtocolError):
    """El protocolo no está inicializado (handshake pendiente)."""

    def __init__(self) -> None:
        super().__init__(
            "Session not initialized. Call 'initialize' before any other method."
        )


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


class InvalidRequestError(ProtocolError):
    """El request no cumple el contrato JSON-RPC."""

    def __init__(self, message: str = "Invalid request") -> None:
        super().__init__(message)


class UnsupportedMethodError(ProtocolError):
    """Método JSON-RPC no soportado."""

    def __init__(self, method: str) -> None:
        self.method = method
        super().__init__(f"Method '{method}' not supported")


class DispatchError(ProtocolError):
    """Fallo al invocar el handler de un tool."""

    def __init__(self, tool_name: str, reason: str = "") -> None:
        self.tool_name = tool_name
        super().__init__(
            f"Tool '{tool_name}' dispatch failed" + (f": {reason}" if reason else "")
        )
