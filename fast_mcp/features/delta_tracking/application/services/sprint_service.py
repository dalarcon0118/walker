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
    _completion_missing,
    review_missing,
)

logger = logging.getLogger(__name__)


def _spec_to_dict(spec: Spec) -> dict:
    d = spec.to_dict()
    d["_id"] = spec.doc_id
    # Promover metadata de reopen (almacenada en task_context) a top-level
    # para que callers/tests la vean sin romper el schema.
    tc = d.get("task_context") or {}
    if isinstance(tc, dict):
        if "reopen_reason" in tc:
            d["reopen_reason"] = tc["reopen_reason"]
        if "reopened_at" in tc:
            d["reopened_at"] = tc["reopened_at"]
    # Atributos dinámicos (si los hubiera).
    for attr in ("reopen_reason", "reopened_at"):
        val = getattr(spec, attr, None)
        if val not in (None, "") and attr not in d:
            d[attr] = val
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

    def transition_spring(self, spring_id: str, status: str) -> dict:
        """Transición explícita con motivo (ADR-WALKER-078: `paused` reversible).

        Devuelve `{ok, from, to}` o `{ok: False, error}`. A diferencia de
        `set_spring_status`, un rechazo NO es silencioso: el llamador puede
        explicar por qué no se puede pasar de `closed` a `active`.
        """
        if not self._repo:
            return {"ok": False, "error": "repo no disponible"}
        spring = self._repo.get_spring(spring_id)
        if spring is None:
            return {"ok": False, "error": f"sprint desconocido: {spring_id}"}
        can, msg = SprintStatusUpdater.can_transition(spring.status, status)
        if not can:
            return {"ok": False, "error": msg, "from": spring.status, "to": status}
        before = spring.status
        spring.status = status
        spring.updated_at = _now_iso()
        self._repo.save_spring(spring)
        return {"ok": True, "spring_id": spring_id, "from": before, "to": status}

    def set_spring_status(self, spring_id: str, status: str) -> None:
        """Compatibilidad: aplica si es válido e ignora si no (ver transition)."""
        self.transition_spring(spring_id, status)

    def close_spring(
        self, spring_id: str, *, force: bool = False, reason: str = ""
    ) -> dict:
        """Cierra un sprint liquidando antes sus tareas (invariante de cierre).

        Un sprint `closed` con specs `in_progress` es estado incoherente: el
        trabajo quedó a medias pero el contenedor dice terminado. Reglas:

          1. Las specs en `in_review` que YA tienen los cuatro artefactos se
             completan solas: el trabajo estaba verificado, solo faltaba la
             transición (era el caso real del cierre 2026-11-19).
          2. Cualquier otra spec abierta BLOQUEA el cierre, con la lista de
             qué falta. El llamador completa con `spec_status` o fuerza.
          3. `force=True` exige `reason` y deja la deuda en
             `spring.context.closed_with_open_tasks`: el cierre excepcional es
             explícito y auditable, nunca silencioso.
        """
        if not self._repo:
            return {"ok": False, "error": "repo no disponible"}
        spring = self._repo.get_spring(spring_id)
        if spring is None:
            return {"ok": False, "error": f"sprint desconocido: {spring_id}"}
        if str(getattr(spring, "status", "")) == "closed":
            return {
                "ok": True,
                "spring_id": spring_id,
                "status": "closed",
                "idempotent": True,
                "settled": [],
                "open": [],
            }

        settled: list[str] = []
        for spec in self.get_specs(spring_id):
            if str(spec.get("status") or "") != "in_review":
                continue
            try:
                self.update_spec_status(spring_id, str(spec.get("spec_id")), "completed")
                settled.append(str(spec.get("spec_id")))
            except ValueError:
                continue  # sin artefactos: sigue abierta y se reporta abajo

        # Revisión independiente: una spec `completed` sin `reviewed_by` (o con
        # el autor como revisor) no habilita el cierre. El tablero puede decir
        # "todas completas" y aun así no haber pasado por revisión.
        unreviewed = [
            {
                "spec_id": str(spec.get("spec_id") or ""),
                "task_id": str(
                    (spec.get("task_context") or {}).get("task_id")
                    or spec.get("spec_id")
                    or ""
                ),
                "missing": review_missing(spec),
            }
            for spec in self.get_specs(spring_id)
            if str(spec.get("status") or "") == "completed" and review_missing(spec)
        ]

        open_specs = [
            {
                "spec_id": str(spec.get("spec_id") or ""),
                "task_id": str(
                    (spec.get("task_context") or {}).get("task_id")
                    or spec.get("spec_id")
                    or ""
                ),
                "status": str(spec.get("status") or ""),
                "missing": _completion_missing(spec),
            }
            for spec in self.get_specs(spring_id)
            if str(spec.get("status") or "") != "completed"
        ]

        if open_specs and not force:
            return {
                "ok": False,
                "error": (
                    f"No se puede cerrar {spring_id}: {len(open_specs)} tarea(s) sin "
                    "completar. Completalas con spec_status (status=completed + "
                    "artefactos) o cerra con force=true y un reason explicito."
                ),
                "spring_id": spring_id,
                "settled": settled,
                "open": open_specs,
                "unreviewed": unreviewed,
            }
        if unreviewed and not force:
            return {
                "ok": False,
                "error": (
                    f"No se puede cerrar {spring_id}: {len(unreviewed)} spec(s) "
                    "completada(s) sin revisión independiente. Registrá la revisión "
                    "con spec_status (status=completed, reviewed_by=<sesión o "
                    "persona distinta del autor>) o cerra con force=true y un "
                    "reason explícito."
                ),
                "spring_id": spring_id,
                "settled": settled,
                "open": open_specs,
                "unreviewed": unreviewed,
            }
        if open_specs and not str(reason or "").strip():
            return {
                "ok": False,
                "error": (
                    "force=true exige reason: cerrar con tareas abiertas debe ser "
                    "explicito y auditable."
                ),
                "spring_id": spring_id,
                "settled": settled,
                "open": open_specs,
            }

        result = self.transition_spring(spring_id, "closed")
        if not result.get("ok"):
            return {
                "ok": False,
                "error": result.get("error", "no se pudo cerrar el sprint"),
                "spring_id": spring_id,
                "settled": settled,
                "open": open_specs,
            }

        if open_specs or unreviewed:
            spring = self._repo.get_spring(spring_id)
            context = dict(getattr(spring, "context", None) or {})
            if open_specs:
                context["closed_with_open_tasks"] = open_specs
            if unreviewed:
                context["closed_without_review"] = unreviewed
            context["close_reason"] = str(reason).strip()
            context["closed_at"] = _now_iso()
            self.update_spring(spring_id, {"context": context})

        return {
            "ok": True,
            "spring_id": spring_id,
            "status": "closed",
            "settled": settled,
            "open": open_specs,
            "unreviewed": unreviewed,
            "forced": bool(open_specs or unreviewed),
        }

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

    def close_session(
        self,
        session_id: str,
        outcome: str = "",
        stamp: dict | None = None,
        cost_usd: float | None = None,
        tokens: dict | None = None,
    ) -> dict | None:
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
        if stamp:
            session.stamp = dict(stamp)
        # Costo por sesión: la jerarquía del delta lo publica tal cual, sin
        # recorrer la traza de cada hijo (F5.4).
        if isinstance(cost_usd, (int, float)):
            session.cost_usd = float(cost_usd)
        if isinstance(tokens, dict) and tokens:
            session.tokens = dict(tokens)
        self._repo.save_session(session)
        return _session_to_dict(session)

    def stamp_session(self, session_id: str, stamp: dict) -> dict | None:
        """Re-sella una sesión ACTIVA con la última verificación observada.

        El sello se escribe al cerrar, y una sesión puede reabrirse y volver a
        verificar: sin re-sellado el artefacto queda congelado en el primer
        cierre (`kind=none`) aunque después haya evidencia, y "cerrado sin
        verificar" pasa a mentir. Sólo avanza en el tiempo (`at` posterior) y
        nunca toca una sesión cerrada: ahí el sello es historia.
        """
        if not self._repo:
            return None
        session = self._repo.get_session(session_id)
        if session is None:
            return None
        if str(getattr(session, "status", "")) != "active":
            return None
        current = dict(getattr(session, "stamp", None) or {})
        new_at = str((stamp or {}).get("at") or "")
        old_at = str(current.get("at") or "")
        if old_at and new_at and new_at <= old_at:
            return None
        session.stamp = dict(stamp or {})
        session.updated_at = _now_iso()
        self._repo.save_session(session)
        return _session_to_dict(session)

    def update_session_plan(self, session_id: str, patch: dict) -> dict | None:
        """Merge del plan de vuelo (goal/acceptance/task/scope/next)."""
        if not self._repo:
            return None
        session = self._repo.get_session(session_id)
        if session is None:
            return None
        plan = dict(session.flight_plan or {})
        for key, value in (patch or {}).items():
            if value is None:
                continue
            plan[key] = value
        plan["updated_at"] = _now_iso()
        session.flight_plan = plan
        session.updated_at = plan["updated_at"]
        self._repo.save_session(session)
        return plan

    def get_session_plan(self, session_id: str) -> dict | None:
        session = self.get_session(session_id)
        return (session or {}).get("flight_plan") or None

    def list_sessions(
        self, status: str = "", project_id: str = "", limit: int = 0
    ) -> list[dict]:
        if not self._repo:
            return []
        sessions = [_session_to_dict(s) for s in self._repo.list_sessions(status, project_id)]
        sessions.sort(key=lambda s: s.get("started_at") or "", reverse=True)
        # Hijos derivados de `parent_session_id` dentro del mismo conjunto: no
        # hay índice inverso que mantener, y una sesión huérfana no desaparece.
        by_id = {s.get("session_id"): s for s in sessions}
        kids: dict[str, list[str]] = {}
        for s in sessions:
            parent = s.get("parent_session_id") or ""
            if parent and parent in by_id and parent != s.get("session_id"):
                kids.setdefault(parent, []).append(s.get("session_id") or "")
        for s in sessions:
            s["children"] = sorted(kids.get(s.get("session_id") or "", []))
        return sessions[:limit] if limit and limit > 0 else sessions

    @staticmethod
    def build_session_tree(sessions: list[dict]) -> list[dict]:
        """Bosque de sesiones: raíces + hijos anidados.

        Una sesión cuyo `parent_session_id` no está en el conjunto se trata como
        raíz: nunca se pierde una sesión por un padre ausente.
        """
        nodes = {
            s.get("session_id"): {**s, "children": []}
            for s in sessions
            if s.get("session_id")
        }
        roots: list[dict] = []
        for sid, node in nodes.items():
            parent = node.get("parent_session_id") or ""
            if parent and parent in nodes and parent != sid:
                nodes[parent]["children"].append(node)
            else:
                roots.append(node)
        roots.sort(key=lambda n: n.get("started_at") or "", reverse=True)
        return roots

    def session_tree(
        self, status: str = "", project_id: str = ""
    ) -> list[dict]:
        return self.build_session_tree(self.list_sessions(status=status, project_id=project_id))

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

    def update_spec_status(
        self,
        spring_id: str,
        spec_id: str,
        status: str,
        artifacts: dict | None = None,
    ) -> dict | None:
        """Update spec status, optionally writing the closing artifacts.

        `artifacts` se aplica al MISMO save que el status: la invariante de
        `completed` (completed_at/commit/review/evidence) se evalúa una sola
        vez, y no queda un estado intermedio con artefactos y sin transición.
        """
        if not self._repo:
            return None
        spec = self._repo.get_spec(spring_id, spec_id)
        if not spec:
            return None
        spec_dict = spec.to_dict()
        for key, value in (artifacts or {}).items():
            if value not in (None, "", []):
                spec_dict[key] = value
        updated = SpecStatusUpdater.update(spec_dict, status)
        merged = Spec.from_dict(updated)
        self._repo.save_spec(merged)
        return _spec_to_dict(merged)

    def reopen_spec(self, spring_id: str, spec_id: str, reason: str = "") -> dict | None:
        """Reabre una spec volviendo a `specified`, registrando el motivo.

        Vía explícita que bypassa la prohibición de retroceso del flujo normal.
        """
        if not self._repo:
            return None
        spec = self._repo.get_spec(spring_id, spec_id)
        if not spec:
            return None
        reopened = SpecService.reopen(spec, reason)
        self._repo.save_spec(reopened)
        return _spec_to_dict(reopened)

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
