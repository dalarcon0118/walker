"""app/memories/query.py — MemoryQueryService (fachada de las memorias del walker).

NEGOCIO DEL WALKER (no una librería genérica, ADR-WALKER-067 §7.2): consulta
las 4 memorias (CPG, CKG, Delta, Trace). Capa de aplicación del host
(ADR-068): el storage genérico es `shared/libs/cognitive_graph`; la semántica
se auto-registra desde las features (forFeature) vía `apply_memory_indexes`.
"""
from __future__ import annotations

from typing import Any, Callable

from shared.libs.cognitive_graph import CognitiveGraph

from app.memories.registration import apply_memory_indexes, build_cognitive_graph


class MemoryQueryService:
    """Fachada unificada de consulta a las memorias del walker.

    Uso:
        svc = MemoryQueryService(project_root)
        svc.search("ckg", {"text": "auth"})
        svc.schema("cpg")
        svc.status()  # stats de las 4 memorias
        svc.multi_search({"ckg": {"text": "auth"}, "cpg": {"type": "class"}})
    """

    def __init__(
        self,
        project_root: str = "",
        graph: CognitiveGraph | None = None,
        register_indexes: Callable[[Any], None] | None = None,
    ):
        # Construcción canónica (G7): los índices se auto-registran desde las
        # features (apply_memory_indexes). Sin doble registro (B1/B2).
        self._cg = graph or build_cognitive_graph(str(project_root))
        if register_indexes is not None:
            # Override explícito (tests/alternativas) — aplica sobre el grafo.
            register_indexes(self._cg)

    def search(self, index: str, query: dict) -> dict:
        """Busqueda fulltext + estructurada en un indice."""
        return self._cg.search(index, query)

    def multi_search(self, layers: dict[str, dict]) -> dict:
        """Query federada multi-indice (search_many)."""
        return self._cg.search_many(layers)

    def schema(self, index: str) -> dict:
        """Schema de un indice (campos, tipos, total_docs, total_edges)."""
        return self._cg.schema(index)

    def list_indexes(self) -> list[str]:
        return self._cg.list_indexes()

    def status(self) -> dict:
        """Estado de las 4 memorias: docs, edges, tipos por indice."""
        result = {}
        for idx in self._cg.list_indexes():
            if idx.startswith("cpg-ref-"):
                continue  # indices de referencia, no memorias principales
            s = self._cg.schema(idx)
            result[idx] = {
                "total_docs": s.get("total_docs", 0),
                "total_edges": s.get("total_edges", 0),
                "types": s.get("types", {}),
                "description": s.get("description", ""),
            }
        return result