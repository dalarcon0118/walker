"""domain/events.py — Eventos de dominio del CKG.

Cada evento representa un hecho relevante del ciclo de vida del conocimiento.
Se registran en el índice trace para trazabilidad y debugging.

Eventos:
  - KnowledgeSaved: se guardó un nodo de conocimiento
  - DecisionSuperseded: una decisión fue reemplazada por otra
  - KnowledgeSearched: se ejecutó una búsqueda en el CKG
  - KnowledgeIndexed: se indexó conocimiento externo
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class DomainEvent:
    """Base event with timestamp and source."""
    occurred_at: str = field(default_factory=_now)
    source: str = "cognitive_knowledge"

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.__class__.__name__,
            "occurred_at": self.occurred_at,
            "source": self.source,
        }


@dataclass
class KnowledgeSaved(DomainEvent):
    """Knowledge nodes were saved to CKG."""
    node_ids: list[str] = field(default_factory=list)
    node_types: list[str] = field(default_factory=list)
    count: int = 0
    project_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        base = super().to_dict()
        base.update({
            "node_ids": self.node_ids,
            "node_types": self.node_types,
            "count": self.count,
            "project_id": self.project_id,
        })
        return base


@dataclass
class DecisionSuperseded(DomainEvent):
    """A decision was superseded by a newer one."""
    old_node_id: str = ""
    new_node_id: str = ""
    statement: str = ""
    project_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        base = super().to_dict()
        base.update({
            "old_node_id": self.old_node_id,
            "new_node_id": self.new_node_id,
            "statement": self.statement,
            "project_id": self.project_id,
        })
        return base


@dataclass
class KnowledgeSearched(DomainEvent):
    """A search was executed on the CKG."""
    query: dict = field(default_factory=dict)
    index: str = ""
    hits: int = 0
    project_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        base = super().to_dict()
        base.update({
            "query": self.query,
            "index": self.index,
            "hits": self.hits,
            "project_id": self.project_id,
        })
        return base


@dataclass
class KnowledgeIndexed(DomainEvent):
    """External knowledge was indexed into CKG."""
    source_url: str = ""
    source_type: str = ""
    nodes_indexed: int = 0
    project_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        base = super().to_dict()
        base.update({
            "source_url": self.source_url,
            "source_type": self.source_type,
            "nodes_indexed": self.nodes_indexed,
            "project_id": self.project_id,
        })
        return base


class EventBus:
    """Simple in-memory event bus for CKG domain events."""

    def __init__(self) -> None:
        self._listeners: list[callable] = []

    def subscribe(self, listener: callable) -> None:
        self._listeners.append(listener)

    def publish(self, event: DomainEvent) -> None:
        for listener in self._listeners:
            try:
                listener(event)
            except Exception:
                pass
