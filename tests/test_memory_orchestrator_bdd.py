"""tests/test_memory_orchestrator_bdd.py — BDD tests for MemoryOrchestrator."""
from __future__ import annotations

import pytest

from features.delta_tracking.application.memory_orchestrator import MemoryOrchestrator


class TestMemoryOrchestratorBDD:
    def test_empty_ops_returns_empty_results(self):
        orchestrator = MemoryOrchestrator()
        results = orchestrator.execute_phase("SPEC-001", [])
        assert results == []

    def test_delta_upsert_spec_calls_spring_service(self):
        class FakeSpring:
            def __init__(self):
                self.updated = {}
            def update_spec(self, spring_id, spec_id, updates):
                self.updated = {"spring_id": spring_id, "spec_id": spec_id, "updates": updates}
                return True

        spring = FakeSpring()
        orchestrator = MemoryOrchestrator(spring_service=spring)
        ops = [{"target": "delta", "op": "upsert_spec", "data": {"spec_id": "SPEC-001", "status": "specified", "priority": "high"}}]
        results = orchestrator.execute_phase("SPEC-001", ops, context={"spring_id": "spring-1"})
        assert len(results) == 1
        assert results[0]["ok"] is True
        assert results[0]["updated"] is True
        assert spring.updated["spec_id"] == "SPEC-001"
        assert "priority" in spring.updated["updates"]

    def test_ckg_save_operation(self):
        class FakeCKG:
            def save_nodes(self, nodes):
                return {"saved": len(nodes)}

        ckg = FakeCKG()
        orchestrator = MemoryOrchestrator(ckg_port=ckg)
        ops = [{"target": "ckg", "op": "save", "data": {"type": "decision", "id": "ADR-001"}}]
        results = orchestrator.execute_phase("SPEC-001", ops)
        assert results[0]["ok"] is True
        assert results[0]["saved"] is True

    def test_trace_emit_operation(self):
        class FakeTrace:
            def __init__(self):
                self.events = []
            def log_event(self, payload):
                self.events.append(payload)

        trace = FakeTrace()
        orchestrator = MemoryOrchestrator(trace_port=trace)
        ops = [{"target": "trace", "op": "emit", "event": "decision_approved", "data": {"spec_id": "SPEC-001"}}]
        results = orchestrator.execute_phase("SPEC-001", ops, context={"spring_id": "spring-1"})
        assert results[0]["ok"] is True
        assert results[0]["emitted"] is True
        assert len(trace.events) == 1
        assert trace.events[0]["event"] == "decision_approved"

    def test_unknown_target_returns_error(self):
        orchestrator = MemoryOrchestrator()
        ops = [{"target": "unknown", "op": "test"}]
        results = orchestrator.execute_phase("SPEC-001", ops)
        assert results[0]["ok"] is False
        assert "Unknown target" in results[0]["error"]

    def test_mixed_ops_executed_in_order(self):
        class FakeSpring:
            def update_spec(self, spring_id, spec_id, updates):
                return True

        spring = FakeSpring()
        trace_events = []

        class FakeTrace:
            def log_event(self, payload):
                trace_events.append(payload["event"])

        orchestrator = MemoryOrchestrator(spring_service=spring, trace_port=FakeTrace())
        ops = [
            {"target": "delta", "op": "upsert_spec", "data": {"spec_id": "SPEC-001"}},
            {"target": "trace", "op": "emit", "event": "spec_specified"},
            {"target": "trace", "op": "emit", "event": "spec_persisted"},
        ]
        results = orchestrator.execute_phase("SPEC-001", ops, context={"spring_id": "spring-1"})
        assert len(results) == 3
        assert all(r["ok"] for r in results)
        assert trace_events == ["spec_specified", "spec_persisted"]

    def test_skip_when_no_port_available(self):
        orchestrator = MemoryOrchestrator()
        ops = [{"target": "delta", "op": "upsert_spec", "data": {"spec_id": "SPEC-001"}}]
        results = orchestrator.execute_phase("SPEC-001", ops)
        assert results[0]["ok"] is True
        assert results[0].get("skipped") is True
