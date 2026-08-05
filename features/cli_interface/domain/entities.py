"""
domain/entities.py — Entities del Bounded Context "Protocol".

Tool, Resource y McpSession. El aggregate ToolRegistry vive en services.py.
Pure — sin IO, sin imports de infra.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .values import (
    ProtocolVersion,
    SessionId,
    SessionState,
    ToolName,
    UriPattern,
    Capabilities,
)
from .errors import HandshakeError


# ═══════════════════════════════════════════════════════════════════════
# Tool — registro inmutable de un tool MCP
# ═══════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class Tool:
    """Entity — registro inmutable de un tool MCP.

    `input_schema` es el SSOT del contrato que ve el agente.
    """

    name: ToolName
    description: str
    input_schema: dict
    handler: Callable[[dict], dict]

    @staticmethod
    def create(
        name: str,
        description: str,
        input_schema: dict | None = None,
        handler: Callable[[dict], dict] | None = None,
    ) -> tuple["Tool | None", list[str]]:
        """Factory con validación. Returns (tool, errors)."""
        errors: list[str] = []
        tool_name, name_errors = ToolName.create(name)
        if name_errors:
            errors.extend(name_errors)
            return None, errors
        if not description or not str(description).strip():
            errors.append("tool description cannot be empty")
        if handler is None:
            errors.append(f"tool '{name}' requires a handler")
        if errors:
            return None, errors
        return Tool(
            name=tool_name,  # type: ignore[arg-type]
            description=description,
            input_schema=input_schema or {"type": "object"},
            handler=handler,  # type: ignore[arg-type]
        ), []

    def to_definition(self) -> dict:
        """Proyección al contrato del agente (DTO para tools/list)."""
        return {
            "name": self.name.value,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


# ═══════════════════════════════════════════════════════════════════════
# Resource — recurso dinámico MCP
# ═══════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class Resource:
    """Entity — recurso dinámico MCP (uri pattern + handler)."""

    uri_pattern: UriPattern
    name: str
    description: str
    mime_type: str
    handler: Callable[[dict], dict]


# ═══════════════════════════════════════════════════════════════════════
# McpSession — lifecycle del protocolo
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class McpSession:
    """Entity — sesión MCP con lifecycle del protocolo.

    Invariantes:
      - no se puede invocar tool antes de `initialize` (handshake)
      - capabilities se negocian UNA vez
      - notifications (sin id) nunca devuelven respuesta
    """

    session_id: SessionId
    protocol_version: ProtocolVersion
    capabilities: Capabilities
    state: SessionState = SessionState.UNINITIALIZED
    client_name: str = ""
    client_version: str = ""
    _events: list[object] = field(default_factory=list, repr=False, init=False)

    @staticmethod
    def create(protocol_version: str = "2024-11-05") -> tuple["McpSession | None", list[str]]:
        """Factory. Returns (session, errors)."""
        version, errors = ProtocolVersion.create(protocol_version)
        if errors:
            return None, errors
        session_id, _sid_errors = SessionId.create()
        return McpSession(
            session_id=session_id,
            protocol_version=version,  # type: ignore[arg-type]
            capabilities=Capabilities(),
        ), []

    # ── Lifecycle ──

    def initialize(self, client_name: str, client_version: str = "") -> list[str]:
        """Negocia el handshake. Publishes SessionInitialized."""
        if self.state == SessionState.CLOSED:
            return ["session already closed"]
        if self.state == SessionState.NEGOTIATED:
            return []  # idempotente — ya inicializada
        self.state = SessionState.NEGOTIATED
        self.client_name = client_name
        self.client_version = client_version
        from .events import SessionInitialized

        self._events.append(
            SessionInitialized(
                session_id=self.session_id.value,
                protocol_version=self.protocol_version.value,
                client_name=client_name,
                client_version=client_version,
            )
        )
        return []

    def activate(self) -> list[str]:
        """Marca la sesión como activa (post handshake)."""
        if self.state == SessionState.UNINITIALIZED:
            return ["session not initialized"]
        self.state = SessionState.ACTIVE
        return []

    def close(self) -> list[str]:
        self.state = SessionState.CLOSED
        return []

    # ── Guards ──

    def require_initialized(self) -> list[str]:
        """Guarda: no se puede operar sin handshake."""
        if self.state in (SessionState.UNINITIALIZED,):
            return ["session not initialized"]
        return []

    def emit(self, event: object) -> None:
        """Registra un evento (observabilidad)."""
        self._events.append(event)

    def collect_events(self) -> list[object]:
        events = list(self._events)
        self._events.clear()
        return events
