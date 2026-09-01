"""infrastructure/noop_tracer.py — No-op ITracer implementation.

All trace methods are silent. Used until event_tracing is ported (Sprint 5).
The engine does NOT require tracing to execute; this is the default.
"""
from __future__ import annotations

from typing import Any

from ..domain.ports import ITracer


class NoopTracer(ITracer):
    """Tracer NO-OP: all methods are silent, never raise.

    ADR-WALKER-067 F5/I: the engine does NOT build a concrete logger;
    it uses the ITracer INJECTED by the host. If none is injected,
    this no-op is the default.
    """

    def trace(
        self,
        chain_dir: str,
        event_type: str,
        data: dict | None = None,
        task_id: str | None = None,
        session_id: str | None = None,
    ) -> None:
        pass

    def trace_event(self, event: Any) -> None:
        pass

    def trace_submit(
        self,
        chain_dir: str,
        sub_state: str,
        decision: str,
        evidence: str,
        task_id: str | None = None,
    ) -> None:
        pass

    def trace_transition(
        self,
        chain_dir: str,
        gate: str,
        from_node: str,
        to_node: str,
        task_id: str | None = None,
    ) -> None:
        pass

    def trace_error(
        self,
        chain_dir: str,
        error_type: str,
        message: str,
        task_id: str | None = None,
    ) -> None:
        pass

    def has_kg_query(
        self,
        chain_dir: str,
        task_id: str = "",
        session_id: str = "",
        layers: set[str] | list[str] | None = None,
    ) -> bool:
        return False
