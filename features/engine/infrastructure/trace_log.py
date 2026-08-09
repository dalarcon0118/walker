"""infrastructure/trace_log.py — TraceLogger escribe via CognitiveGraph.

Implementa el puerto ITracer del dominio.
Cada evento se persiste en el índice "trace" de CognitiveGraph.
El storage es data/trace.json.gz (mismo que CPG y CKG).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from features.engine.domain.ports import ITracer

logger = logging.getLogger(__name__)


class TraceLogger(ITracer):
    """Logger de auditoria. Escribe eventos en CognitiveGraph (índice trace)."""

    def __init__(self, trace_dir: str | None = None):
        self._trace_dir = trace_dir
        self._buffer: list = []

    def trace(
        self,
        chain_dir: str,
        event_type: str,
        data: dict | None = None,
        task_id: str | None = None,
        session_id: str | None = None,
    ) -> None:
        """Escribe un evento en el TraceGraph via CognitiveGraph."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            "data": data or {},
        }
        if task_id:
            entry["task_id"] = task_id
        if session_id:
            entry["session_id"] = session_id

        # Persistir via CognitiveGraph
        try:
            from features.engine.bootstrap import get_module_service

            proj_dir = get_module_service("path_resolver").resolve_project_root(chain_dir)
            if not proj_dir:
                # PRINCIPIO (fix estructural): una traza que se pierde por no
                # poder resolver el proyecto NO debe silenciarse. El workflow
                # debe saber que el tracking fallo para no dar por sentado
                # un flujo limpio.
                logger.error(
                    "TRACE PERDIDA: no se pudo resolver project_root desde "
                    "chain_dir=%r para evento %s — el TraceGraph queda incompleto",
                    chain_dir,
                    event_type,
                )
                return
            cg = get_module_service("cognitive_graph")(proj_dir)
            event_id = f"ev-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
            cg.store(
                "trace",
                event_id,
                {
                    "tool": data.get("tool", "") if data else "",
                    "task_id": task_id or "",
                    "session_id": session_id or "",
                    "event_type": event_type,
                    "timestamp": entry["timestamp"],
                    "data": data or {},
                },
            )
        except Exception as e:
            # PRINCIPIO: no silenciar la perdida de trazas. Registrar con
            # logger.error (no warning) para que sea visible en el flujo.
            logger.error(
                "TRACE PERDIDA: fallo al persistir evento %s en CognitiveGraph: %s",
                event_type,
                e,
            )

    def trace_auto(
        self,
        chain_dir: str,
        sub_state: str,
        stdout: str,
        stderr: str,
        exit_code: int,
        task_id: str | None = None,
    ) -> None:
        self.trace(
            chain_dir,
            "AUTO_EXECUTE",
            {
                "sub_state": sub_state,
                "stdout": stdout[:2000],
                "stderr": stderr[:1000],
                "exit_code": exit_code,
            },
            task_id=task_id,
        )

    def trace_submit(
        self,
        chain_dir: str,
        sub_state: str,
        decision: str,
        evidence: str,
        task_id: str | None = None,
    ) -> None:
        self.trace(
            chain_dir,
            "SUBMIT",
            {
                "sub_state": sub_state,
                "decision": decision,
                "evidence": evidence[:2000],
            },
            task_id=task_id,
        )

    def trace_transition(
        self,
        chain_dir: str,
        gate: str,
        from_node: str,
        to_node: str,
        task_id: str | None = None,
    ) -> None:
        self.trace(
            chain_dir,
            "TRANSITION",
            {
                "gate": gate,
                "from": from_node,
                "to": to_node,
            },
            task_id=task_id,
        )

    def trace_error(
        self, chain_dir: str, error_type: str, message: str, task_id: str | None = None
    ) -> None:
        self.trace(
            chain_dir,
            "ERROR",
            {
                "error_type": error_type,
                "message": message[:2000],
            },
            task_id=task_id,
        )

    def replay(self, chain_dir: str, max_events: int | None = None) -> list[dict]:
        """Replay de eventos desde CognitiveGraph.

        Devuelve TODOS los eventos del índice trace (size alto para no
        truncar con traces grandes — el default del search es 50 y el gate
        de required_tools necesita ver el trace completo).
        """
        try:
            from features.engine.bootstrap import get_module_service

            proj_dir = get_module_service("path_resolver").resolve_project_root(chain_dir)
            if proj_dir:
                cg = get_module_service("cognitive_graph")(proj_dir)
                query = {"_index": "trace"}
                if max_events:
                    query["max_results"] = max_events
                else:
                    query["max_results"] = 100000
                result = cg.search("trace", query)
                return [h["_source"] for h in result["hits"]["hits"]]
        except Exception:
            pass
        return []

    def has_kg_query(
        self,
        chain_dir: str,
        task_id: str = "",
        session_id: str = "",
        layers: set[str] | list[str] | None = None,
    ) -> bool:
        """¿Existe un kg_query en el trace para las capas requeridas?

        Gate de auto-consulta (convención CKG doc-28): el submit de un
        sub-estado con required_tools se BLOQUEA si no hay evidencia de
        consulta a las capas obligatorias.

        Política de conteo (alineada al fix de trazado del QueryManager):
          - Consultas del composer: trazan con task_id/session_id reales
            del contexto → matchean por task_id o session_id.
          - Consultas MCP/CLI del agente: trazan con session_id="cli" o ""
            (indistinguibles por tarea). Cuentan como evidencia porque son
            las consultas ACTIVAS del agente a las capas requeridas — el
            gate exige "hubo consulta real a estas capas", no "fue de la
            sesión exacta".

        Args:
            chain_dir: directorio del sprint.
            task_id: tarea en curso ("" si no se distingue).
            session_id: sesión en curso ("" si no se distingue).
            layers: capas obligatorias (p.ej. {"ckg", "cpg"}). Si None,
                    cualquier kg_query cuenta.

        Returns:
            True si hay evidencia de consulta, False si no.
        """
        try:
            events = self.replay(chain_dir)
        except Exception:
            return False
        layers = {l for l in (layers or set()) if l}
        for ev in events:
            if not isinstance(ev, dict):
                continue
            if ev.get("event_type") != "kg_query":
                continue
            data = ev.get("data") or {}
            idx = data.get("index")
            # "multi" (kg_multi) consulta varias capas a la vez → cubre
            # cualquier layer requerida. Evita falso bloqueo cuando el
            # agente usa la herramienta multi-capa recomendada.
            if layers and idx not in layers and idx != "multi":
                continue
            ev_task = str(ev.get("task_id", "") or "")
            ev_sess = str(ev.get("session_id", "") or "")
            if task_id and ev_task == task_id:
                return True
            if session_id and session_id not in ("", "cli") and ev_sess == session_id:
                return True
            # Consultas manuales/MCP (session "cli" o vacía): evidencia de
            # consulta activa del agente a las capas requeridas.
            if ev_sess in ("", "cli"):
                return True
        return False

    def flush(self) -> None:
        self._buffer.clear()

    def _get_trace_path(self, chain_dir: str) -> str:
        if self._trace_dir:
            return os.path.join(self._trace_dir, f"{os.path.basename(chain_dir)}.log")
        return os.path.join(chain_dir, "trace.log")
