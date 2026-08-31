"""domain/values.py — Value Objects del dominio TraceGraph.

VOs que representan conceptos del dominio de event_tracing sin identidad propia.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Self


@dataclass(frozen=True)
class EventId:
    """Identificador de evento en el TraceGraph."""
    value: str

    def __post_init__(self) -> None:
        if not self.value:
            raise ValueError("EventId no puede ser vacio")


@dataclass(frozen=True)
class EventType:
    """Tipo de evento de ejecucion."""
    value: str

    def __post_init__(self) -> None:
        valid = {
            "workflow_state",
            "task_execute",
            "agent_message",
            "tool_call",
            "file_impact",
            "kg_query",
            "event",
        }
        if self.value not in valid:
            raise ValueError(f"EventType invalido: {self.value}. Validos: {sorted(valid)}")


@dataclass(frozen=True)
class EventSource:
    """Fuente del evento."""
    value: str

    def __post_init__(self) -> None:
        valid = {"workflow", "agent_hook", "walker_task", "walker"}
        if self.value not in valid:
            raise ValueError(f"EventSource invalido: {self.value}. Validos: {sorted(valid)}")


@dataclass(frozen=True)
class TraceFilters:
    """Filtros de busqueda en el TraceGraph."""
    event_type: str = ""
    source: str = ""
    agent_id: str = ""
    session_id: str = ""
    sprint_id: str = ""
    task_id: str = ""
    node: str = ""
    tool: str = ""
    max_results: int = 50

    def __post_init__(self) -> None:
        if self.max_results < 1:
            raise ValueError("TraceFilters.max_results debe ser >= 1")
