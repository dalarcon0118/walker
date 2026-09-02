"""application/ports/trace_repository.py — Port for TraceGraph persistence.

Driven port (output port) in Clean Architecture.
Defines the contract that infrastructure implements.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from fast_mcp.features.event_tracing.domain.entities import TraceEvent


class TraceRepository(ABC):
    """Persistence contract for the TraceGraph (trace index)."""

    @abstractmethod
    def load_traces(self, chain_dir: str) -> list[TraceEvent]:
        """Load all events for a sprint (canonical, deduped)."""
        ...

    @abstractmethod
    def push_event(self, chain_dir: str, data: dict) -> dict:
        """Store an event in the trace index (canonical).

        Returns {"ok": bool, "data": {"event_id", "event_type"}}.
        """
        ...
