"""domain/services/signal_bus.py — Pub/Sub de señales entre nodos.

P1: Signal Events BPMN.
Permite que nodos se comuniquen sin acoplamiento directo.

Responsabilidad: mantener señales emitidas y despacharlas a nodos
suscriptos. No persiste en disco; las señales son eventos en memoria
durante la ejecución del workflow.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class SignalEvent:
    name: str
    source_node: str
    source_sub_state: str = ""
    data: dict = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class SignalBus:
    """Bus simple de señales entre nodos del workflow."""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[str]] = {}
        self._history: list[SignalEvent] = []

    def subscribe(self, node_id: str, signal: str) -> None:
        key = f"{node_id}:{signal}"
        self._subscribers.setdefault(key, []).append(node_id)

    def emit(self, signal: str, source_node: str, source_sub_state: str = "", data: dict | None = None) -> list[str]:
        event = SignalEvent(
            name=signal,
            source_node=source_node,
            source_sub_state=source_sub_state,
            data=data or {},
        )
        self._history.append(event)
        notified: list[str] = []
        for key, nodes in self._subscribers.items():
            if key.endswith(f":{signal}") or key == f"*:{signal}":
                for node in nodes:
                    if node not in notified:
                        notified.append(node)
        return notified

    def pending_for(self, node_id: str) -> list[str]:
        signals: list[str] = []
        for key in self._subscribers:
            if key.startswith(f"{node_id}:") or key.startswith("*:"):
                signals.append(key.split(":", 1)[1])
        return list(set(signals))

    def history(self, limit: int = 50) -> list[dict]:
        return [{"name": e.name, "source_node": e.source_node, "source_sub_state": e.source_sub_state, "timestamp": e.timestamp} for e in self._history[-limit:]]
