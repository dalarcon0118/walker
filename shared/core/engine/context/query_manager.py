"""query_manager.py — Query layer delegando en el servicio DI de búsqueda.

Los providers reciben QueryManager para consultar datos indexados.
El searcher se resuelve por clave en el ModuleRegistry ("cognitive_searcher")
en vez de importar la implementación concreta de shared/ (inversión de control).
"""

from typing import Any

SUPPORTED_LAYERS = {"cpg", "ckg", "delta", "trace", "state"}


class QueryManager:
    """Capa unificada de consulta. Delega en el searcher resuelto por DI.

    Cada query_layer() registra un evento kg_query en el TraceGraph
    (best-effort). Esto hace VISIBLES las consultas de contexto que el
    composer ejecuta para inyectar capas (cpg/ckg/delta/trace) — antes
    eran invisibles en el trace porque solo las tareas CLI recall_knowledge/
    search_memories registraban eventos.
    """

    def __init__(
        self, chain_dir: str, task_id: str | None = None, session_id: str | None = None,
        project_service: Any = None, searcher_factory: Any = None,
    ):
        # ADR-WALKER-059/061: sin service locator. Si nadie inyecta
        # project_service, no hay resolución de paths ni searcher (degrada a
        # query vacía) — el caller que necesita búsqueda DEBE inyectar ambos.
        paths = project_service.resolve_paths(chain_dir) if project_service else None
        self._searcher = None
        if paths and paths.project_root:
            if searcher_factory is None:
                raise ValueError("QueryManager: searcher_factory no inyectado (composition root required)")
            self._searcher = searcher_factory(paths.project_root)
        self._chain_dir = chain_dir
        self._task_id = task_id or ""
        self._session_id = session_id or ""

    def query(self, layers):
        if not self._searcher:
            return {}
        return self._searcher.search_many(layers)

    def query_layer(self, layer, filters):
        # Trazar la consulta ANTES de ejecutarla (visibilidad en TraceGraph).
        self._trace_query(layer, filters)
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

    def _trace_query(self, layer: str, filters: dict) -> None:
        """Registra un evento kg_query en el TraceGraph (best-effort).

        La traza asocia la consulta al task_id/session_id del contexto en
        curso (no a "cli" como las queries manuales), permitiendo distinguir
        qué consultas ejecutó el agente/workflow para obtener contexto.
        """
        try:
            from shared.core.engine.infrastructure.trace_log import TraceLogger

            safe = {}
            for k, v in (filters or {}).items():
                if isinstance(v, str) and len(v) > 200:
                    safe[k] = v[:200] + "..."
                else:
                    safe[k] = v
            TraceLogger().trace(
                self._chain_dir,
                "kg_query",
                data={
                    "index": layer,
                    "query": safe,
                    "project_id": "",
                },
                task_id=self._task_id,
                session_id=self._session_id or "composer",
            )
        except Exception:
            pass

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
            node = (
                f"{wf.current_node}.{wf.current_sub_state}" if wf.current_node else ""
            )
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

            spring_svc = self._sprint_service.from_chain_dir(self._chain_dir) if self._sprint_service else None
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
