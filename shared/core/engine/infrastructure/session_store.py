"""
infrastructure/session_store.py — Persistencia de sesiones (adapter ISessionStore).

INCUMBENCIA DE INFRAESTRUCTURA: la persistencia en disco de sesiones NO vive en
el dominio (ADR-WALKER-067, revisión de dominio). `SessionRecord` (dato puro)
vive en `domain/session.py`; ESTE adapter implementa `ISessionStore` y hace el
file-IO (json.dump/load, os.makedirs).

Misma API que el histórico SessionStore de domain/session.py (classmethods +
attrs de clase SESSIONS_DIR/ACTIVE_FILE para aislamiento en tests).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from shared.core.engine.domain.ports import ISessionStore


class SessionStore(ISessionStore):
    """Persistencia de sesiones en <sessions_dir>/.

    Cada sesión es un archivo JSON: <sessions_dir>/{session_id}.json
    La sesión activa es PER-AGENTE: <sessions_dir>/agents/<agent_id>/active_session
    Si no hay agente, usa <sessions_dir>/active_session (legacy compat)

    sessions_dir se resuelve via config (IConfig port).
    """

    SESSIONS_DIR = None  # Se resuelve via config
    ACTIVE_FILE = None   # Se resuelve via config

    @classmethod
    def _agent_id(cls) -> str:
        """Retorna el agent_id del entorno, o vacio."""
        return os.environ.get("WALKER_AGENT_ID", "")

    @classmethod
    def _sessions_dir(cls) -> str:
        if cls.SESSIONS_DIR is None:
            from shared.core.engine.domain.ports_registry import resolve_port
            from shared.core.engine.domain.ports import IConfig

            cfg = resolve_port(IConfig)
            if cfg is None:
                raise RuntimeError("IConfig no registrado en domain.ports_registry")
            cls.SESSIONS_DIR = cfg.get("sessions_dir")
        return cls.SESSIONS_DIR

    @classmethod
    def _active_file(cls) -> str:
        if cls.ACTIVE_FILE is None:
            sessions = cls._sessions_dir()
            agent = cls._agent_id()
            if agent:
                cls.ACTIVE_FILE = os.path.join(sessions, "agents", agent, "active_session")
            else:
                cls.ACTIVE_FILE = os.path.join(sessions, "active_session")
        return cls.ACTIVE_FILE

    @classmethod
    def _ensure_dir(cls) -> None:
        sdir = cls._sessions_dir()
        os.makedirs(sdir, exist_ok=True)
        af = cls._active_file()
        os.makedirs(os.path.dirname(af), exist_ok=True)

    @classmethod
    def _path(cls, session_id: str) -> str:
        return os.path.join(cls._sessions_dir(), f"{session_id}.json")

    @classmethod
    def save(cls, record: Any) -> None:
        """Persiste una sesión."""
        cls._ensure_dir()
        record.updated_at = datetime.now(timezone.utc).isoformat()
        with open(cls._path(record.session_id), "w") as f:
            json.dump(record.to_dict(), f, indent=2)

    @classmethod
    def load(cls, session_id: str) -> Any | None:
        """Carga una sesión por ID."""
        path = cls._path(session_id)
        if os.path.exists(path):
            try:
                from shared.core.engine.domain.session import SessionRecord

                with open(path) as f:
                    return SessionRecord.from_dict(json.load(f))
            except Exception:
                return None
        return None

    @classmethod
    def get_active(cls) -> Any | None:
        """Retorna la sesión activa (última usada)."""
        af = cls._active_file()
        if os.path.exists(af):
            try:
                with open(af) as f:
                    session_id = f.read().strip()
                    return cls.load(session_id)
            except Exception:
                return None
        return None

    @classmethod
    def set_active(cls, session_id: str) -> None:
        """Marca una sesión como activa."""
        cls._ensure_dir()
        with open(cls._active_file(), "w") as f:
            f.write(session_id)

    @classmethod
    def delete(cls, session_id: str) -> None:
        """Elimina una sesión."""
        path = cls._path(session_id)
        if os.path.exists(path):
            os.remove(path)

    @classmethod
    def list_sessions(cls) -> list:
        """Lista todas las sesiones disponibles."""
        cls._ensure_dir()
        sessions = []
        sdir = cls._sessions_dir()
        if os.path.isdir(sdir):
            for fname in os.listdir(sdir):
                if fname.endswith(".json"):
                    sess = cls.load(fname[:-5])
                    if sess:
                        sessions.append(sess)
        return sorted(sessions, key=lambda s: s.updated_at, reverse=True)

    @classmethod
    def find_by_chain_dir(cls, chain_dir: str) -> Any | None:
        """Retorna la sesión más reciente para un chain_dir (o None)."""
        for record in cls.list_sessions():
            if record.chain_dir == chain_dir:
                return record
        return None

    @classmethod
    def ensure_session_dir(cls, record: Any) -> str:
        """Crea el directorio de sesión del registro si no existe."""
        d = record.session_dir
        os.makedirs(d, exist_ok=True)
        return d

    # Última corrida de GC (timestamp epoch). Throttle horario para no
    # escanear el directorio en cada llamada CLI/MCP.
    _LAST_GC: float = 0.0
    GC_THROTTLE_SECONDS: float = 3600.0
    # Guarda de seguridad: el GC nunca actúa sobre directorios con menos
    # sesiones que este umbral.
    GC_STALE_MIN_SESSIONS: int = 50

    @classmethod
    def gc_stale(cls, max_hours: int = 168, min_sessions: int = 0) -> int:
        """Borra sesiones stale que no son la activa (GC periódico)."""
        import time as _time

        now = _time.time()
        if now - cls._LAST_GC < cls.GC_THROTTLE_SECONDS:
            return 0
        cls._LAST_GC = now

        sessions = cls.list_sessions()
        limit = min_sessions or cls.GC_STALE_MIN_SESSIONS
        if len(sessions) < limit:
            return 0

        active = cls.get_active()
        active_id = active.session_id if active else ""
        removed = 0
        for record in sessions:
            if record.session_id == active_id:
                continue
            if record.is_stale(max_hours=max_hours):
                cls.delete(record.session_id)
                removed += 1
        return removed