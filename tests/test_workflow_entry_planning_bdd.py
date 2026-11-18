"""tests/test_workflow_entry_planning_bdd.py — Entrada del sprint planning al workflow.

Cubre:
  - handle_resolve_project_sprint: prefer active, fallback planning, ninguno.
  - OrchestrationService.transition con gate project_selected hacia SPRINT:
    resuelve ACTIVE/PLANNING según el sprint real; sin sprint deja el declarado.
  - Seam de side effects: NoOp no rompe; fake recibe la llamada con args.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch

import pytest

from fast_mcp.features.workflow.application.bundle import EngineBundle
from fast_mcp.features.workflow.application.orchestrator import OrchestrationService
from fast_mcp.features.workflow.domain.entities import (
    Gate, SubState, WorkflowGraph, WorkflowNode, WorkflowState,
)
from fast_mcp.features.workflow.domain.execution import ExecutionContext
from fast_mcp.features.workflow.domain.ports import (
    ITracer, IWorkflowIO, IWorkflowStore, ISideEffectEngine,
)

from fast_mcp.features.delta_tracking.handlers.sprint import handle_resolve_project_sprint


# ── Mocks (mismos patrones que test_fast_mcp_workflow_handlers_bdd) ──

@dataclass
class MockWorkflowStore(IWorkflowStore):
    _graph: Any = None
    _state: Any = None
    _saved: list = field(default_factory=list)

    def load_graph(self):
        return self._graph
    def load_state(self, chain_dir):
        return self._state
    def save_state(self, chain_dir, state):
        self._saved.append(state)
        self._state = state
        return True


@dataclass
class MockTracer(ITracer):
    def trace(self, *a, **kw): pass
    def trace_event(self, e): pass
    def trace_submit(self, *a, **kw): pass
    def trace_transition(self, *a, **kw): pass
    def trace_error(self, *a, **kw): pass
    def has_kg_query(self, *a, **kw): return False


@dataclass
class MockIO(IWorkflowIO):
    def run_script(self, script, chain_dir=None):
        return {"exit_code": 0, "stdout": "", "stderr": ""}
    def eprint(self, *a, **kw): pass


@dataclass
class MockSideEffects(ISideEffectEngine):
    def run_compensation_handler(self, name, ctx):
        from returns.result import Success
        return Success({"ok": True, "skipped": True})


def _make_graph():
    return WorkflowGraph(
        nodes={
            "START": WorkflowNode(
                node_id="START",
                sub_states=[SubState(key="INIT", label="Init")],
            ),
            "SPRINT": WorkflowNode(
                node_id="SPRINT",
                sub_states=[
                    SubState(key="ACTIVE", label="Active"),
                    SubState(key="PLANNING", label="Planning"),
                ],
            ),
        },
        edges=[Gate(name="project_selected", source_node="START", target_node="SPRINT",
                    target_sub_state="ACTIVE")],
        edges_by_source={
            "START": [Gate(name="project_selected", source_node="START", target_node="SPRINT",
                           target_sub_state="ACTIVE")],
        },
        initial_state="START",
    )


def _make_orchestrator(graph, state, chain_dir="/tmp/test", side_effects=None):
    store = MockWorkflowStore(_graph=graph, _state=state)
    bundle = EngineBundle(
        workflow_store=store,
        workflow_io=MockIO(),
        tracer=MockTracer(),
        side_effects=side_effects if side_effects is not None else MockSideEffects(),
        chain_dir=chain_dir,
    )
    return OrchestrationService(bundle=bundle), store


def _exec(chain_dir="/tmp/test"):
    return ExecutionContext(
        session_id="t", trace_id="tr", span_id="sp", chain_dir=chain_dir, task_id="",
    )


# ── 1. handle_resolve_project_sprint ───────────────────────────────────

class TestResolveProjectSprint:
    def _svc(self, tmp_path):
        from fast_mcp.features.delta_tracking.infrastructure.cognitive_delta_repo import (
            CognitiveDeltaRepository,
        )
        from fast_mcp.features.delta_tracking.application.services.sprint_service import SprintService
        from fast_mcp.shared_kernel.cognitive_graph import build_cognitive_graph

        g = build_cognitive_graph(str(tmp_path))
        g.register_index("delta", {
            "fields": {"task": "spec_id", "spec": "spec_id", "spring": "spring_id",
                       "domain": "domain", "type": "type"},
            "types": {"spec": {}, "spring": {}},
            "description": "d",
            "search": {"type": "fulltext", "fields": ["objective", "title", "description", "domain"]},
        })
        repo = CognitiveDeltaRepository(g)
        return SprintService(project_root="/tmp/test", repo=repo)

    def test_only_planning_found_true_status_planning(self, tmp_path):
        svc = self._svc(tmp_path)
        svc.create_spring("sprint-1", goal="g", status="planning", project_id="p")
        res = handle_resolve_project_sprint({"project_id": "p", "_sprint_service": svc})
        assert res["ok"] is True
        assert res["found"] is True
        assert res["status"] == "planning"
        assert res["spring_id"] == "sprint-1"

    def test_active_plus_planning_returns_active(self, tmp_path):
        svc = self._svc(tmp_path)
        svc.create_spring("sprint-p", goal="g", status="planning", project_id="p")
        svc.create_spring("sprint-a", goal="g", status="active", project_id="p")
        res = handle_resolve_project_sprint({"project_id": "p", "_sprint_service": svc})
        assert res["found"] is True
        assert res["status"] == "active"
        assert res["spring_id"] == "sprint-a"

    def test_none_found_false(self, tmp_path):
        svc = self._svc(tmp_path)
        res = handle_resolve_project_sprint({"project_id": "p", "_sprint_service": svc})
        assert res["ok"] is True
        assert res["found"] is False

    def test_session_id_returns_session_sprint(self, tmp_path):
        svc = self._svc(tmp_path)
        svc.create_spring("sprint-s1", goal="g", status="planning", project_id="p")
        svc.open_session(session_id="s1", project_id="p", spring_id="sprint-s1", goal="g")
        # another planning sprint to ensure not chosen
        svc.create_spring("sprint-other", goal="g", status="planning", project_id="p")
        res = handle_resolve_project_sprint({"project_id": "p", "_sprint_service": svc, "session_id": "s1"})
        assert res["found"] is True
        assert res["spring_id"] == "sprint-s1"
        assert res["resolved_by"] == "session"

    def test_no_session_picks_most_recent_planning_deterministic(self, tmp_path):
        svc = self._svc(tmp_path)
        svc.create_spring("sprint-old", goal="g", status="planning", project_id="p")
        svc.create_spring("sprint-new", goal="g", status="planning", project_id="p")
        svc.update_spring("sprint-new", {"updated_at": "9999-12-31T23:59:59+00:00"})
        # invert list order returned by service
        orig = svc.list_springs
        svc.list_springs = lambda status="": list(reversed(orig(status)))
        res = handle_resolve_project_sprint({"project_id": "p", "_sprint_service": svc})
        assert res["found"] is True
        assert res["spring_id"] == "sprint-new"
        assert res["resolved_by"] == "planning"


# ── 2. transition project_selected ─────────────────────────────────────

class TestTransitionEntryPlanning:
    def test_planning_sprint_enters_sprint_planning(self):
        graph = _make_graph()
        state = WorkflowState(current_node="START", current_sub_state="INIT", sub_state_index=0)
        orch, store = _make_orchestrator(graph, state)

        fake_svc = type("S", (), {})()
        fake_svc.get_active_spring = lambda: None
        fake_svc.list_springs = lambda status="": [{"spring_id": "s1", "status": "planning"}] if status == "planning" else []

        with patch("fast_mcp.composition._build_sprint_service", return_value=fake_svc):
            result = orch.transition(_exec(), "project_selected")
        assert result["ok"] is True
        assert result["to_node"] == "SPRINT"
        assert result["to_sub_state"] == "PLANNING"

    def test_active_sprint_enters_sprint_active(self):
        graph = _make_graph()
        state = WorkflowState(current_node="START", current_sub_state="INIT", sub_state_index=0)
        orch, store = _make_orchestrator(graph, state)

        fake_svc = type("S", (), {})()
        fake_svc.get_active_spring = lambda: {"spring_id": "s1", "status": "active"}
        fake_svc.list_springs = lambda status="": []

        with patch("fast_mcp.composition._build_sprint_service", return_value=fake_svc):
            result = orch.transition(_exec(), "project_selected")
        assert result["ok"] is True
        assert result["to_node"] == "SPRINT"
        assert result["to_sub_state"] == "ACTIVE"

    def test_session_sprint_overrides_active(self):
        graph = _make_graph()
        state = WorkflowState(current_node="START", current_sub_state="INIT", sub_state_index=0)
        orch, store = _make_orchestrator(graph, state)

        fake_svc = type("S", (), {})()

        def get_session(sid):
            if sid == "s1":
                return {"session_id": "s1", "spring_id": "sprint-plan"}
            return None

        def get_spring(sid):
            if sid == "sprint-plan":
                return {"spring_id": "sprint-plan", "status": "planning"}
            return None

        fake_svc.get_session = get_session
        fake_svc.get_spring = get_spring
        fake_svc.get_active_spring = lambda: {"spring_id": "sprint-active", "status": "active"}
        fake_svc.list_springs = lambda status="": []

        ctx = _exec()
        ctx = ExecutionContext(
            session_id="s1", trace_id="tr", span_id="sp", chain_dir="/tmp/test", task_id="",
        )
        with patch("fast_mcp.composition._build_sprint_service", return_value=fake_svc):
            result = orch.transition(ctx, "project_selected")
        assert result["ok"] is True
        assert result["to_sub_state"] == "PLANNING"

    def test_no_session_with_active_enters_active(self):
        graph = _make_graph()
        state = WorkflowState(current_node="START", current_sub_state="INIT", sub_state_index=0)
        orch, store = _make_orchestrator(graph, state)

        fake_svc = type("S", (), {})()
        fake_svc.get_active_spring = lambda: {"spring_id": "s1", "status": "active"}
        fake_svc.list_springs = lambda status="": []

        ctx = ExecutionContext(
            session_id="", trace_id="tr", span_id="sp", chain_dir="/tmp/test", task_id="",
        )
        with patch("fast_mcp.composition._build_sprint_service", return_value=fake_svc):
            result = orch.transition(ctx, "project_selected")
        assert result["ok"] is True
        assert result["to_sub_state"] == "ACTIVE"

    def test_no_sprint_respects_declared_target(self):
        graph = _make_graph()
        state = WorkflowState(current_node="START", current_sub_state="INIT", sub_state_index=0)
        orch, store = _make_orchestrator(graph, state)

        fake_svc = type("S", (), {})()
        fake_svc.get_active_spring = lambda: None
        fake_svc.list_springs = lambda status="": []

        with patch("fast_mcp.composition._build_sprint_service", return_value=None):
            result = orch.transition(_exec(), "project_selected")
        assert result["ok"] is True
        assert result["to_node"] == "SPRINT"
        assert result["to_sub_state"] == "ACTIVE"

    def test_resolution_failure_fail_open(self):
        graph = _make_graph()
        state = WorkflowState(current_node="START", current_sub_state="INIT", sub_state_index=0)
        orch, store = _make_orchestrator(graph, state)

        def boom(*a, **k):
            raise RuntimeError("boom")
        with patch("fast_mcp.composition._build_sprint_service", side_effect=boom):
            result = orch.transition(_exec(), "project_selected")
        assert result["ok"] is True
        assert result["to_sub_state"] == "ACTIVE"


# ── 3. Seam side effects ────────────────────────────────────────────────

class TestSideEffectSeam:
    def test_no_apply_side_effects_does_not_break_submit(self):
        graph = _make_graph_submit()
        state = WorkflowState(current_node="START", current_sub_state="INIT", sub_state_index=0)
        # state needs required? INIT has no required_fields
        orch, store = _make_orchestrator(graph, state, side_effects=MockSideEffects())
        # MockSideEffects has no apply_side_effects → getattr None → no-op
        res = orch.submit(_exec(), fields={})
        assert res["ok"] is True

    def test_fake_engine_receives_call_with_args(self):
        graph = _make_graph_submit()
        state = WorkflowState(current_node="START", current_sub_state="INIT", sub_state_index=0)
        calls = []

        class FakeEngine:
            def apply_side_effects(self, sub_state, state, chain_dir, ctx=None, fields=None):
                calls.append({
                    "sub_state": sub_state,
                    "state": state,
                    "chain_dir": chain_dir,
                    "ctx": ctx,
                    "fields": fields,
                })
                return {"ok": True}

        orch, store = _make_orchestrator(graph, state, side_effects=FakeEngine())
        res = orch.submit(_exec(), fields={"x": 1})
        assert res["ok"] is True
        assert len(calls) == 1
        c = calls[0]
        assert c["chain_dir"] == "/tmp/test"
        assert c["fields"] == {"x": 1}

    def test_fake_engine_failure_returns_side_effect_failed_no_save(self):
        graph = _make_graph_submit()
        state = WorkflowState(current_node="START", current_sub_state="INIT", sub_state_index=0)
        store_ref = {}

        class FakeEngine:
            def apply_side_effects(self, *a, **k):
                return {"ok": False, "error": "boom"}

        orch, store = _make_orchestrator(graph, state, side_effects=FakeEngine())
        before = len(store._saved)
        res = orch.submit(_exec(), fields={})
        assert res["ok"] is False
        assert res["action"] == "side_effect_failed"
        assert res["error"] == "boom"
        # no save after side-effect failure (saved count unchanged from before save)
        assert len(store._saved) == before

    def test_no_apply_side_effects_does_not_break_advance(self):
        graph = _make_graph_advance()
        state = WorkflowState(current_node="START", current_sub_state="A", sub_state_index=0)
        orch, store = _make_orchestrator(graph, state, side_effects=MockSideEffects())
        res = orch.advance(_exec(), fields={})
        assert res["ok"] is True


def _make_graph_submit():
    return WorkflowGraph(
        nodes={
            "START": WorkflowNode(
                node_id="START",
                sub_states=[SubState(key="INIT", label="Init", required_fields=[])],
            ),
        },
        edges=[],
        edges_by_source={},
        initial_state="START",
    )


def _make_graph_advance():
    return WorkflowGraph(
        nodes={
            "START": WorkflowNode(
                node_id="START",
                sub_states=[
                    SubState(key="A", label="A"),
                    SubState(key="B", label="B"),
                ],
            ),
        },
        edges=[],
        edges_by_source={},
        initial_state="START",
    )
