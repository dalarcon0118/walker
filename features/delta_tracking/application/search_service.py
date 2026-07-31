"""application/search_service.py — Busqueda en el indice DeltaTracking via CognitiveGraph.

Usa CognitiveGraph.search() nativo con full-text + contexto del grafo.
"""

from __future__ import annotations


def search_tasks(cg, query: str, max_results: int = 20) -> list[dict]:
    """Busca tareas en el indice delta via CognitiveGraph.

    Args:
        cg: Instancia de CognitiveGraph
        query: Texto a buscar
        max_results: Maximo de resultados

    Returns:
        Lista de dicts con source, source_id, field, snippet, score
    """
    result = cg.search("delta", {"text": query})
    hits = result.get("hits", {}).get("hits", [])
    entries = []
    for h in hits[:max_results]:
        source = h.get("_source", {})
        node_id = h.get("_id", "")
        entries.append({
            "source": "task",
            "source_id": node_id,
            "field": "objective",
            "snippet": source.get("objective", "")[:200],
            "score": h.get("_score", 1.0),
            "_id": node_id,
            "_related": h.get("_related", []),
        })
    return entries


def search_by_source(cg, source: str, source_id: str) -> list[dict]:
    """Obtiene informacion de un nodo especifico en el grafo."""
    result = cg.get("delta", source_id)
    if not result:
        return []
    source_data = result.get("_source", {})
    # Obtener vecinos
    related = cg.get_related(source_id, depth=1)
    return [{
        "source": source,
        "source_id": source_id,
        "field": k,
        "snippet": str(v)[:200] if v else "",
        "score": 1.0,
        "related": related,
    } for k, v in source_data.items() if isinstance(v, (str, int, float))]
