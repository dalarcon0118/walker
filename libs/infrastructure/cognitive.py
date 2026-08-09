"""
libs/infrastructure/cognitive.py — CognitiveGraph adapters (CQRS read).

SRP:
  CognitiveGraphSearcher — queries indexes (read-only)

PRINCIPIO: Separated by CQRS for READS. A feature that only reads
depends only on Searcher, not on CognitiveGraph directly.
Writes van por CognitiveGraph.upsert/store/store_many o Entity.save().

DI: el CognitiveGraph se INYECTA por constructor (no se construye aquí), así
el Searcher no sabe cómo se arma el grafo. La composición vive en el compose
root (bootstrap) → registry key "cognitive_searcher".
"""
from __future__ import annotations


class CognitiveGraphSearcher:
    """Read-only access to indexed data (CPG, CKG, Delta, Trace).

    SRP: Single responsibility = search and schema queries.

    Args:
        graph: instancia de CognitiveGraph ya compuesta (inyectada por DI).
    """

    def __init__(self, graph):
        self._cg = graph

    def search(self, index: str, query: dict) -> dict:
        try:
            return self._cg.search(index, query)
        except KeyError:
            return {
                "ok": False,
                "error": f"Index '{index}' not found",
                "hits": {"total": 0, "hits": []},
            }

    def search_many(self, layers: dict[str, dict]) -> dict:
        return self._cg.search_many(layers)

    def schema(self, index: str) -> dict:
        try:
            return self._cg.schema(index)
        except KeyError:
            return {"ok": False, "error": f"Index '{index}' not found"}

    def list_indexes(self) -> list[str]:
        return self._cg.list_indexes()
