"""test_fast_mcp_event_tracing_bdd.py — BDD tests for event_tracing feature.

Sprint 5.3: domain + application + infrastructure layer tests.
"""
from __future__ import annotations

import json
import pytest
from pathlib import Path
from datetime import datetime, timezone


# ── Domain ──


class TestTraceEvent:
    """TraceEvent entity tests."""

    def test_from_dict_minimal(self):
        from fast_mcp.features.event_tracing.domain.entities import TraceEvent
        ev = TraceEvent.from_dict({})
        assert ev.event_type == "event"
        assert ev.source == "walker"
        assert ev.files == []

    def test_from_dict_full(self):
        from fast_mcp.features.event_tracing.domain.entities import TraceEvent
        d = {
            "id": "ev-001",
            "event_type": "SUBMIT",
            "source": "workflow",
            "sprint_id": "sp-1",
            "task_id": "t-1",
            "node": "IMPLEMENT",
            "tool": "bash",
            "input": {"cmd": "ls"},
            "output": {"stdout": "file.py"},
            "files": ["file.py"],
            "timestamp": "2026-01-01T00:00:00Z",
            "duration_ms": 150,
            "data": {"decision": "approve"},
        }
        ev = TraceEvent.from_dict(d)
        assert ev.id == "ev-001"
        assert ev.event_type == "SUBMIT"
        assert ev.sprint_id == "sp-1"
        assert ev.tool == "bash"
        assert ev.files == ["file.py"]
        assert ev.duration_ms == 150

    def test_to_dict_symmetric(self):
        from fast_mcp.features.event_tracing.domain.entities import TraceEvent
        d = {
            "id": "ev-002",
            "event_type": "TRANSITION",
            "source": "workflow",
            "sprint_id": "sp-1",
            "task_id": "t-2",
            "tool": "bash",
            "data": {"gate": "auto", "from": "A", "to": "B"},
        }
        ev = TraceEvent.from_dict(d)
        out = ev.to_dict()
        assert out["id"] == "ev-002"
        assert out["event_type"] == "TRANSITION"
        assert out["data"]["gate"] == "auto"

    def test_roundtrip(self):
        from fast_mcp.features.event_tracing.domain.entities import TraceEvent
        original = TraceEvent(
            id="ev-003",
            event_type="ERROR",
            source="walker_task",
            sprint_id="sp-2",
            task_id="t-3",
            tool="python",
            data={"error_type": "ImportError", "message": "no module"},
        )
        d = original.to_dict()
        restored = TraceEvent.from_dict(d)
        assert restored.id == original.id
        assert restored.event_type == original.event_type
        assert restored.data == original.data


class TestTraceEventValues:
    """Value object tests."""

    def test_event_type_valid(self):
        from fast_mcp.features.event_tracing.domain.values import EventType
        assert EventType("task_execute").value == "task_execute"

    def test_event_type_invalid(self):
        from fast_mcp.features.event_tracing.domain.values import EventType
        with pytest.raises(ValueError, match="Invalid EventType"):
            EventType("bogus")

    def test_event_id_empty(self):
        from fast_mcp.features.event_tracing.domain.values import EventId
        with pytest.raises(ValueError):
            EventId("")

    def test_trace_filters_defaults(self):
        from fast_mcp.features.event_tracing.domain.values import TraceFilters
        f = TraceFilters()
        assert f.max_results == 50

    def test_trace_filters_min(self):
        from fast_mcp.features.event_tracing.domain.values import TraceFilters
        with pytest.raises(ValueError):
            TraceFilters(max_results=0)


class TestDomainEvents:
    """Domain event tests."""

    def test_event_recorded(self):
        from fast_mcp.features.event_tracing.domain.events import EventRecorded
        ev = EventRecorded(event_id="e-1", event_type="SUBMIT")
        assert ev.event_id == "e-1"

    def test_event_recorded_empty_id(self):
        from fast_mcp.features.event_tracing.domain.events import EventRecorded
        with pytest.raises(ValueError):
            EventRecorded(event_id="")

    def test_trace_queried(self):
        from fast_mcp.features.event_tracing.domain.events import TraceQueried
        q = TraceQueried(query_id="q-1", result_count=5)
        assert q.result_count == 5


# ── Application ──


