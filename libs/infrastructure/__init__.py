"""
libs/infrastructure/ — Infrastructure utilities.

Arquitectura ORM-like:
  IndexAdapter   → interface que cada feature implementa
  IndexRegistry  → registro central de adaptadores
  SearchResult   → estructura de resultado comun

  CognitiveGraphSearcher/Writer → wrappers para el CognitiveGraph legacy

PRINCIPIO: Las features NO dependen de CognitiveGraph directamente.
           Dependen de IndexAdapter que ellas mismas implementan.
"""
from __future__ import annotations

from .adapters import IndexAdapter, IndexRegistry, SearchResult
from .cognitive import CognitiveGraphSearcher, CognitiveGraphWriter

__all__ = [
    "IndexAdapter",
    "IndexRegistry",
    "SearchResult",
    "CognitiveGraphSearcher",
    "CognitiveGraphWriter",
]
