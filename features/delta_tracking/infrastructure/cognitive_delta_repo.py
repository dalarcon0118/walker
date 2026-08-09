"""infrastructure/cognitive_delta_repo.py — Implementa DeltaRepository via CognitiveGraph.

Capa de infraestructura en Clean Architecture. Traduce entidades de dominio
(Spring, Spec) a operaciones sobre CognitiveGraph (store, search, edges).

Ahora recibe el grafo/factory desde la composición raíz, no desde engine.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from features.delta_tracking.application.ports.delta_repository import DeltaRepository
from features.delta_tracking.domain.entities import Spring, Spec
from features.delta_tracking.domain.values import (
    TYPE_SPRING,
    TYPE_SPEC,
    spec_doc_id,
)

logger = logging.getLogger(__name__)


class CognitiveDeltaRepository(DeltaRepository):
    """Implementacion sobre CognitiveGraph (indice 'delta').

    El orquestador CognitiveGraph se resuelve por DI desde la composicion
    raiz (graph_factory), no desde engine/bootstrap.
    """

    INDEX = "delta"

    def __init__(self, project_root: str, graph_factory: Callable[[str], Any] | None = None):
        if graph_factory is None:
            from features.engine.bootstrap import get_module_service
            graph_factory = get_module_service("cognitive_graph")
        self._cg = graph_factory(str(project_root))

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
        self._cg.store(self.INDEX, spring.spring_id, spring.to_dict())

    def list_springs(self, status: str = "") -> list[Spring]:
        filters = {"type": TYPE_SPRING}
        hits = self._search(filters)
        springs = [Spring.from_dict(h["_source"]) for h in hits]
        if status:
            springs = [s for s in springs if s.status == status]
        return springs

    def get_active_spring(self) -> Spring | None:
        """Obtiene el spring activo con prioridad FORMAL y el mas reciente.

        Reglas (anti-secuestro del sprint activo):
          1. Los springs INFORMALES (session-YYYYMMDD) NUNCA ganan la
             resolucion si existe un formal activo: son logs de sesion,
             no sprints de workflow. Si no hay formal activo, se usa el
             informal mas reciente (fallback).
          2. Entre los activos del mismo tipo se elige el ULTIMO (mas
             reciente por updated_at/created_at), no el primero del index.
          3. Si hay mas de 2 springs FORMALES activos, es anomalo: emite
             una alerta (log warning) para que el usuario cierre uno.
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

        # Prioridad: formales (flag informal=False) sobre informales.
        # Se usa el campo Spring.informal (ya persistido), NO el prefijo
        # del id: los sprints formales legacy pueden llamarse session-* pero
        # no tienen informal=True (ver _is_informal_sprint).
        formal = [s for s in springs if not s.informal]
        informal = [s for s in springs if s.informal]

        # Alerta: mas de 2 formales activos es anomalo. Sin try/except:
        # loggear nunca debe protegerse (convention-001 — cero except silenciosos).
        if len(formal) > 2:
            logger.warning(
                "DeltaGraph: %d springs FORMALES activos (%s). "
                "Es anomalo tener mas de 2 abiertos — cerrar los que no se usen.",
                len(formal),
                ", ".join(s.spring_id for s in formal),
            )

        pool = formal or informal
        pool.sort(key=_recency, reverse=True)
        return pool[0]

    # ── Spec ──

    def get_specs(self, spring_id: str, status: str = "") -> list[Spec]:
        # SSOT (unificacion Task/Spec): el indice delta solo contiene docs con
        # esquema Spec (index_service escribe Spec.to_dict, igual que save_spec).
        # La compensacion legacy por docs Task-schema (setdefault) fue eliminada:
        # los docs Task-schema previos al upgrade se migran al reindexar (mismos
        # doc_ids {spring}:{spec_id}), via index_delta/index_all.
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
