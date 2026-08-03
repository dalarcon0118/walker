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
        # Node/history: estado del workflow (motor).
        node = ""
        history = []
        sub_state_data = {}
        last_fields = {}
        try:
            from ..infrastructure.repositories import WorkflowStateRepository
            wf = WorkflowStateRepository().load(self._chain_dir)
            node = f"{wf.current_node}.{wf.current_sub_state}" if wf.current_node else ""
            history = wf.history[-5:] or []
            # Exponer sub_state_data del paso anterior: el resultado del
            # submit previo (Q&A del ANALYZE, decisiones, etc.) vive aquí.
            # Es lo que permite que el user gate muestre "QUÉ SUCEDIÓ".
            ssd = wf.sub_state_data or {}
            if isinstance(ssd, dict):
                sub_state_data = ssd
                # Del sub-estado actual o del último con fields
                for key in (wf.current_sub_state, "ANALYZE.ANALYZE", "ANALYZE"):
                    entry = ssd.get(key)
                    fields = None
                    if isinstance(entry, dict):
                        fields = entry.get("fields", {})
                    elif hasattr(entry, "fields"):
                        fields = getattr(entry, "fields", {})
                    if fields:
                        last_fields = fields
                        break
        except Exception:
            pass
        # Sprint status: delta (fuente unica del spring).
        sprint_status = ""
        sprint_goal = ""
        try:
            from features.delta_tracking.application.spring_graph_service import SpringGraphService
            spring_svc = SpringGraphService.from_chain_dir(self._chain_dir)
            if spring_svc:
                spring_id = spring_svc.get_spring_id(self._chain_dir)
                spring_svc.ensure_spring_imported(self._chain_dir, spring_id)
                spring = spring_svc.get_spring(spring_id)
                if spring:
                    sprint_status = spring.get("status", "")
                    sprint_goal = spring.get("goal", "")
        except Exception:
            pass
        return {
            "ok": True,
            "layer": "state",
            "results": [{"node": node, "sprint_status": sprint_status}],
            "node": node,
            "history": history,
            "sprint_status": sprint_status,
            "sprint_goal": sprint_goal,
            "sub_state_data": sub_state_data,
            "last_fields": last_fields,
        }

    @property
    def chain_dir(self):
        return self._chain_dir
