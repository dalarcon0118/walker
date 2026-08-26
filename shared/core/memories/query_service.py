"""shared/memories/query_service.py — MemoryQueryService.

SRP: Fachada unificada para consultar las 4 memorias del walker.
    - search: fulltext + estructurado en cualquier indice
    - search_many: query federada multi-indice
    - schema: schema de cada indice
    - status/presenter: stats de memorias

DEPENDENCIAS: CognitiveGraph (infra compartida) para search.
Los ports (CkgRepo, CpgRepo, DeltaRepo) para operaciones de dominio.
"""
from __future__ import annotations

from shared.core.cognitive_graph import CognitiveGraph


class MemoryQueryService:
    """Fachada unificada de consulta a las memorias del walker.

    Uso:
        svc = MemoryQueryService(project_root)
        svc.search("ckg", {"text": "auth"})
        svc.schema("cpg")
        svc.status()  # stats de las 4 memorias
        svc.multi_search({"ckg": {"text": "auth"}, "cpg": {"type": "class"}})
    """

    def __init__(self, project_root: str = ""):
        self._cg = CognitiveGraph(str(project_root), auto_register=True)
        # ADR-WALKER-054 D2/D3: shared/core NO registra cpg; los schemas de
        # dominio (incl. cpg) se registran via el composition root.
        from libs.cognitive_graph.cognitive_registry import register_cognitive_indexes

        register_cognitive_indexes(self._cg)

    # ── Búsqueda (sin lógica de negocio → directo a infra) ──

    def search(self, index: str, query: dict) -> dict:
        """Busqueda fulltext + estructurada en un indice.

        Delega en CognitiveGraph.search() que ya tiene:
        - TokenIndex con bigramas O(1) + field-scoped (E1)
        - Indices secundarios O(1) (_type_index, _field_index)
        - Paginacion _from/size (G8)
        - Match exacto por defecto (G7)
        """
        return self._cg.search(index, query)

    def multi_search(self, layers: dict[str, dict]) -> dict:
        """Query federada multi-indice (search_many)."""
        return self._cg.search_many(layers)

    # ── Schema ──

    def schema(self, index: str) -> dict:
        """Schema de un indice (campos, tipos, total_docs, total_edges)."""
        return self._cg.schema(index)

    def list_indexes(self) -> list[str]:
        return self._cg.list_indexes()

    # ── Status (para el presenter y status MCP) ──

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
