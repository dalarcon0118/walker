"""
application/query_use_cases.py — Casos de uso para consultas multi-dominio.

Todos los casos de uso delegan en CognitiveGraph (vía QueryManager).
No importan directamente query/, cpg/, ckg/ ni deltagraph/ internamente.
"""

from __future__ import annotations


def _get_query_manager(chain_dir: str):
    from features.engine.context.query_manager import QueryManager

    return QueryManager(chain_dir)


def _get_cg(chain_dir: str):
    from libs.cognitive_graph import CognitiveGraph
    from libs import resolve_project_root

    proj_dir = resolve_project_root(chain_dir)
    return CognitiveGraph(proj_dir) if proj_dir else CognitiveGraph(chain_dir)


class QueryCpgUseCase:
    """Consulta CPG via CognitiveGraph."""

    def __init__(self, chain_dir: str):
        self._cg = _get_cg(chain_dir)
        self._chain_dir = chain_dir

    def execute(self, query_text: str) -> dict:
        try:
            from features.code_analysis.application.query_service import QueryGraphUseCase

            svc = QueryGraphUseCase(self._chain_dir)
            result = svc.grep(query_text)
            return {"ok": True, "layer": "cpg", "data": result}
        except Exception as e:
            return {"ok": False, "error": str(e)}


class QueryCkgUseCase:
    """Consulta CKG via CognitiveGraph."""

    def __init__(self, chain_dir: str):
        self._cg = _get_cg(chain_dir)
        self._chain_dir = chain_dir

    def execute(self, query_obj: dict) -> dict:
        try:
            filters = (
                query_obj.get("filters", {}) if isinstance(query_obj, dict) else {}
            )
            result = self._cg.search("ckg", filters)
            hits = result.get("hits", {})
            limit = query_obj.get("limit", 30) if isinstance(query_obj, dict) else 30
            return {
                "ok": True,
                "layer": "ckg",
                "total": hits.get("total", 0),
                "results": [
                    {
                        "id": h["_id"],
                        "type": h["_source"].get("type", "?"),
                        "label": h["_source"].get("statement", "")[:100],
                        "snippet": h["_source"].get("rationale", "")[:200],
                    }
                    for h in hits.get("hits", [])[:limit]
                ],
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}


class QueryDeltaGraphUseCase:
    """Consulta DeltaGraph via CognitiveGraph."""

    def __init__(self, chain_dir: str):
        self._cg = _get_cg(chain_dir)
        self._chain_dir = chain_dir

    def execute(self, query_text: str) -> dict:
        try:
            result = self._cg.search("delta", {"objective": query_text})
            hits = result.get("hits", {})
            return {
                "ok": True,
                "layer": "delta",
                "total": hits.get("total", 0),
                "data": {"results": {q: hits.get("hits", [])}},
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}


class QueryTraceUseCase:
    """Consulta TraceGraph via CognitiveGraph."""

    def __init__(self, chain_dir: str):
        self._cg = _get_cg(chain_dir)
        self._chain_dir = chain_dir

    def execute(self, query_text: str) -> dict:
        try:
            result = self._cg.search("trace", {"tool": query_text})
            hits = result.get("hits", {})
            return {
                "ok": True,
                "layer": "trace",
                "total": hits.get("total", 0),
                "data": {"results": {q: hits.get("hits", [])}},
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}


class QueryStateUseCase:
    """Consulta el estado del workflow/sprint via CognitiveGraph (QueryManager wrapper)."""

    def __init__(self, chain_dir: str):
        self._qm = _get_query_manager(chain_dir)
        self._chain_dir = chain_dir

    def execute(self, filters: dict) -> dict:
        try:
            return self._qm.query_layer("state", filters or {})
        except Exception as e:
            return {"ok": False, "error": str(e)}


class QueryCrossDomainUseCase:
    """Consulta federada multi-dominio via CognitiveGraph."""

    def __init__(self, chain_dir: str):
        self._cg = _get_cg(chain_dir)
        self._chain_dir = chain_dir

    def execute(self, query_text: str, domains: list[str] | None = None) -> dict:
        domains = domains or ["delta", "trace", "ckg"]
        queries = {}
        for d in domains:
            if d in ("cpg", "ckg", "delta", "trace"):
                queries[d] = (
                    {"statement": query_text}
                    if d == "ckg"
                    else {"objective": query_text}
                )
        result = self._cg.search_many(queries)
        return {
            "ok": True,
            "layer": "cross-domain",
            "query": query_text,
            "domains_consulted": domains,
            "results": {
                k: v.get("hits", {}) for k, v in result.get("responses", {}).items()
            },
        }
