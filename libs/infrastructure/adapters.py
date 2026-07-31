"""
libs/infrastructure/adapters.py — Index adapter interfaces (ORM-like).

Each feature implements its own adapter for its data index.
CognitiveGraph is one implementation; features can provide custom ones.

Uso:
    from libs.infrastructure import IndexAdapter, SearchResult
    from features.cognitive_knowledge import CkgAdapter

    registry.register("ckg", CkgAdapter(chain_dir))
    result = registry.search("ckg", {"type": "decision"})
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SearchResult:
    """Structured search result from any index."""
    total: int = 0
    hits: list[dict] = field(default_factory=list)
    took_ms: int = 0
    error: str = ""


class IndexAdapter(ABC):
    """Interface for a single data index adapter.
    
    Each feature (CKG, CPG, Delta, Trace) implements this interface
    to provide search/store access to its indexed data.
    """
    
    @abstractmethod
    def search(self, query: dict) -> SearchResult:
        """Search this index with the given filters."""
        ...
    
    def store(self, doc_id: str, data: dict) -> dict:
        """Store a document in this index. Optional (read-only adapters can skip)."""
        raise NotImplementedError(f"{type(self).__name__} does not support store")
    
    def schema(self) -> dict:
        """Return schema information for this index."""
        return {}


class IndexRegistry:
    """Registry of named index adapters (like a connection pool).
    
    SSOT: All available indexes and their adapters.
    """
    
    def __init__(self):
        self._adapters: dict[str, IndexAdapter] = {}
    
    def register(self, name: str, adapter: IndexAdapter) -> None:
        self._adapters[name] = adapter
    
    def search(self, index: str, query: dict) -> SearchResult:
        adapter = self._adapters.get(index)
        if not adapter:
            return SearchResult(error=f"Index '{index}' not registered")
        return adapter.search(query)
    
    def search_many(self, queries: dict[str, dict]) -> dict[str, SearchResult]:
        return {idx: self.search(idx, q) for idx, q in queries.items()}
    
    def list_indexes(self) -> list[str]:
        return list(self._adapters.keys())
