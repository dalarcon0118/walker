"""infrastructure/cognitive_trace_repo.py — TraceRepository backed by CognitiveGraph.

DI-injected: receives a CognitiveGraph instance, no service locator.
Simplified from legacy (265 LOC → ~120 LOC): removed project_root resolution
(DI-injected CG handles it), removed legacy imports.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from returns.pipeline import is_successful
from returns.result import safe

from fast_mcp.features.event_tracing.application.ports.trace_repository import (
    TraceRepository,
)
from fast_mcp.features.event_tracing.domain.entities import TraceEvent

logger = logging.getLogger(__name__)

# In-process cache: (chain_dir -> (expires_at, events))
_CACHE_TTL = 5.0
_CACHE: dict[str, tuple[float, list[TraceEvent]]] = {}


class CognitiveTraceRepository(TraceRepository):
    """Implementation of TraceRepository using CognitiveGraph.

    DI-injected: the CG instance is provided at construction time.
    """

    INDEX = "trace"

    def __init__(self, cg: Any) -> None:
        self._cg = cg

    def load_traces(self, chain_dir: str) -> list[TraceEvent]:
        if not chain_dir:
            return []

        cached = _CACHE.get(chain_dir)
        if cached and cached[0] > time.time():
            return cached[1]

        events = self._load_from_cg()
        events = _dedupe(events)
        events.sort(key=lambda ev: (ev.timestamp or "", ev.event_type or ""))
        _CACHE[chain_dir] = (time.time() + _CACHE_TTL, events)
        return events

    def push_event(self, chain_dir: str, data: dict) -> dict:
        _CACHE.pop(chain_dir, None)
        try:
            event_id = f"ev-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
            ts = datetime.now(timezone.utc).isoformat()
            event = {
                "event_type": data.get("event_type", "event"),
                "source": data.get("source", "walker_task"),
                "sprint_id": data.get("sprint_id", ""),
                "task_id": data.get("task_id", ""),
                "node": data.get("node", ""),
                "sub_state": data.get("sub_state", ""),
                "tool": data.get("tool", ""),
                "input": _coerce_json(data.get("input", {})),
                "output": _coerce_json(data.get("output", {})),
                "files": _coerce_list(data.get("files", [])),
                "timestamp": ts,
                "started_at": data.get("started_at", ts),
                "finished_at": data.get("finished_at", ts),
                "duration_ms": data.get("duration_ms", 0),
                "metadata": data.get("metadata", {}),
                "data": data.get("data", {}),
            }
            self._cg.store(self.INDEX, event_id, event)
            return {"ok": True, "data": {"event_id": event_id, "event_type": event["event_type"]}}
        except Exception as exc:
            logger.error("push_event failed: %s", exc)
            return {"ok": False, "error": str(exc)}

    def _load_from_cg(self) -> list[TraceEvent]:
        """Load events from the trace index in CognitiveGraph."""
        try:
            result = self._cg.search(self.INDEX, {})
            events: list[TraceEvent] = []
            for h in result.get("hits", {}).get("hits", []):
                source = dict(h.get("_source", {}))
                source["id"] = h.get("_id", "")
                events.append(TraceEvent.from_dict(source))
            return events
        except Exception as exc:
            logger.warning("CognitiveGraph trace not available: %s", exc)
            return []


def _dedupe(events: list[TraceEvent]) -> list[TraceEvent]:
    """Uniqueness by signature (timestamp, event_type, tool, data)."""
    seen: set[tuple] = set()
    result: list[TraceEvent] = []
    for ev in events:
        d = ev.to_dict()
        signature = (
            d.get("timestamp", ""),
            d.get("event_type", ""),
            d.get("tool", ""),
            json.dumps(d.get("data") or {}, sort_keys=True, default=str),
        )
        if signature in seen:
            continue
        seen.add(signature)
        result.append(ev)
    return result


def _coerce_json(value: Any) -> Any:
    """Convert str JSON to object; on failure, wrap as {"raw": ...}."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return {"raw": value}
    return value


def _coerce_list(value: Any) -> list:
    """Convert str JSON list to list; if not a list, wrap it."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
    return [value]
