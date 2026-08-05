"""
domain/events.py — Domain events del Bounded Context "Protocol".

Immutable value objects. Solo eventos con consumidores reales
(event_tracing / CKG). No por estética.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True)
class SessionInitialized:
    """Emitido al completar el handshake `initialize`."""

    session_id: str
    protocol_version: str
    client_name: str
    client_version: str
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


@dataclass(frozen=True)
class ToolRegistered:
    """Emitido cuando un tool se registra en el ToolRegistry."""

    tool_name: str
    session_id: str = ""
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


@dataclass(frozen=True)
class ToolInvoked:
    """Emitido al invocar un tool (observabilidad)."""

    session_id: str
    tool_name: str
    args_hash: str
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


@dataclass(frozen=True)
class ToolFailed:
    """Emitido cuando el dispatch de un tool falla."""

    tool_name: str
    error_type: str
    session_id: str = ""
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


@dataclass(frozen=True)
class ServerReloaded:
    """Emitido tras el hot-reload de módulos de features."""

    modules_reloaded: list[str]
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
