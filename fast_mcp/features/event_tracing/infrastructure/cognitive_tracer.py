"""infrastructure/cognitive_tracer.py — ITracer implementation backed by TraceRepository.

Replaces NoopTracer with real tracing to the CognitiveGraph trace index.
Implements ITracer from workflow/domain/ports.py using DI-injected TraceService.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fast_mcp.features.event_tracing.application.services.trace_service import (
    TraceService,
)
from fast_mcp.shared_kernel.ports import ITracer

logger = logging.getLogger(__name__)


class CognitiveTracer(ITracer):
    """Real tracer backed by TraceService (CognitiveGraph).

    Replaces NoopTracer. All methods persist events to the trace index.
    """

    def __init__(self, trace_service: TraceService) -> None:
        self._service = trace_service

    def trace(
        self,
        chain_dir: str,
        event_type: str,
        data: dict | None = None,
        task_id: str | None = None,
        session_id: str | None = None,
    ) -> None:
        self._service.push_event(chain_dir, {
            "event_type": event_type,
            "source": "workflow",
            "task_id": task_id or "",
            "data": data or {},
        })

    def has_skill_load(
        self,
        chain_dir: str,
        task_id: str = "",
        session_id: str = "",
        skills: set[str] | list[str] | None = None,
        since: str = "",
        node: str = "",
        sub_state: str = "",
    ) -> bool:
        """True si hay un evento `skill_loaded` de uno de `skills`.

        Token (task, node, sub_state): una carga hecha en ESTE sub-estado vale
        aunque después haya submits. `state.updated_at` cambia con cada submit;
        usarlo como piso obligaba a recargar el skill en cada paso — el paso
        artificial que reporta el feedback (P1.1).

        `since` queda como fallback para eventos legacy (sin node/sub_state):
        sin token no se puede probar la entrada, así que se conserva la regla
        anterior en vez de aceptar cualquier carga vieja. Los eventos con
        posición de OTRA entrada se ignoran: un token de otra tarea/fase no
        satisface el gate.
        """
        wanted = {str(s) for s in (skills or [])}
        try:
            events = self._service.load_traces(chain_dir)
        except Exception:  # noqa: BLE001 — sin trace no se puede verificar
            return False

        def _matches(ev: Any) -> bool:
            if getattr(ev, "event_type", "") != "skill_loaded":
                return False
            if task_id and getattr(ev, "task_id", "") and ev.task_id != task_id:
                return False
            data = getattr(ev, "data", None) or {}
            name = str(data.get("skill", "")) if isinstance(data, dict) else ""
            return not wanted or name in wanted

        token = bool(node and sub_state)
        legacy: list = []
        for ev in events:
            if not _matches(ev):
                continue
            data = getattr(ev, "data", None) or {}
            ev_node = str(data.get("node", "") or "") if isinstance(data, dict) else ""
            ev_sub = str(data.get("sub_state", "") or "") if isinstance(data, dict) else ""
            if not ev_node and not ev_sub:
                legacy.append(ev)
                continue
            if token and ev_node == node and ev_sub == sub_state:
                return True
            if not token and (
                not since or str(getattr(ev, "timestamp", "")) >= since
            ):
                return True
        # Compat: cargas sin posición (cliente viejo) dentro de la entrada.
        for ev in legacy:
            if since and str(getattr(ev, "timestamp", "")) < since:
                continue
            return True
        return False

    def trace_event(self, event: Any) -> None:
        """Register a typed event (WorkflowEvent)."""
        if hasattr(event, "to_dict"):
            d = event.to_dict()
        elif isinstance(event, dict):
            d = event
        else:
            d = {"event_type": "event", "data": {"raw": str(event)}}
        chain_dir = d.pop("chain_dir", "")
        if chain_dir:
            self._service.push_event(chain_dir, d)

    def trace_submit(
        self,
        chain_dir: str,
        sub_state: str,
        decision: str,
        evidence: str,
        task_id: str | None = None,
    ) -> None:
        self._service.push_event(chain_dir, {
            "event_type": "SUBMIT",
            "source": "workflow",
            "sub_state": sub_state,
            "task_id": task_id or "",
            "data": {"decision": decision, "evidence": evidence},
        })

    def trace_transition(
        self,
        chain_dir: str,
        gate: str,
        from_node: str,
        to_node: str,
        task_id: str | None = None,
    ) -> None:
        self._service.push_event(chain_dir, {
            "event_type": "TRANSITION",
            "source": "workflow",
            "task_id": task_id or "",
            "data": {"gate": gate, "from": from_node, "to": to_node},
        })

    def trace_error(
        self,
        chain_dir: str,
        error_type: str,
        message: str,
        task_id: str | None = None,
    ) -> None:
        self._service.push_event(chain_dir, {
            "event_type": "ERROR",
            "source": "workflow",
            "task_id": task_id or "",
            "data": {"error_type": error_type, "message": message},
        })

    def has_kg_query(
        self,
        chain_dir: str,
        task_id: str = "",
        session_id: str = "",
        layers: set[str] | list[str] | None = None,
    ) -> bool:
        """Check if a kg_query event exists in the trace."""
        events = self._service.load_traces(chain_dir)
        for ev in events:
            if ev.event_type == "kg_query":
                if task_id and ev.task_id != task_id:
                    continue
                return True
        return False
