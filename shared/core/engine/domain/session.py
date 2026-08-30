"""
domain/session.py — Modelo de sesión (dato puro).

El session_id es la raíz de todo el sistema. Sin session_id
no hay ExecutionContext, sin ExecutionContext no hay operaciones.

SessionRecord es PURA (sin IO). La persistencia vive en
`infrastructure/session_store.py` (adapter del puerto ISessionStore):
el dominio no hace file-IO (ADR-WALKER-067, revisión de dominio).
"""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any


@dataclass
class SessionRecord:
    """Registro de sesión persistente (dato puro, sin IO).

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

        Derivación de ruta (pura): compone la ruta desde IConfig. La creación
        del directorio (IO) la hace el adapter ISessionStore.ensure_session_dir.
        """
        from .ports_registry import resolve_port
        from .ports import IConfig

        cfg = resolve_port(IConfig)
        if cfg is None:
            raise RuntimeError("IConfig no registrado en domain.ports_registry")
        base = cfg.get("sessions_dir")
        return os.path.join(base, self.session_id)

    @classmethod
    def create(cls, project_id: str = "", chain_dir: str = "") -> SessionRecord:
        """Crea una nueva sesión con session_id auto-generado."""
        return cls(
            session_id=f"sess-{uuid.uuid4().hex[:12]}",
            project_id=project_id,
            chain_dir=chain_dir,
        )
