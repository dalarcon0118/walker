"""
libs/infrastructure/cognitive.py — CognitiveGraph adapters (CQRS read).

SRP:
  CognitiveGraphSearcher — queries indexes (read-only)

PRINCIPIO: Separated by CQRS for READS. A feature that only reads
depends only on Searcher, not on CognitiveGraph directly.
Writes van por CognitiveGraph.upsert/store/store_many o Entity.save().
"""
from __future__ import annotations

from libs.cognitive_graph import CognitiveGraph


class CognitiveGraphSearcher:
    """Read-only access to indexed data (CPG, CKG, Delta, Trace).

    SRP: Single responsibility = search and schema queries.
    """

    def __init__(self, project_root: str):
        self._cg = CognitiveGraph(project_root) if project_root else CognitiveGraph(".")

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
