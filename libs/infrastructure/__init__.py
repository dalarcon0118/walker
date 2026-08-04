"""
libs/infrastructure/ — Infrastructure utilities.

  CognitiveGraphSearcher → wrappers read para el CognitiveGraph (busqueda)

PRINCIPIO: CQRS para reads (Searcher da envolvente compartida con error
handling para tasks MCP). Para writes: CognitiveGraph.upsert/store/store_many
o Entity.save() directamente. No existe capa Writer extra.
"""
from __future__ import annotations

from .cognitive import CognitiveGraphSearcher

__all__ = [
    "CognitiveGraphSearcher",
]