class TestTraceService:
    """TraceService application facade tests."""

    def _make_service(self, events=None):
        from fast_mcp.features.event_tracing.application.services.trace_service import TraceService
        from fast_mcp.features.event_tracing.domain.entities import TraceEvent
        if events is None:
            events = []
        class FakeRepo:
            def load_traces(self, chain_dir):
                return events
            def push_event(self, chain_dir, data):
                return {"ok": True, "data": {"event_id": "ev-test", "event_type": data.get("event_type", "event")}}
        return TraceService(FakeRepo())

    def test_load_traces(self):
        svc = self._make_service([])
        assert svc.load_traces("chain-1") == []

    def test_push_event(self):
        svc = self._make_service()
        result = svc.push_event("chain-1", {"event_type": "SUBMIT"})
        assert result["ok"] is True

    def test_build_reflection_empty(self):
        svc = self._make_service([])
        pkg = svc.build_reflection_context("chain-1", task_id="t-1")
        assert pkg["task_id"] == "t-1"
        assert pkg["attempt"] == 1
        assert pkg["hint"]  # non-empty

    def test_build_reflection_with_events(self):
        from fast_mcp.features.event_tracing.domain.entities import TraceEvent
        events = [
            TraceEvent(event_type="TRANSITION", task_id="t-1", data={"gate": "g1", "from": "A", "to": "REFINE"}),
            TraceEvent(event_type="ERROR", task_id="t-1", data={"error_type": "RuntimeError", "message": "boom"}),
            TraceEvent(event_type="SUBMIT", task_id="t-1", data={"decision": "fix", "evidence": "patch applied"}),
        ]
        svc = self._make_service(events)
        pkg = svc.build_reflection_context(
            "chain-1", task_id="t-1",
            last_error="boom", last_reason="test failed",
        )
        assert pkg["source_node"] == "A"
        assert pkg["attempt"] == 1
        # TRANSITION is not in past_attempts (only ERROR, SUBMIT, AUTO_ADVANCE)
        assert len(pkg["past_attempts"]) == 2
        assert "error=boom" in pkg["error_context"]

    def test_build_reflection_with_failed_gates(self):
        from fast_mcp.features.event_tracing.domain.entities import TraceEvent
        events = [
            TraceEvent(event_type="ERROR", data={"error_type": "AssertionError", "message": "fail"}),
        ]
        svc = self._make_service(events)
        pkg = svc.build_reflection_context(
            "chain-1", failed_gates="quality_gate, test_gate",
        )
        assert "quality_gate" in pkg["hint"]

    def test_format_reflection_context(self):
        svc = self._make_service()
        pkg = {"task_id": "t-1", "source_node": "IMPLEMENT", "attempt": 1, "max_attempts": 3}
        text = svc.format_reflection_context(pkg)
        assert "task: t-1" in text
        assert "source_node: IMPLEMENT" in text

    def test_format_empty(self):
        svc = self._make_service()
        assert svc.format_reflection_context({}) == "(no reflection context)"

    def test_derive_source_node(self):
        from fast_mcp.features.event_tracing.application.services.trace_service import TraceService
        transitions = [
            {"data": {"from": "ANALYZE", "to": "REFINE"}},
            {"data": {"from": "IMPLEMENT", "to": "REFINE"}},
        ]
        assert TraceService._derive_source_node(transitions) == "IMPLEMENT"

    def test_derive_attempt(self):
        from fast_mcp.features.event_tracing.application.services.trace_service import TraceService
        transitions = [
            {"data": {"from": "A", "to": "REFINE"}},
            {"data": {"from": "B", "to": "REFINE"}},
            {"data": {"from": "C", "to": "SPRINT"}},
        ]
        assert TraceService._derive_attempt(transitions) == 2


# ── Infrastructure ──


