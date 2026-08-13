"""
domain/values.py — Value Objects del Bounded Context "Protocol".

Pure, immutable, validated on construction.
Factory methods use return pattern: (value, errors).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from enum import Enum


# ── Patrones ──

_TOOL_NAME_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
# Versiones del protocolo MCP soportadas. opencode moderno envía la versión
# más reciente del spec: 2024-11-05 → 2025-03-26 → 2025-06-18 → 2025-11-25.
_PROTOCOL_VERSIONS = {"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"}


# ═══════════════════════════════════════════════════════════════════════
# Enums
# ═══════════════════════════════════════════════════════════════════════


class RpcMethod(str, Enum):
    """Métodos JSON-RPC 2.0 soportados por el protocolo MCP."""

    INITIALIZE = "initialize"
    NOTIFICATIONS_INITIALIZED = "notifications/initialized"
    TOOLS_LIST = "tools/list"
    TOOLS_CALL = "tools/call"
    RESOURCES_LIST = "resources/list"
    RESOURCES_READ = "resources/read"
    PROMPTS_LIST = "prompts/list"
    PROMPTS_GET = "prompts/get"
    PING = "ping"


class SessionState(str, Enum):
    """Lifecycle de una sesión MCP."""

    UNINITIALIZED = "uninitialized"
    NEGOTIATED = "negotiated"
    ACTIVE = "active"
    CLOSED = "closed"


# ═══════════════════════════════════════════════════════════════════════
# Value Objects
# ═══════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class SessionId:
    """VO — identidad única de una sesión MCP."""

    value: str

    @staticmethod
    def create(value: str | None = None) -> tuple["SessionId", list[str]]:
        """Factory. Genera UUID si no se provee valor.

        No puede fallar (siempre produce un id válido) — devuelve
        (session_id, []) por consistencia con el return pattern.
        """
        if value:
            return SessionId(str(value)), []
        return SessionId(uuid.uuid4().hex), []

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class ToolName:
    """VO — nombre de un tool MCP.

    Validado: `^[a-z_][a-z0-9_]*$`. Un tool = un handler.
    """

    value: str

    @staticmethod
    def create(value: str) -> tuple["ToolName | None", list[str]]:
        errors: list[str] = []
        if not value or not str(value).strip():
            errors.append("tool name cannot be empty")
        elif not _TOOL_NAME_RE.match(value):
            errors.append(f"Invalid tool name '{value}': must match [a-z_][a-z0-9_]*")
        if errors:
            return None, errors
        return ToolName(value), []

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class ProtocolVersion:
    """VO — versión del protocolo MCP negociada."""

    value: str

    @staticmethod
    def create(value: str) -> tuple["ProtocolVersion | None", list[str]]:
        errors: list[str] = []
        if value not in _PROTOCOL_VERSIONS:
            errors.append(
                f"Unsupported protocol version '{value}'. Supported: {sorted(_PROTOCOL_VERSIONS)}"
            )
        if errors:
            return None, errors
        return ProtocolVersion(value), []

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class UriPattern:
    """VO — patrón de URI de un recurso dinámico MCP.

    Ej: `walker://schema/{index}`, `walker://help/{tool}`.
    """

    value: str

    @staticmethod
    def create(value: str) -> tuple["UriPattern | None", list[str]]:
        errors: list[str] = []
        if not value or not str(value).strip():
            errors.append("uri pattern cannot be empty")
        elif not value.startswith("walker://"):
            errors.append(f"Invalid uri pattern '{value}': must start with 'walker://'")
        if errors:
            return None, errors
        return UriPattern(value), []

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class Capabilities:
    """VO — capacidades negociadas en `initialize`."""

    tools_list: bool = True
    resources: bool = True
    prompts: bool = True
    list_changed: bool = False
    subscribe: bool = False
