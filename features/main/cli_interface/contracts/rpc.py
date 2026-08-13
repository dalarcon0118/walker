"""
contracts/rpc.py — DTOs JSON-RPC 2.0 del protocolo MCP.

Definen el contrato wire del protocolo: requests, results y errores.
Sin lógica — puros dataclasses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from returns.pipeline import is_successful
from returns.result import safe

from ..domain.values import RpcMethod


# ── Requests ──


@dataclass(frozen=True)
class JsonRpcRequest:
    """DTO — request JSON-RPC 2.0 genérico."""

    method: RpcMethod
    id: str | int | None
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def is_notification(self) -> bool:
        """Notificación = sin id → no debe recibir respuesta."""
        return self.id is None

    @staticmethod
    def create(method: str, id_value: str | int | None, params: dict | None = None) -> tuple["JsonRpcRequest | None", list[str]]:
        method_outcome = _parse_rpc_method_core(method)
        if not is_successful(method_outcome):
            return None, [f"Unsupported method '{method}'"]
        rpc_method = method_outcome.value_or(RpcMethod(method))
        return JsonRpcRequest(method=rpc_method, id=id_value, params=params or {}), []


@safe
def _parse_rpc_method_core(method: str) -> RpcMethod:
    """Core puro: parsea el método JSON-RPC (puede lanzar ValueError)."""
    return RpcMethod(method)


@dataclass(frozen=True)
class InitializeRequest:
    """DTO — handshake MCP `initialize`."""

    protocol_version: str
    client_name: str
    client_version: str = ""
    capabilities: dict = field(default_factory=dict)

    @staticmethod
    def from_params(params: dict) -> "InitializeRequest":
        return InitializeRequest(
            protocol_version=params.get("protocolVersion", "2024-11-05"),
            client_name=params.get("clientInfo", {}).get("name", "unknown"),
            client_version=params.get("clientInfo", {}).get("version", ""),
            capabilities=params.get("capabilities", {}),
        )


@dataclass(frozen=True)
class ToolCallRequest:
    """DTO — `tools/call` con nombre y argumentos."""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def from_params(params: dict) -> "ToolCallRequest":
        return ToolCallRequest(
            name=params.get("name", ""),
            arguments=params.get("arguments", {}) or {},
        )


# ── Results ──


@dataclass(frozen=True)
class JsonRpcResult:
    """DTO — resultado JSON-RPC 2.0 genérico."""

    id: str | int | None
    result: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"jsonrpc": "2.0", "id": self.id, "result": self.result}


@dataclass(frozen=True)
class JsonRpcError:
    """DTO — error JSON-RPC 2.0 (estándar -32600, -32601, etc.)."""

    id: str | int | None
    code: int
    message: str
    data: dict[str, Any] | None = None

    def to_dict(self) -> dict:
        error = {"code": self.code, "message": self.message}
        if self.data is not None:
            error["data"] = self.data
        return {"jsonrpc": "2.0", "id": self.id, "error": error}
