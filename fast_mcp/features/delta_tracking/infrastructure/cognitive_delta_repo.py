"""infrastructure/cognitive_delta_repo.py — DeltaRepository via CognitiveGraph.

Infrastructure layer in Clean Architecture. Translates domain entities
(Spring, Spec) to CognitiveGraph operations (store, search, edges).

Uses shared_kernel.cognitive_graph (not legacy mcp/ code).
"""
from __future__ import annotations

import logging
from typing import Any

from fast_mcp.features.delta_tracking.application.ports.delta_repository import DeltaRepository
from fast_mcp.features.delta_tracking.domain.entities import Session, Spring, Spec
from fast_mcp.features.delta_tracking.domain.values import (
    TYPE_SPRING,
    TYPE_SPEC,
    TYPE_SESSION,
    spec_doc_id,
)

logger = logging.getLogger(__name__)


class DeltaWriteError(RuntimeError):
    """La escritura del delta no se persistió (INV-016: sin pérdida silenciosa).

    `CognitiveGraph.store` devuelve un Result-dict que puede traer `ok: False`
    (p. ej. CAS de generación agotado tras el reintento). Descartarlo convertía
    un conflicto real en un "ok" mentiroso: la spec/plan no quedaba en el
    delta y los gates de arriba actuaban sobre un estado vacío.
    """


def _require_ok(result: Any, what: str) -> None:
    """Fail-fast si el backend rechazó la escritura (tolerante a fakes)."""
    if isinstance(result, dict) and result.get("ok") is False:
        detail = str(result.get("error") or "escritura rechazada")
        raise DeltaWriteError(f"{what}: {detail}")


class CognitiveDeltaRepository(DeltaRepository):
    """DeltaRepository implementation using CognitiveGraph.

    The graph is injected via DI (composition root), not resolved
    from a service locator.
    """

    INDEX = "delta"

    def __init__(self, cg: Any):
        """Initialize with an injected CognitiveGraph instance.

        Args:
            cg: CognitiveGraph instance (injected via DI).
        """
        if cg is None:
            raise ValueError(
                "CognitiveDeltaRepository requires a CognitiveGraph instance. "
                "Inject it from the composition root."
            )
        self._cg = cg

    @property
    def cg(self) -> Any:
        return self._cg

    # ── Helpers ──

    def _search(self, filters: dict) -> list[dict]:
        result = self._cg.search(self.INDEX, filters)
        return result.get("hits", {}).get("hits", [])

    # ── Spring ──

    def get_spring(self, spring_id: str) -> Spring | None:
        spring = self._cg.get(self.INDEX, spring_id)
        if spring and spring.get("_source"):
            return Spring.from_dict(spring["_source"])
        return None

    def save_spring(self, spring: Spring) -> None:
        _require_ok(
            self._cg.store(self.INDEX, spring.spring_id, spring.to_dict()),
            f"save_spring({spring.spring_id})",
        )

    def list_springs(self, status: str = "") -> list[Spring]:
        filters = {"type": TYPE_SPRING}
        hits = self._search(filters)
        springs = [Spring.from_dict(h["_source"]) for h in hits]
        if status:
            springs = [s for s in springs if s.status == status]
        return springs

    def get_active_spring(self) -> Spring | None:
        """Get active spring with formal priority and recency rules.

        Rules (anti-sprint-hijacking):
        1. Formal sprints always win over informal ones
        2. Most recent wins among same type
        3. >2 formal active = anomaly warning
        """
        hits = self._search({"type": TYPE_SPRING, "status": "active"})
        springs = [
            Spring.from_dict(h["_source"])
            for h in hits
            if h["_source"].get("active") or h["_source"].get("status") == "active"
        ]
        if not springs:
            return None

        def _recency(s: Spring) -> str:
            return s.updated_at or s.created_at or ""

        formal = [s for s in springs if not s.informal]
        informal = [s for s in springs if s.informal]

        # ADR-WALKER-077: varios activos por proyecto son LEGÍTIMOS si cada uno
        # pertenece a una sesión distinta (delta-sprint). La anomalía real es la
        # falta de atribución: sin `session_id`, o con ids duplicados, no se
        # puede decidir a qué sesión pertenece cada sprint (sprint hijacking).
        unattributed = [s for s in formal if not (s.session_id or "").strip()]
        session_ids = [s.session_id for s in formal if (s.session_id or "").strip()]
        duplicated = len(session_ids) != len(set(session_ids))
        if unattributed or duplicated:
            logger.warning(
                "DeltaGraph: %d FORMAL active sprints sin atribución por sesión "
                "(%s). Anomaly — cada activo debe pertenecer a una sesión distinta.",
                len(formal),
                ", ".join(f"{s.spring_id}:{s.session_id or '(sin sesión)'}" for s in formal),
            )

        pool = formal or informal
        pool.sort(key=_recency, reverse=True)
        return pool[0]

    # ── Spec ──

    def get_specs(self, spring_id: str, status: str = "") -> list[Spec]:
        all_specs = self._search({"type": TYPE_SPEC})
        specs = [
            h["_source"]
            for h in all_specs
            if h["_source"].get("spring_id") == spring_id
            or h["_source"].get("sprint_id") == spring_id
        ]
        result = [Spec.from_dict(s) for s in specs]
        if status:
            result = [s for s in result if s.status == status]
        return sorted(result, key=lambda s: s.spec_index)

    def get_spec(self, spring_id: str, spec_id: str) -> Spec | None:
        doc_id = spec_doc_id(spring_id, spec_id)
        doc = self._cg.get(self.INDEX, doc_id)
        if doc and doc.get("_source"):
            return Spec.from_dict(doc["_source"])
        return None

    def save_spec(self, spec: Spec) -> None:
        _require_ok(
            self._cg.store(self.INDEX, spec.doc_id, spec.to_dict()),
            f"save_spec({spec.doc_id})",
        )

    def save_specs_batch(self, specs: list[Spec]) -> None:
        if not specs:
            return
        docs = {s.doc_id: s.to_dict() for s in specs}
        _require_ok(
            self._cg.store_many(self.INDEX, docs),
            f"save_specs_batch({len(docs)})",
        )

    def delete_spec(self, spring_id: str, spec_id: str) -> None:
        _require_ok(
            self._cg.delete(self.INDEX, spec_doc_id(spring_id, spec_id)),
            f"delete_spec({spec_doc_id(spring_id, spec_id)})",
        )

    # ── Session ──

    def get_session(self, session_id: str) -> Session | None:
        doc = self._cg.get(self.INDEX, session_id)
        if doc and doc.get("_source"):
            return Session.from_dict(doc["_source"])
        return None

    def save_session(self, session: Session) -> None:
        _require_ok(
            self._cg.store(self.INDEX, session.session_id, session.to_dict()),
            f"save_session({session.session_id})",
        )

    def list_sessions(self, status: str = "", project_id: str = "") -> list[Session]:
        hits = self._search({"type": TYPE_SESSION})
        sessions = [Session.from_dict(h["_source"]) for h in hits]
        if status:
            sessions = [s for s in sessions if s.status == status]
        if project_id:
            sessions = [s for s in sessions if s.project_id == project_id]
        return sessions

    # ── Relations ──

    def add_edge(self, from_id: str, to_id: str, edge_type: str) -> None:
        self._cg.add_edge(from_id, to_id, type=edge_type)
