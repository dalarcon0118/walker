"""application/ports/ckg_repository.py — CKG repository port.

Output port in Clean Architecture. Defines the persistence and query
contract of the Knowledge Graph. Infrastructure implements it via
CognitiveGraph (index 'ckg').
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class CkgRepository(ABC):
    """Contract for CKG persistence and query."""

    @abstractmethod
    def save_nodes(self, nodes: list[dict]) -> dict:
        """Persist nodes in batch via store_many. Returns {ok, node_ids, degraded}."""
        ...

    @abstractmethod
    def query(self, filters: dict | None = None) -> list[dict]:
        """Query active CKG nodes (excludes superseded)."""
        ...

    @abstractmethod
    def search(self, query: str, max_results: int = 20) -> list[dict]:
        """Fulltext search in the CKG."""
        ...

    @abstractmethod
    def delete_by_metadata(self, key: str, value: str) -> dict:
        """Delete nodes by metadata in batch."""
        ...

    @abstractmethod
    def next_id(self, prefix: str = "node") -> str:
        """Generate sequential ID without collision (uses max_key)."""
        ...
