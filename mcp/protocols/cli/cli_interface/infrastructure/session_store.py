"""
infrastructure/session_store.py — InMemorySessionRepository (port impl).

Implementa SessionRepository (domain/ports.py) — persistencia del lifecycle
de sesiones MCP en memoria (single-process, por eso in-memory es suficiente).

Return-pattern: (result, errors) — nunca lanza.
"""

from __future__ import annotations

from ..domain.entities import McpSession
from ..domain.ports import SessionRepository


class InMemorySessionRepository(SessionRepository):
    """Repositorio in-memory de McpSession."""

    def __init__(self) -> None:
        self._sessions: dict[str, McpSession] = {}

    # ── SessionRepository ──

    def save(self, session: McpSession) -> tuple[McpSession | None, list[str]]:
        """Guarda una sesión. Returns (session, errors)."""
        if session is None:
            return None, ["session is required"]
        self._sessions[session.session_id.value] = session
        return session, []

    def load(self, session_id: str) -> tuple[McpSession | None, list[str]]:
        """Carga una sesión por id. Returns (session, errors)."""
        session = self._sessions.get(session_id)
        if session is None:
            return None, [f"session not found: '{session_id}'"]
        return session, []

    def close(self, session_id: str) -> tuple[bool, list[str]]:
        """Cierra (elimina) una sesión. Returns (closed, errors)."""
        if session_id not in self._sessions:
            return False, [f"session not found: '{session_id}'"]
        self._sessions[session_id].close()
        del self._sessions[session_id]
        return True, []

    # ── Utilidades para testing/inspección ──

    def list_sessions(self) -> list[str]:
        """Ids de sesiones activas (útil para diagnóstico)."""
        return sorted(self._sessions.keys())

    def count(self) -> int:
        return len(self._sessions)
