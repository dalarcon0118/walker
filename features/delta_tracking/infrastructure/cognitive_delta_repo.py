"""infrastructure/cognitive_delta_repo.py — Implementa DeltaRepository via CognitiveGraph.

Capa de infraestructura en Clean Architecture. Traduce entidades de dominio
(Spring, Spec) a operaciones sobre CognitiveGraph (store, search, edges).
"""
from __future__ import annotations

from libs.cognitive_graph import CognitiveGraph

from features.delta_tracking.application.ports.delta_repository import DeltaRepository
from features.delta_tracking.domain.entities import Spring, Spec
from features.delta_tracking.domain.values import (
    TYPE_SPRING, TYPE_SPEC,
    EDGE_CONTAINS, EDGE_IMPACTS, EDGE_DEPENDS,
    spec_doc_id, spec_id_from_index,
)


class CognitiveDeltaRepository(DeltaRepository):
    """Implementacion sobre CognitiveGraph (indice 'delta')."""

    INDEX = "delta"

    def __init__(self, project_root: str):
        self._cg = CognitiveGraph(str(project_root), auto_register=True)

    @property
    def cg(self) -> CognitiveGraph:
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
        self._cg.store(self.INDEX, spring.spring_id, spring.to_dict())

    def list_springs(self, status: str = "") -> list[Spring]:
        filters = {"type": TYPE_SPRING}
        hits = self._search(filters)
        springs = [Spring.from_dict(h["_source"]) for h in hits]
        if status:
            springs = [s for s in springs if s.status == status]
        return springs

    def get_active_spring(self) -> Spring | None:
        hits = self._search({"type": TYPE_SPRING, "status": "active"})
        for h in hits:
            src = h["_source"]
            if src.get("active"):
                return Spring.from_dict(src)
        return None

    # ── Spec ──

    def get_specs(self, spring_id: str, status: str = "") -> list[Spec]:
        all_specs = self._search({"type": TYPE_SPEC})
        # Incluir legacy docs
        legacy = self._search({"type": "delta"})
        for l in legacy:
            src = l["_source"]
            if src.get("sprint_id") == spring_id:
                src.setdefault("spec_id", src.get("task_id", ""))
                src.setdefault("spring_id", spring_id)
                src.setdefault("type", TYPE_SPEC)
                all_specs.append(l)

        specs = [
            h["_source"] for h in all_specs
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
        self._cg.store(self.INDEX, spec.doc_id, spec.to_dict())

    def save_specs_batch(self, specs: list[Spec]) -> None:
        if not specs:
            return
        docs = {s.doc_id: s.to_dict() for s in specs}
        self._cg.store_many(self.INDEX, docs)

    def delete_spec(self, spring_id: str, spec_id: str) -> None:
        self._cg.delete(self.INDEX, spec_doc_id(spring_id, spec_id))

    # ── Relaciones ──

    def add_edge(self, from_id: str, to_id: str, edge_type: str) -> None:
        self._cg.add_edge(from_id, to_id, type=edge_type)
