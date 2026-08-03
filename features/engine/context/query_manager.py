"""query_manager.py — Query layer delegando en CognitiveGraphSearcher.

Los providers reciben QueryManager para consultar datos indexados.
Usa CognitiveGraphSearcher (libs/infrastructure/) en vez de CognitiveGraph directo.
"""

from libs.infrastructure import CognitiveGraphSearcher
from features.project import ProjectService

SUPPORTED_LAYERS = {"cpg", "ckg", "delta", "trace", "state"}


class QueryManager:
    """Capa unificada de consulta. Delega en CognitiveGraphSearcher."""

    def __init__(self, chain_dir: str):
        paths = ProjectService.resolve_paths(chain_dir)
        self._searcher = CognitiveGraphSearcher(paths.project_root) if paths.project_root else None
        self._chain_dir = chain_dir

    def query(self, layers):
        if not self._searcher:
            return {}
        return self._searcher.search_many(layers)

    def query_layer(self, layer, filters):
        if not self._searcher:
            return {"ok": False, "error": "project_root not resolved"}
        if layer not in SUPPORTED_LAYERS:
            return {"ok": False, "error": f"Capa no soportada: {layer}"}
        if layer == "state":
            return self._query_state(filters)
        try:
            result = self._searcher.search(layer, filters)
        except KeyError as e:
            return {"ok": False, "error": str(e), "layer": layer}
        return self._normalize(layer, result)

    @staticmethod
    def _normalize(layer: str, result: dict) -> dict:
        """Traduce el formato ES-like del searcher al contrato de los providers.

        El searcher (CognitiveGraph.search) devuelve:
          {"took", "index", "hits": {"total", "hits": [{"_id", "_source"}]}, "schema"}

        Los providers de contexto (ckg/cpg/delta) esperan:
          {"ok": True, "layer", "total", "results": [<source>...]}

        Si el searcher ya devuelve {"ok": False} (error), se propaga tal cual.
        """
        if result is None:
            return {"ok": False, "error": "empty result", "layer": layer}
        if result.get("ok") is False:
            result["layer"] = layer
            return result

        hits = result.get("hits", {})
        total = hits.get("total", 0) if isinstance(hits, dict) else 0
        raw_hits = hits.get("hits", []) if isinstance(hits, dict) else []
        results = []
        for h in raw_hits:
            if isinstance(h, dict):
                src = h.get("_source") or {}
                if isinstance(src, dict):
                    results.append(src)
        return {
            "ok": True,
            "layer": layer,
            "total": total,
            "results": results,
            "raw": result,
        }

    def _query_state(self, filters):
        from features.spring.infrastructure.repositories import SprintStateRepository

        state = SprintStateRepository().load_dict(self._chain_dir)
        if not state:
            return {"ok": False, "error": "sprint-state.json not found"}
        return {
            "ok": True,
            "layer": "state",
            "results": [state],
            "node": f"{state.get('current_node', '')}.{state.get('current_sub_state', '')}",
            "history": state.get("history", [])[-5:],
            "sprint_status": state.get("sprint", {}).get("status", ""),
        }

    @property
    def chain_dir(self):
        return self._chain_dir
