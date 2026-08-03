"""
libs/infrastructure/cognitive.py — CognitiveGraph adapters (CQRS).

SRP:
  CognitiveGraphSearcher — queries indexes (read)
  CognitiveGraphWriter   — persists data to indexes (write)

PRINCIPIO: Separated by CQRS. A feature that only reads (e.g. CKG query)
           depends only on Searcher, not Writer.
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


class CognitiveGraphWriter:
    """Write access to indexed data.

    SRP: Single responsibility = persist data to indexes.
    """

    def __init__(self, project_root: str):
        self._cg = CognitiveGraph(project_root) if project_root else CognitiveGraph(".")

    def store(self, index: str, key: str, data: dict) -> None:
        self._cg.store(index, key, data)