class TestCognitiveTraceRepository:
    """CognitiveTraceRepository tests with in-memory CG."""

    def _make_repo(self):
        from fast_mcp.features.event_tracing.infrastructure.cognitive_trace_repo import (
            CognitiveTraceRepository,
        )
        # In-memory CG mock
        class FakeCG:
            def __init__(self):
                self._store = {}
            def search(self, index, query):
                hits = []
                for eid, data in self._store.items():
                    hits.append({"_id": eid, "_source": data})
                return {"hits": {"hits": hits}}
            def store(self, index, eid, data):
                self._store[eid] = data
        return CognitiveTraceRepository(FakeCG())

    def test_load_empty(self):
        repo = self._make_repo()
        assert repo.load_traces("chain-1") == []

    def test_push_and_load(self):
        repo = self._make_repo()
        result = repo.push_event("chain-1", {
            "event_type": "SUBMIT",
            "source": "workflow",
            "task_id": "t-1",
            "data": {"decision": "approve"},
        })
        assert result["ok"] is True
        events = repo.load_traces("chain-1")
        assert len(events) == 1
        assert events[0].event_type == "SUBMIT"

    def test_dedupe(self):
        from fast_mcp.features.event_tracing.infrastructure.cognitive_trace_repo import _dedupe
        from fast_mcp.features.event_tracing.domain.entities import TraceEvent
        ev = TraceEvent(event_type="SUBMIT", tool="bash", data={"key": "val"})
        deduped = _dedupe([ev, ev, ev])
        assert len(deduped) == 1

    def test_dedupe_different(self):
        from fast_mcp.features.event_tracing.infrastructure.cognitive_trace_repo import _dedupe
        from fast_mcp.features.event_tracing.domain.entities import TraceEvent
        ev1 = TraceEvent(event_type="SUBMIT", tool="bash", data={"key": "val"})
        ev2 = TraceEvent(event_type="ERROR", tool="bash", data={"key": "val"})
        deduped = _dedupe([ev1, ev2])
        assert len(deduped) == 2

    def test_coerce_json(self):
        from fast_mcp.features.event_tracing.infrastructure.cognitive_trace_repo import _coerce_json
        assert _coerce_json('{"a": 1}') == {"a": 1}
        assert _coerce_json({"a": 1}) == {"a": 1}
        assert _coerce_json("not json") == {"raw": "not json"}

    def test_coerce_list(self):
        from fast_mcp.features.event_tracing.infrastructure.cognitive_trace_repo import _coerce_list
        assert _coerce_list(["a", "b"]) == ["a", "b"]
        assert _coerce_list('["a"]') == ["a"]
        assert _coerce_list("single") == ["single"]


class TestCognitiveTracer:
    """CognitiveTracer tests."""

    def _make_tracer(self):
        from fast_mcp.features.event_tracing.infrastructure.cognitive_tracer import CognitiveTracer
        from fast_mcp.features.event_tracing.infrastructure.cognitive_trace_repo import (
            CognitiveTraceRepository,
        )
        import fast_mcp.features.event_tracing.infrastructure.cognitive_trace_repo as repo_mod
        # Clear module cache for isolation
        repo_mod._CACHE.clear()

        class FakeCG:
            def __init__(self):
                self._store = {}
            def search(self, index, query):
                hits = [{"_id": eid, "_source": data} for eid, data in self._store.items()]
                return {"hits": {"hits": hits}}
            def store(self, index, eid, data):
                self._store[eid] = data
        cg = FakeCG()
        repo = CognitiveTraceRepository(cg)
        from fast_mcp.features.event_tracing.application.services.trace_service import TraceService
        svc = TraceService(repo)
        return CognitiveTracer(svc), cg

    def test_trace(self):
        tracer, cg = self._make_tracer()
        tracer.trace("chain-1", "task_execute", data={"cmd": "ls"})
        events = tracer._service.load_traces("chain-1")
        assert len(events) == 1
        assert events[0].event_type == "task_execute"

    def test_trace_submit(self):
        tracer, cg = self._make_tracer()
        tracer.trace_submit("chain-1", "DIAGNOSE", "approve", "evidence")
        events = tracer._service.load_traces("chain-1")
        assert events[0].event_type == "SUBMIT"

    def test_trace_transition(self):
        tracer, cg = self._make_tracer()
        tracer.trace_transition("chain-1", "auto", "A", "B")
        events = tracer._service.load_traces("chain-1")
        assert events[0].event_type == "TRANSITION"
        # data is stored as nested dict in CG; from_dict reconstructs it
        d = events[0].to_dict()["data"]
        assert d["gate"] == "auto"

    def test_trace_error(self):
        tracer, cg = self._make_tracer()
        tracer.trace_error("chain-1", "RuntimeError", "boom")
        events = tracer._service.load_traces("chain-1")
        assert events[0].event_type == "ERROR"
        d = events[0].to_dict()["data"]
        assert d["error_type"] == "RuntimeError"

    def test_has_kg_query_false(self):
        tracer, cg = self._make_tracer()
        assert tracer.has_kg_query("chain-1") is False

    def test_has_kg_query_true(self):
        tracer, cg = self._make_tracer()
        # Use push_event to go through the normal path (avoids cache mismatch)
        tracer._service.push_event("chain-1", {"event_type": "kg_query", "task_id": "t-1"})
        assert tracer.has_kg_query("chain-1", task_id="t-1") is True
