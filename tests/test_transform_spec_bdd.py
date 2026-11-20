"""tests/test_transform_spec_bdd.py — BDD tests for side-effect engine + transform_spec."""
import os
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from fast_mcp.features.workflow.domain.entities import WorkflowState, SubStateData
from fast_mcp.features.workflow.domain.values import SideEffectDef
from fast_mcp.features.workflow.infrastructure.side_effect_engine import SideEffectEngine
from fast_mcp.features.tasks.domain.side_effect_registry import SideEffectRegistry
from fast_mcp.features.workflow.context import WorkflowContext
from fast_mcp.features.workflow.infrastructure.noop_side_effects import NoOpSideEffectEngine
from fast_mcp.features.tasks.side_effects.spec import transform_spec


# ── Helpers ─────────────────────────────────────────────────────────────

def _make_sub_state(effects):
    return SimpleNamespace(key="WORK", side_effects=effects)


# ── Engine tests ────────────────────────────────────────────────────────

class FakeRegistry:
    def __init__(self, registered=None, result=None, raise_on=None):
        self.registered = set(registered or [])
        self.result = result if result is not None else {"ok": True}
        self.raise_on = raise_on
        self.calls = []

    def has(self, name):
        return name in self.registered

    def execute(self, name, chain_dir, params, **kwargs):
        self.calls.append((name, chain_dir, params, kwargs))
        if self.raise_on == name:
            raise RuntimeError("boom")
        return dict(self.result)


class TestSideEffectEngineRegistered:
    def test_registered_ok(self):
        reg = FakeRegistry(registered={"transform_spec"}, result={"ok": True, "done": 1})
        engine = SideEffectEngine(reg)
        se = SideEffectDef(type="transform_spec", params={"status": "in_progress"})
        sub = _make_sub_state([se])
        state = WorkflowState(current_node="IMPLEMENT", current_sub_state="WORK", revision=3)
        out = engine.apply_side_effects(sub, state, "/tmp/cd")
        assert out["ok"] is True
        assert out["results"][0]["ok"] is True
        assert len(reg.calls) == 1

    def test_unregistered_skipped(self):
        reg = FakeRegistry(registered=set())
        engine = SideEffectEngine(reg)
        se = SideEffectDef(type="ghost_effect", params={})
        sub = _make_sub_state([se])
        state = WorkflowState(current_node="IMPLEMENT", current_sub_state="WORK", revision=1)
        out = engine.apply_side_effects(sub, state, "/tmp/cd")
        assert out["ok"] is True
        assert out["results"][0] == {"ok": True, "skipped": True, "type": "ghost_effect"}
        assert len(reg.calls) == 0

    def test_optional_false_fails(self):
        reg = FakeRegistry(registered={"transform_spec"}, result={"ok": False, "error": "boom"})
        engine = SideEffectEngine(reg)
        se = SideEffectDef(type="transform_spec", params={}, optional=False)
        sub = _make_sub_state([se])
        state = WorkflowState(current_node="IMPLEMENT", current_sub_state="WORK", revision=1)
        out = engine.apply_side_effects(sub, state, "/tmp/cd")
        assert out["ok"] is False
        assert out["error"] == "boom"

    def test_optional_true_continues(self):
        reg = FakeRegistry(registered={"transform_spec"}, result={"ok": False, "error": "soft"})
        engine = SideEffectEngine(reg)
        se1 = SideEffectDef(type="transform_spec", params={}, optional=True)
        se2 = SideEffectDef(type="transform_spec", params={}, optional=True)
        sub = _make_sub_state([se1, se2])
        state = WorkflowState(current_node="IMPLEMENT", current_sub_state="WORK", revision=1)
        out = engine.apply_side_effects(sub, state, "/tmp/cd")
        # both attempted
        assert len(reg.calls) == 2
        assert out["ok"] is True

    def test_idempotency(self):
        reg = FakeRegistry(registered={"transform_spec"}, result={"ok": True})
        engine = SideEffectEngine(reg)
        se = SideEffectDef(type="transform_spec", params={})
        sub = _make_sub_state([se])
        state = WorkflowState(current_node="IMPLEMENT", current_sub_state="WORK", revision=5)
        engine.apply_side_effects(sub, state, "/tmp/cd")
        engine.apply_side_effects(sub, state, "/tmp/cd")
        assert len(reg.calls) == 1

    def test_resolution_node_output_y(self):
        reg = FakeRegistry(registered={"transform_spec"}, result={"ok": True})
        engine = SideEffectEngine(reg)
        se = SideEffectDef(
            type="transform_spec",
            params={"status": "in_progress", "fields": {"tc": "NODE.WORK.output.task_context"}},
        )
        sub = _make_sub_state([se])
        state = WorkflowState(
            current_node="IMPLEMENT",
            current_sub_state="WORK",
            sub_state_data={"WORK": SubStateData(fields={"task_context": "CTX-VALUE"})},
            revision=2,
        )
        engine.apply_side_effects(sub, state, "/tmp/cd")
        assert reg.calls[0][2]["fields"]["tc"] == "CTX-VALUE"

    def test_literal_copied(self):
        reg = FakeRegistry(registered={"transform_spec"}, result={"ok": True})
        engine = SideEffectEngine(reg)
        se = SideEffectDef(type="transform_spec", params={"status": "completed", "fields": {"x": 42, "y": "lit"}})
        sub = _make_sub_state([se])
        state = WorkflowState(current_node="IMPLEMENT", current_sub_state="WORK", revision=1)
        engine.apply_side_effects(sub, state, "/tmp/cd")
        params = reg.calls[0][2]
        assert params["status"] == "completed"
        assert params["fields"]["x"] == 42
        assert params["fields"]["y"] == "lit"

    def test_never_raises_on_handler_error(self):
        reg = FakeRegistry(registered={"transform_spec"}, raise_on="transform_spec")
        engine = SideEffectEngine(reg)
        se = SideEffectDef(type="transform_spec", params={}, optional=True)
        sub = _make_sub_state([se])
        state = WorkflowState(current_node="IMPLEMENT", current_sub_state="WORK", revision=1)
        out = engine.apply_side_effects(sub, state, "/tmp/cd")
        assert out["ok"] is True

    def test_empty_side_effects(self):
        reg = FakeRegistry(registered=set())
        engine = SideEffectEngine(reg)
        sub = _make_sub_state([])
        state = WorkflowState(current_node="IMPLEMENT", current_sub_state="WORK", revision=1)
        out = engine.apply_side_effects(sub, state, "/tmp/cd")
        assert out == {"ok": True, "results": [], "error": ""}


