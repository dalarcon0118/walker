"""app/memories.py — MemoryQueryService (fachada de las memorias del walker).

NEGOCIO DEL WALKER (no una librería genérica, ADR-WALKER-067 §7.2): consulta
las 4 memorias (CPG, CKG, Delta, Trace) con sus schemas. Vive en el host
(app), no en shared/libs. El storage genérico es `shared/libs/cognitive_graph`;
la semántica de las memorias se inyecta desde `app/memory_schemas.py`.
"""
from __future__ import annotations

from typing import Any, Callable

from shared.libs.cognitive_graph import CognitiveGraph

from app.memory_schemas import register_default_memory_indexes


class MemoryQueryService:
    """Fachada unificada de consulta a las memorias del walker.

    Uso:
        svc = MemoryQueryService(project_root, register_indexes=register_cognitive_indexes)
        svc.search("ckg", {"text": "auth"})
        svc.schema("cpg")
        svc.status()  # stats de las 4 memorias
        svc.multi_search({"ckg": {"text": "auth"}, "cpg": {"type": "class"}})
    """

    def __init__(
        self,
        project_root: str = "",
        register_indexes: Callable[[Any], None] | None = None,
    ):
        self._cg = CognitiveGraph(
            str(project_root), register_defaults=register_default_memory_indexes
        )
        # ADR-WALKER-054 D2/D3: el indice cpg lo registra el composition root
        # (register_cognitive_indexes) — inyectado aquí. shared NO importa app.
        if register_indexes is not None:
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