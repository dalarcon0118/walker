"""
libs/infrastructure/adapters_ckg.py — CKG adapter example.

Demuestra como una feature implementa IndexAdapter.
El CodeKnowledgeGraph existente en features/cognitive_knowledge/
implementa esta interfaz.
"""
from __future__ import annotations

import time
from typing import Any
from libs.infrastructure import IndexAdapter, SearchResult


class CkgAdapter(IndexAdapter):
    """Adapter for CKG (Cognitive Knowledge Graph).
    
    Wraps CodeKnowledgeGraph behind the IndexAdapter interface.
    """
    
    def __init__(self, chain_dir: str):
        from features.cognitive_knowledge.application.query import CkgQueryService
        self._svc = CkgQueryService(chain_dir)
    
    def search(self, query: dict) -> SearchResult:
        start = time.time()
        try:
            result = self._svc.query(query)
            hits = result.get("results", [])
            took = int((time.time() - start) * 1000)
            return SearchResult(total=len(hits), hits=hits, took_ms=took)
        except Exception as e:
            return SearchResult(error=str(e))
    
    def schema(self) -> dict:
        return {"fields": ["type", "label", "subject", "statement", "tags"]}