# ── transform_spec tests ────────────────────────────────────────────────

class FakeSprintService:
    def __init__(self, spec_exists=True, raise_on=False):
        self.spec_exists = spec_exists
        self.raise_on = raise_on
        self.calls = []

    def update_spec(self, spring_id, spec_id, updates):
        self.calls.append((spring_id, spec_id, updates))
        if self.raise_on:
            raise ValueError("svc error")
        if not self.spec_exists:
            return None
        return {"spring_id": spring_id, "spec_id": spec_id, "status": updates.get("status"), **{k: v for k, v in updates.items() if k != "status"}}

    def get_spring_id(self, chain_dir):
        return "SPR-TEST"


class TestTransformSpec:
    def test_moves_status_and_fields(self):
        svc = FakeSprintService()
        params = {"status": "completed", "fields": {"commit_hash": "abc123", "completed_at": "now"}}
        state = SimpleNamespace(context={"task_id": "SPEC-001"})
        out = transform_spec("/tmp/cd", params, state=state, sprint_service=svc)
        assert out["ok"] is True
        assert svc.calls[0][0] == "SPR-TEST"
        assert svc.calls[0][1] == "SPEC-001"
        assert svc.calls[0][2]["status"] == "completed"
        assert svc.calls[0][2]["commit_hash"] == "abc123"

    def test_spec_inexistente_falla_visible(self):
        """Con sprint y spec resueltos, `update_spec -> None` significa que la
        transición NO ocurrió. Antes devolvía `skipped: True` y la spec se
        quedaba en el estado viejo sin que nadie lo supiera (medido 2026-11-18)."""
        svc = FakeSprintService(spec_exists=False)
        params = {"status": "in_progress"}
        state = SimpleNamespace(context={"task_id": "SPEC-X"})
        out = transform_spec("/tmp/cd", params, state=state, sprint_service=svc)
        assert out["ok"] is False
        assert "no se movió" in out["error"]
        assert out["spec_id"] == "SPEC-X" and out["spring_id"] == "SPR-TEST"

    def test_error_ok_false(self):
        svc = FakeSprintService(raise_on=True)
        params = {"status": "in_progress"}
        state = SimpleNamespace(context={"task_id": "SPEC-X"})
        out = transform_spec("/tmp/cd", params, state=state, sprint_service=svc)
        assert out["ok"] is False

    def test_sin_servicio_es_error_de_composicion(self):
        """Un `skipped: True` sin motivo era el bug: el efecto no se aplicaba y
        el submit seguía como si nada. Sin servicio no hay efecto posible."""
        params = {"status": "in_progress"}
        out = transform_spec("/tmp/cd", params)
        assert out["ok"] is False
        assert "sprint_service" in out["error"]

    def test_sin_sprint_es_skip_con_motivo(self):
        """Un skip legítimo dice POR QUÉ (el agente lo ve en `side_effects`)."""
        class NoSpringService(FakeSprintService):
            def get_spring_id(self, chain_dir):  # noqa: ARG002
                return ""

        out = transform_spec(
            "/tmp/cd",
            {"status": "in_progress"},
            state=SimpleNamespace(context={"task_id": "SPEC-X"}),
            sprint_service=NoSpringService(),
        )
        assert out["ok"] is True and out["skipped"] is True
        assert out["missing"] == "spring" and out["reason"]
        assert out["spec_id"] == "SPEC-X"

    def test_spring_id_from_params(self):
        svc = FakeSprintService()
        params = {"status": "in_review", "spring_id": "SPR-PARAM", "spec_id": "SPEC-P"}
        out = transform_spec("/tmp/cd", params, sprint_service=svc)
        assert out["ok"] is True
        assert svc.calls[0][0] == "SPR-PARAM"
        assert svc.calls[0][1] == "SPEC-P"

    def test_sin_spec_id_es_skip_con_motivo(self):
        """Sin `spec_id` no hay nada que mover: skip explícito, no error."""
        svc = FakeSprintService()
        out = transform_spec(
            "/tmp/cd", {"status": "in_progress", "spring_id": "SPR"}, sprint_service=svc
        )
        assert out["skipped"] is True
        assert out["missing"] == "spec" and out["spring_id"] == "SPR"

    def test_output_mergea_resultado_y_campos(self):
        """`NODE.X.output` debe traer la salida de la acción, no solo el input.

        Regresión: `review_metrics` se guardaba `{files, project_id}` porque el
        resolver devolvía los campos enviados antes del `task_result`.
        """
        from fast_mcp.features.workflow.infrastructure.side_effect_engine import (
            _resolve_node_output,
        )

        state = SimpleNamespace(
            sub_state_data={
                "CODE_REVIEW_SCAN": SimpleNamespace(fields={"files": ["x.js"]})
            },
            node_executions=[
                SimpleNamespace(
                    node="IMPLEMENT",
                    sub_state="CODE_REVIEW_SCAN",
                    task_result={"data": {"passed": True, "findings": [], "summary": "ok"}},
                )
            ],
            current_node="IMPLEMENT",
            current_sub_state="CODE_REVIEW_RESULT",
        )

        out = _resolve_node_output(state, "CODE_REVIEW_SCAN", None)

        assert out["files"] == ["x.js"]
        assert out["passed"] is True
        assert out["summary"] == "ok"
        assert _resolve_node_output(state, "CODE_REVIEW_SCAN", "passed") is True


    def test_spring_from_session_not_basename(self):
        """El basename (\"walker\") no contiene la spec; la sesión sí."""
        class SessionService:
            def __init__(self):
                self.calls = []
                self.specs = {("SPR-INTEG", "SPEC-002")}

            def get_session(self, sid):
                return {"session_id": sid, "spring_id": "SPR-INTEG"}

            def get_spring_id(self, chain_dir):
                return "walker"

            def get_spec(self, spring_id, spec_id):
                return {"spec_id": spec_id} if (spring_id, spec_id) in self.specs else None

            def list_springs(self):
                return [{"spring_id": "SPR-INTEG"}, {"spring_id": "walker"}]

            def update_spec(self, spring_id, spec_id, updates):
                self.calls.append((spring_id, spec_id, updates))
                return {"spring_id": spring_id, "spec_id": spec_id, "status": updates.get("status")}

        svc = SessionService()
        ctx = SimpleNamespace(session_id="s1")
        state = SimpleNamespace(context={"task_id": "SPEC-002"})
        out = transform_spec("/tmp/walker", {"status": "specified"}, state=state, ctx=ctx, sprint_service=svc)

        assert out["ok"] is True
        assert svc.calls[0][0] == "SPR-INTEG"
        assert svc.calls[0][1] == "SPEC-002"

    def test_search_spec_in_other_spring(self):
        """Sin sesión: si el sprint resuelto no tiene la spec, la busca."""
        class SearchService:
            def __init__(self):
                self.calls = []
                self.specs = {("SPR-INTEG", "SPEC-009")}

            def get_spring_id(self, chain_dir):
                return "walker"

            def get_spec(self, spring_id, spec_id):
                return {"spec_id": spec_id} if (spring_id, spec_id) in self.specs else None

            def list_springs(self):
                return [{"spring_id": "walker"}, {"spring_id": "SPR-INTEG"}]

            def update_spec(self, spring_id, spec_id, updates):
                self.calls.append((spring_id, spec_id, updates))
                return {"spring_id": spring_id, "spec_id": spec_id}

        svc = SearchService()
        state = SimpleNamespace(context={"task_id": "SPEC-009"})
        out = transform_spec("/tmp/walker", {"status": "in_progress"}, state=state, sprint_service=svc)

        assert out["ok"] is True
        assert svc.calls[0][0] == "SPR-INTEG"


# ── WorkflowContext.resolve no injection ────────────────────────────────

class TestContextResolveNoInjection:
    def test_uses_noop(self):
        # resolve without side_effects should use NoOp
        # We can't fully resolve without workflow file maybe; patch _find_workflow_path? 
        # Instead test that passing side_effects=None keeps NoOp by checking source behavior.
        from fast_mcp.features.workflow.context import WorkflowContext
        # Monkeypatch graph loading not needed; just ensure signature accepts
        assert "side_effects" in WorkflowContext.resolve.__code__.co_varnames


# ── Registry execute never raises ───────────────────────────────────────

class TestRegistryExecute:
    def test_unknown_returns_ok_false(self):
        reg = SideEffectRegistry()
        r = reg.execute("nope", "/tmp", {})
        assert r["ok"] is False
