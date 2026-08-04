"""
libs/infrastructure/ — Infrastructure utilities.

  CognitiveGraphSearcher → wrappers read para el CognitiveGraph (busqueda)
  CognitiveGraphWriter   → wrappers write (store/upsert/delete por key)

PRINCIPIO: las features acceden a los indices via el CognitiveGraph
(ODM/Entity) o via estos wrappers — no existe otra capa de adaptadores.
"""
from __future__ import annotations

from .cognitive import CognitiveGraphSearcher, CognitiveGraphWriter

__all__ = [
    "CognitiveGraphSearcher",
    "CognitiveGraphWriter",
]
