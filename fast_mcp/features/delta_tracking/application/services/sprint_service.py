"""application/services/sprint_service.py — SprintService (thin orchestrator).

Application service in Clean Architecture. Orchestrates sprint/spec
operations by delegating to the DeltaRepository. No business logic —
just DI and delegation.

Replaces the legacy SprintApplicationService (17 use cases → direct methods).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Callable

from fast_mcp.features.delta_tracking.application.ports.delta_repository import DeltaRepository
from fast_mcp.features.delta_tracking.domain.entities import Session, Spring, Spec
from fast_mcp.features.delta_tracking.domain.values import (
    spec_id_from_index,
    spring_id_from_chain_dir,
)
from fast_mcp.features.delta_tracking.domain.spec_service import (
    SpecValidator,
    SpecStatusUpdater,
    SprintStatusUpdater,
    SpecService,
)

logger = logging.getLogger(__name__)


def _spec_to_dict(spec: Spec) -> dict:
    d = spec.to_dict()
    d["_id"] = spec.doc_id
    return d


def _spring_to_dict(spring: Spring) -> dict:
    d = spring.to_dict()
    d["_id"] = spring.spring_id
    return d


def _session_to_dict(session: Session) -> dict:
    return session.to_dict()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SprintService:
    """Thin orchestrator: delegates to DeltaRepository.

    This is the public facade consumed by MCP handlers.
    It replaces the legacy SprintApplicationService with 17 use cases.
    """

    def __init__(self, project_root: str = "", repo: DeltaRepository | None = None):
        self.project_root = os.path.normpath(project_root) if project_root else ""
        self._repo = repo

    @classmethod
    def from_chain_dir(cls, chain_dir: str, path_resolver=None) -> SprintService | None:
        """Build SprintService from chain_dir (resolves project_root).

        Args:
            chain_dir: Directory of the sprint.
            path_resolver: Injectable IProjectContext.resolve_paths (DI).
        """
        if not chain_dir:
            return None
        if path_resolver is None:
            logger.warning("from_chain_dir: no path_resolver injected for %s", chain_dir)
            return None
        try:
            paths = path_resolver(chain_dir)
            data_dir = getattr(paths, "data_dir", "") or ""
            project_root = os.path.dirname(data_dir) if data_dir else ""
            if project_root and os.path.isdir(project_root):
                return cls(project_root=project_root)
        except Exception as e:
            logger.warning("resolve/init failed for %s: %s", chain_dir, e)
        return None

    # ── Spring lifecycle ──

    def create_spring(
        self,
        spring_id: str,
        goal: str = "",
        status: str = "planning",
        project_id: str = "",
        session_id: str = "",
        **extra,
    ) -> dict:
        """Create a new spring in the delta."""
        spring = Spring(
            spring_id=spring_id,
            goal=goal,
            status=status,
            project_id=project_id,
            session_id=session_id,
            **extra,
        )
        if self._repo:
            self._repo.save_spring(spring)
        return _spring_to_dict(spring)

    def get_spring(self, spring_id: str) -> dict | None:
        """Get a spring by ID."""
        if not self._repo:
            return None
        s = self._repo.get_spring(spring_id)
        return _spring_to_dict(s) if s else None

    def get_active_spring(self) -> dict | None:
        """Get the active spring."""
        if not self._repo:
            return None
        s = self._repo.get_active_spring()
        return _spring_to_dict(s) if s else None

    def list_springs(self, status: str = "") -> list[dict]:
        """List springs, optionally filtered by status."""
        if not self._repo:
            return []
        return [_spring_to_dict(s) for s in self._repo.list_springs(status)]

    def get_spring_id(self, chain_dir: str) -> str:
        """Derive spring_id from chain_dir."""
        return spring_id_from_chain_dir(chain_dir)

    def set_spring_status(self, spring_id: str, status: str) -> None:
        """Update spring status."""
        if not self._repo:
            return
        spring = self._repo.get_spring(spring_id)
        if spring:
            can, msg = SprintStatusUpdater.can_transition(spring.status, status)
            if can:
                spring.status = status
                self._repo.save_spring(spring)

    def update_spring(self, spring_id: str, data: dict) -> None:
        """Update spring fields."""
        if not self._repo:
            return
        spring = self._repo.get_spring(spring_id)
        if spring:
            for k, v in data.items():
                if hasattr(spring, k) and v is not None:
                    setattr(spring, k, v)
            self._repo.save_spring(spring)

    # ── Sessions (registro de continuidad) ──
    #
    # La sesión es la unidad del AGENTE; el sprint la del proyecto. Viven en el
    # mismo delta para que el join por session_id no dependa de archivos aparte.

    def get_session(self, session_id: str) -> dict | None:
        if not self._repo:
            return None
        s = self._repo.get_session(session_id)
        return _session_to_dict(s) if s else None

    def open_session(
        self,
        session_id: str,
        project_id: str = "",
        agent: str = "",
        model: str = "",
        parent_session_id: str = "",
        spring_id: str = "",
        goal: str = "",
        context: dict | None = None,
    ) -> dict:
        """Alta idempotente: si existe, actualiza; no pisa `started_at`."""
        if not self._repo:
            raise ValueError("repo no disponible")
        now = _now_iso()
        existing = self._repo.get_session(session_id)
        if existing:
            existing.updated_at = now
            existing.status = "active"
            existing.ended_at = ""
            if agent:
                existing.agent = agent
            if model:
                existing.model = model
            if parent_session_id:
                existing.parent_session_id = parent_session_id
            if spring_id:
                existing.spring_id = spring_id
            if goal:
                existing.goal = goal
            if project_id and not existing.project_id:
                existing.project_id = project_id
            if context:
                existing.context = {**(existing.context or {}), **context}
            session = existing
        else:
            session = Session(
                session_id=session_id,
                project_id=project_id,
                agent=agent,
                model=model,
                status="active",
                goal=goal,
                spring_id=spring_id,
                parent_session_id=parent_session_id,
                started_at=now,
                updated_at=now,
                context=context or {},
            )
        self._repo.save_session(session)
        return _session_to_dict(session)

    def link_session_spring(
        self, session_id: str, spring_id: str, goal: str = "", parent_session_id: str = ""
    ) -> dict | None:
        if not self._repo:
            return None
        session = self._repo.get_session(session_id)
        if session is None:
            return None
        session.spring_id = spring_id
        session.status = "active"
        session.updated_at = _now_iso()
        if goal:
            session.goal = goal
        if parent_session_id:
            session.parent_session_id = parent_session_id
        self._repo.save_session(session)
        return _session_to_dict(session)

    def close_session(self, session_id: str, outcome: str = "") -> dict | None:
        if not self._repo:
            return None
        session = self._repo.get_session(session_id)
        if session is None:
            return None
        now = _now_iso()
        session.status = "closed"
        session.ended_at = now
        session.updated_at = now
        if outcome:
            session.outcome = outcome
        self._repo.save_session(session)
        return _session_to_dict(session)

    def list_sessions(
        self, status: str = "", project_id: str = "", limit: int = 0
    ) -> list[dict]:
        if not self._repo:
            return []
        sessions = [_session_to_dict(s) for s in self._repo.list_sessions(status, project_id)]
        sessions.sort(key=lambda s: s.get("started_at") or "", reverse=True)
        return sessions[:limit] if limit and limit > 0 else sessions

    def last_session(self, project_id: str = "") -> dict | None:
        sessions = self.list_sessions(project_id=project_id, limit=1)
        return sessions[0] if sessions else None

    # ── Specs ──

    def add_spec(
        self,
        spring_id: str,
        objective: str,
        domain: str = "",
        spec_type: str = "feature",
        spec_index: int | None = None,
        result_state: str = "",
        files: list[str] | None = None,
        status: str = "draft",
        description: str = "",
    ) -> dict:
        """Add a spec to a spring."""
        # Auto-assign spec_index if not provided.
        # `len(existing)` colisiona después de borrar: con 17 specs (SPEC-008..024)
        # el índice 17 genera SPEC-018, que YA existe, y save_spec lo sobreescribe
        # en silencio (pasó con la spec de `snapshot-diff`). Se usa el máximo + 1
        # y se verifica que el id resultante esté libre.
        if spec_index is None and self._repo:
            existing = self._repo.get_specs(spring_id)
            used = [s.spec_index for s in existing]
            taken = {s.spec_id for s in existing}
            spec_index = (max(used) + 1) if used else 0
            while spec_id_from_index(spec_index) in taken:
                spec_index += 1

        spec = Spec(
            spring_id=spring_id,
            objective=objective,
            domain=domain,
            spec_type=spec_type,
            spec_index=spec_index or 0,
            result_state=result_state,
            files=files or [],
            status=status,
            description=description,
        )
        if self._repo:
            self._repo.save_spec(spec)
        return _spec_to_dict(spec)

    def get_specs(self, spring_id: str, status: str = "") -> list[dict]:
        """Get all specs for a spring."""
        if not self._repo:
            return []
        return [_spec_to_dict(s) for s in self._repo.get_specs(spring_id, status)]

    def get_spec(self, spring_id: str, spec_id: str) -> dict | None:
        """Get a spec by ID."""
        if not self._repo:
            return None
        s = self._repo.get_spec(spring_id, spec_id)
        return _spec_to_dict(s) if s else None

    def update_spec(self, spring_id: str, spec_id: str, updates: dict) -> dict | None:
        """Update a spec."""
        if not self._repo:
            return None
        spec = self._repo.get_spec(spring_id, spec_id)
        if not spec:
            return None
        merged = SpecService.merge(spec, updates)
        self._repo.save_spec(merged)
        return _spec_to_dict(merged)

    def delete_spec(self, spring_id: str, spec_id: str) -> bool:
        """Borra una spec del delta. False si no existía o no hay repo.

        El repo ya tenía `delete_spec`; el servicio no lo exponía, así que la
        única forma de limpiar specs basura era tocar infraestructura.
        """
        if not self._repo:
            return False
        if not self._repo.get_spec(spring_id, spec_id):
            return False
        self._repo.delete_spec(spring_id, spec_id)
        return True

    def update_spec_status(self, spring_id: str, spec_id: str, status: str) -> dict | None:
        """Update spec status."""
        if not self._repo:
            return None
        spec = self._repo.get_spec(spring_id, spec_id)
        if not spec:
            return None
        spec_dict = spec.to_dict()
        updated = SpecStatusUpdater.update(spec_dict, status)
        merged = Spec.from_dict(updated)
        self._repo.save_spec(merged)
        return _spec_to_dict(merged)

    # ── Engine integration ──

    def load_tasks(self, chain_dir: str) -> list[dict]:
        """Load tasks from the sprint (for workflow engine).

        Returns specs as task dicts (task_id, objective, status, etc.).
        """
        if not chain_dir or not self._repo:
            return []
        spring_id = spring_id_from_chain_dir(chain_dir)
        specs = self._repo.get_specs(spring_id)
        return [_spec_to_dict(s) for s in specs]

    def get_sprint_progress(self, spring_id: str) -> dict:
        """Get sprint progress summary."""
        if not self._repo:
            return {"spring_id": spring_id, "total": 0, "completed": 0}
        specs = self._repo.get_specs(spring_id)
        completed = [s for s in specs if s.status == "completed"]
        return {
            "spring_id": spring_id,
            "total": len(specs),
            "completed": len(completed),
            "specs": [_spec_to_dict(s) for s in specs],
        }
