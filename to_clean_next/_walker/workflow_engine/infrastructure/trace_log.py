"""infrastructure/trace_log.py — TraceLogger escribe via CognitiveGraph.

Cada evento se persiste en el índice "trace" de CognitiveGraph.
El storage es data/trace.json.gz (mismo que CPG y CKG).
"""

from __future__ import annotations
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class TraceLogger:
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
            from _walker.shared.cognitive_graph import CognitiveGraph
            from _walker.shared import resolve_project_root

            proj_dir = resolve_project_root(chain_dir)
            if proj_dir:
                cg = CognitiveGraph(proj_dir)
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
            logger.warning("No se pudo escribir traza en CognitiveGraph: %s", e)

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

    def replay(self, chain_dir: str) -> list[dict]:
        """Replay de eventos desde CognitiveGraph."""
        try:
            from _walker.shared.cognitive_graph import CognitiveGraph
            from _walker.shared import resolve_project_root

            proj_dir = resolve_project_root(chain_dir)
            if proj_dir:
                cg = CognitiveGraph(proj_dir)
                result = cg.search("trace", {})
                return [h["_source"] for h in result["hits"]["hits"]]
        except Exception:
            pass
        return []

    def flush(self) -> None:
        self._buffer.clear()

    def _get_trace_path(self, chain_dir: str) -> str:
        if self._trace_dir:
            return os.path.join(self._trace_dir, f"{os.path.basename(chain_dir)}.log")
        return os.path.join(chain_dir, "trace.log")
