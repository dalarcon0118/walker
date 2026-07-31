"""query_manager.py — CognitiveGraph directo. Sin wrappers.

Los providers y el ContextComposer reciben CognitiveGraph directamente.
"""

from _walker.shared.cognitive_graph import CognitiveGraph

SUPPORTED_LAYERS = {"cpg", "ckg", "delta", "trace", "state"}


class QueryManager:
    """Capa unificada de consulta. Delega en CognitiveGraph."""

    def __init__(self, chain_dir: str):
        from _walker.shared import resolve_project_root

        proj_dir = resolve_project_root(chain_dir)
        self._cg = CognitiveGraph(proj_dir) if proj_dir else CognitiveGraph(chain_dir)
        self._chain_dir = chain_dir

    def query(self, layers):
        return self._cg.search_many(layers)

    def query_layer(self, layer, filters):
        if layer not in SUPPORTED_LAYERS:
            return {"ok": False, "error": f"Capa no soportada: {layer}"}
        if layer == "state":
            return self._query_state(filters)
        try:
            return self._cg.search(layer, filters)
        except KeyError as e:
            return {"ok": False, "error": str(e), "layer": layer}

    def _query_state(self, filters):
        import json
        import os

        spath = os.path.join(self._chain_dir, "sprint-state.json")
        if not os.path.exists(spath):
            return {"ok": False, "error": "sprint-state.json not found"}
        with open(spath) as f:
            state = json.load(f)
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
