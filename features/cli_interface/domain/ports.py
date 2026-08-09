"""
domain/ports.py — Ports (interfaces) del Bounded Context "Protocol".

El dominio define QUÉ necesita; la infraestructura implementa CÓMO.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .entities import McpSession


class SessionRepository(ABC):
    """Port — persistencia del lifecycle de sesiones MCP."""

    @abstractmethod
    def save(self, session: McpSession) -> tuple["McpSession | None", list[str]]:
        pass

    @abstractmethod
    def load(self, session_id: str) -> tuple["McpSession | None", list[str]]:
        pass

    @abstractmethod
    def close(self, session_id: str) -> tuple[bool, list[str]]:
        pass
