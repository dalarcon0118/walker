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
