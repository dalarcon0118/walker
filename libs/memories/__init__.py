"""libs/memories/__init__.py — MemoryQueryService: fachada unificada de memorias.

Unico punto de entrada para consultas a las 4 memorias (CPG, CKG, Delta, Trace).
Las tools MCP y el workflow engine consultan aqui en vez de CognitiveGraph directo.

Cada memoria se consulta via su port (CpgRepository, CkgRepository, DeltaRepository),
pero el fulltext search (kg_search) se delega en CognitiveGraph.search() porque
no tiene logica de negocio y ya tiene indices secundarios O(1) + bigramas.
"""
from __future__ import annotations

from libs.memories.query_service import MemoryQueryService

__all__ = ["MemoryQueryService"]
