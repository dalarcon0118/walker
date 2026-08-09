"""domain/events.py — Eventos de dominio del CPG.

Cada evento representa un hecho relevante del ciclo de vida del análisis
de código. Se registran en el índice trace para trazabilidad y debugging.

Eventos:
  - SymbolIndexed: se indexó un símbolo (función, clase, etc.) en el CPG
  - AnalysisCompleted: completó un análisis (business, dead code, etc.)
  - PatternDetected: se detectó un patrón arquitectónico
  - SecurityRiskFound: se encontró un riesgo de seguridad en el DFG scan
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
    source: str = "code_analysis"

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.__class__.__name__,
            "occurred_at": self.occurred_at,
            "source": self.source,
        }


@dataclass
class SymbolIndexed(DomainEvent):
    """A symbol was indexed into the CPG."""
    symbol_id: str = ""
    symbol_type: str = ""
    name: str = ""
    file: str = ""
    line: int = 0

    def to_dict(self) -> dict[str, Any]:
        base = super().to_dict()
        base.update({
            "symbol_id": self.symbol_id,
            "symbol_type": self.symbol_type,
            "name": self.name,
            "file": self.file,
            "line": self.line,
        })
        return base


@dataclass
class AnalysisCompleted(DomainEvent):
    """An analysis operation completed."""
    analysis_type: str = ""
    scope: str = ""
    duration_ms: int = 0
    result_summary: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        base = super().to_dict()
        base.update({
            "analysis_type": self.analysis_type,
            "scope": self.scope,
            "duration_ms": self.duration_ms,
            "result_summary": self.result_summary,
        })
        return base


@dataclass
class PatternDetected(DomainEvent):
    """An architecture pattern was detected in the CPG."""
    pattern_type: str = ""
    file: str = ""
    symbols: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        base = super().to_dict()
        base.update({
            "pattern_type": self.pattern_type,
            "file": self.file,
            "symbols": self.symbols,
        })
        return base


@dataclass
class SecurityRiskFound(DomainEvent):
    """A security risk was found during DFG scan."""
    risk_type: str = ""
    file: str = ""
    line: int = 0
    detail: str = ""
    variable: str = ""
    flow: str = ""

    def to_dict(self) -> dict[str, Any]:
        base = super().to_dict()
        base.update({
            "risk_type": self.risk_type,
            "file": self.file,
            "line": self.line,
            "detail": self.detail,
            "variable": self.variable,
            "flow": self.flow,
        })
        return base


class EventBus:
    """Simple in-memory event bus for code analysis domain events.

    Events are published and can be consumed by listeners (e.g., trace_push).
    """

    def __init__(self) -> None:
        self._listeners: list[callable] = []

    def subscribe(self, listener: callable) -> None:
        self._listeners.append(listener)

    def publish(self, event: DomainEvent) -> None:
        for listener in self._listeners:
            try:
                listener(event)
            except Exception:
                pass  # Best-effort: never break analysis because of event delivery
