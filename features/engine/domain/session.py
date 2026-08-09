"""
domain/session.py — Modelo de sesión y SessionStore.

El session_id es la raíz de todo el sistema. Sin session_id
no hay ExecutionContext, sin ExecutionContext no hay operaciones.
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any


@dataclass
class SessionRecord:
    """Registro de sesión persistente.

    SSOT: .cognitive/sessions/{session_id}.json (resuelto via config)
    Contiene todo lo necesario para reconstruir un ExecutionContext.
    """
    session_id: str
    project_id: str = ""
    chain_dir: str = ""
    task_id: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_read_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_written_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    status: str = "active"  # active | free | terminated

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> SessionRecord:
        return cls(**data)

    def touch_read(self) -> None:
        """Actualiza last_read_at (llamado por hook pre-tool)."""
        self.last_read_at = datetime.now(timezone.utc).isoformat()
        self.updated_at = self.last_read_at

    def touch_write(self) -> None:
        """Actualiza last_written_at (llamado por hook post-tool)."""
        self.last_written_at = datetime.now(timezone.utc).isoformat()
        self.updated_at = self.last_written_at

    def is_stale(self, max_hours: int = 2) -> bool:
        """True si la sesión no ha tenido actividad en >max_hours."""
        try:
            last = datetime.fromisoformat(self.last_read_at)
            delta = datetime.now(timezone.utc) - last
            return delta.total_seconds() > max_hours * 3600
        except Exception:
            return False

    @property
    def mode(self) -> str:
        """'task' si hay chain_dir y status active, 'free' en otro caso."""
        return "task" if self.chain_dir and self.status == "active" else "free"

    @property
    def session_dir(self) -> str:
        """Directorio de sesion, usado como chain_dir virtual durante bootstrap.
        
        Cuando no hay proyecto activo (START node), el engine usa este directorio
        como chain_dir temporal para persistir el estado del motor
        (.workflow-state.json).
        """
        from ..infrastructure.config import get as cfg
        base = cfg("sessions_dir")
        return os.path.join(base, self.session_id)

    def ensure_session_dir(self) -> str:
        """Crea el directorio de sesion si no existe. Retorna la ruta."""
        d = self.session_dir
        os.makedirs(d, exist_ok=True)
        return d

    @classmethod
    def create(cls, project_id: str = "", chain_dir: str = "") -> SessionRecord:
        """Crea una nueva sesión con session_id auto-generado."""
        return cls(
            session_id=f"sess-{uuid.uuid4().hex[:12]}",
            project_id=project_id,
            chain_dir=chain_dir,
        )


class SessionStore:
    """Persistencia de sesiones en <sessions_dir>/.

    Cada sesión es un archivo JSON: <sessions_dir>/{session_id}.json
    La sesión activa es PER-AGENTE: <sessions_dir>/agents/<agent_id>/active_session
    Si no hay agente, usa <sessions_dir>/active_session (legacy compat)

    sessions_dir se resuelve via config (config/config.json del proyecto).
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
            from ..infrastructure.config import get
            cls.SESSIONS_DIR = get("sessions_dir")
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
    def save(cls, record: SessionRecord) -> None:
        """Persiste una sesión."""
        cls._ensure_dir()
        record.updated_at = datetime.now(timezone.utc).isoformat()
        with open(cls._path(record.session_id), "w") as f:
            json.dump(record.to_dict(), f, indent=2)

    @classmethod
    def load(cls, session_id: str) -> SessionRecord | None:
        """Carga una sesión por ID."""
        path = cls._path(session_id)
        if os.path.exists(path):
            try:
                with open(path) as f:
                    return SessionRecord.from_dict(json.load(f))
            except Exception:
                return None
        return None

    @classmethod
    def get_active(cls) -> SessionRecord | None:
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
    def list_sessions(cls) -> list[SessionRecord]:
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
    def find_by_chain_dir(cls, chain_dir: str) -> SessionRecord | None:
        """Retorna la sesión más reciente para un chain_dir (o None).

        Evita la acumulación de sesiones duplicadas: el API de workflow
        reutiliza la sesión de un chain_dir en vez de crear una nueva en
        cada llamada cuando esta no está stale.
        """
        for record in cls.list_sessions():
            if record.chain_dir == chain_dir:
                return record
        return None
