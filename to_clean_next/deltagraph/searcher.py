"""DEPRECATED: ContextSearcher moved to deltagraph.application.search_service.

Usar DeltaGraphService.search() en su lugar.
"""
from deltagraph import DeltaGraphService


class ContextSearcher:
    """DEPRECATED: Usar DeltaGraphService."""

    def __init__(self, chain_dir: str):
        self._svc = DeltaGraphService(chain_dir)

    def search(self, query: str, max_results: int = 10) -> list:
        return self._svc.search(query, max_results)
