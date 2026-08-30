"""infrastructure/engine_metrics.py — EngineMetrics (default IEngineMetrics).

Observabilidad del motor (ADR-WALKER-067 F5/J): p99 de submit/transition,
tamaño de state, contadores. Default in-memory host-agnóstico; el host puede
reemplazarlo por uno que alimente su telemetría.
"""
from __future__ import annotations

import time
from collections import deque
from typing import Any

from shared.core.engine.domain.ports import IEngineMetrics


class EngineMetrics(IEngineMetrics):
    """Métricas in-memory del motor (p99 con ventana deslizante)."""

    def __init__(self, window: int = 200):
        self._submits: deque[float] = deque(maxlen=window)
        self._transitions: deque[float] = deque(maxlen=window)
        self._state_sizes: deque[int] = deque(maxlen=window)
        self._started_at: float = time.time()

    @staticmethod
    def _p99(values: deque) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        idx = max(0, int(len(ordered) * 0.99) - 1)
        return round(ordered[idx], 3)

    def record_submit(self, duration_ms: float) -> None:
        self._submits.append(float(duration_ms))

    def record_transition(self, duration_ms: float) -> None:
        self._transitions.append(float(duration_ms))

    def record_state_size(self, fields: int) -> None:
        self._state_sizes.append(int(fields))

    def snapshot(self) -> dict[str, Any]:
        return {
            "uptime_seconds": round(time.time() - self._started_at, 1),
            "submit": {
                "count": len(self._submits),
                "p99_ms": self._p99(self._submits),
                "avg_ms": round(sum(self._submits) / len(self._submits), 3)
                if self._submits else 0.0,
            },
            "transition": {
                "count": len(self._transitions),
                "p99_ms": self._p99(self._transitions),
                "avg_ms": round(sum(self._transitions) / len(self._transitions), 3)
                if self._transitions else 0.0,
            },
            "state": {
                "samples": len(self._state_sizes),
                "avg_fields": round(sum(self._state_sizes) / len(self._state_sizes), 1)
                if self._state_sizes else 0,
            },
        }