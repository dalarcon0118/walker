"""application/search_service.py — Búsqueda en el índice DeltaGraph.

Usa TokenIndex para buscar tareas por texto. Multi-sprint.
"""

from __future__ import annotations
from deltagraph.domain.models import TokenIndex


def search_tasks(index: TokenIndex, query: str, max_results: int = 20) -> list[dict]:
    """Busca tareas en el índice.

    Args:
        index: TokenIndex cargado
        query: texto a buscar
        max_results: máximo de resultados

    Returns:
        Lista de dicts con source, source_id, field, snippet, score
    """
    results = index.search(query, max_results)
    return [r.to_dict() for r in results]


def search_by_source(index: TokenIndex, source: str, source_id: str) -> list[dict]:
    """Obtiene todas las entradas de un objeto específico."""
    return [r.to_dict() for r in index.search_by_source(source, source_id)]
