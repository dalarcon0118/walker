"""test_skills_gate_bdd.py — gate de skills (policy: required).

El nodo QA de verify es `user_input`: el agente arma la evidencia y el usuario
decide. Si la fase declara `skill_intent.policy == "required"`, el submit se
bloquea hasta que el agente haya cargado un skill candidato — pero con una regla
anti-deadlock: si el entorno no tiene skills que matcheen, el gate pasa (no se
puede exigir cargar algo que no existe).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fast_mcp.features.workflow.application.orchestrator import OrchestrationService


class FakeTracer:
    def __init__(self, loaded: bool = True, raises: bool = False):
        self.loaded = loaded
        self.raises = raises
        self.calls: list[dict] = []

    def has_skill_load(self, chain_dir, task_id="", session_id="", skills=None, since=""):
        self.calls.append({"skills": set(skills or []), "since": since})
        if self.raises:
            raise RuntimeError("trace roto")
        return self.loaded


def _orchestrator(tmp_path, tracer):
    orch = object.__new__(OrchestrationService)
    orch._bundle = SimpleNamespace(chain_dir=str(tmp_path), tracer=tracer)
    return orch


def _state(updated_at: str = "2026-11-17T10:00:00+00:00"):
    return SimpleNamespace(updated_at=updated_at)


def _sub(policy: str = "required"):
    return SimpleNamespace(skill_intent={
        "needs": "revisión de código", "tags": ["review"], "policy": policy,
    })


def _ctx(tmp_path):
    return SimpleNamespace(chain_dir=str(tmp_path), task_id="T-1", session_id="s")


def test_policy_advisory_no_aplica(tmp_path):
    orch = _orchestrator(tmp_path, FakeTracer())

    assert orch._check_skill_gate(_ctx(tmp_path), _sub("advisory"), _state()) is None
    assert orch._check_skill_gate(_ctx(tmp_path), SimpleNamespace(skill_intent={}),
                                  _state()) is None


def test_sin_candidatos_pasa_con_nota(tmp_path, monkeypatch):
    orch = _orchestrator(tmp_path, FakeTracer(loaded=False))
    monkeypatch.setattr(orch, "_skill_candidates", lambda ctx, intent: [])

    out = orch._check_skill_gate(_ctx(tmp_path), _sub(), _state())

    assert out["ok"] is True
    assert "no aplica" in out["note"]


def test_con_candidatos_y_sin_carga_bloquea(tmp_path, monkeypatch):
    tracer = FakeTracer(loaded=False)
    orch = _orchestrator(tmp_path, tracer)
    monkeypatch.setattr(orch, "_skill_candidates", lambda ctx, intent: ["review-x"])

    out = orch._check_skill_gate(_ctx(tmp_path), _sub(), _state())

    assert out["ok"] is False
    assert out["blocked"] is True
    assert out["error_type"] == "required_skill_gate"
    assert "load_skill" in out["message"]
    assert "review-x" in out["message"]
    assert tracer.calls[0]["skills"] == {"review-x"}
    assert tracer.calls[0]["since"] == "2026-11-17T10:00:00+00:00"


def test_con_candidatos_y_carga_pasa(tmp_path, monkeypatch):
    orch = _orchestrator(tmp_path, FakeTracer(loaded=True))
    monkeypatch.setattr(orch, "_skill_candidates", lambda ctx, intent: ["review-x"])

    out = orch._check_skill_gate(_ctx(tmp_path), _sub(), _state())

    assert out["ok"] is True
    assert out["skills_checked"] == ["review-x"]


def test_tracer_roto_no_bloquea(tmp_path, monkeypatch):
    """Sin trace verificable, bloquear sería un deadlock."""
    orch = _orchestrator(tmp_path, FakeTracer(raises=True))
    monkeypatch.setattr(orch, "_skill_candidates", lambda ctx, intent: ["review-x"])

    assert orch._check_skill_gate(_ctx(tmp_path), _sub(), _state())["ok"] is True


def test_has_skill_load_filtra_por_skill_y_timestamp():
    from fast_mcp.features.event_tracing.infrastructure.cognitive_tracer import (
        CognitiveTracer,
    )

    events = [
        SimpleNamespace(event_type="skill_loaded", task_id="T-1",
                        timestamp="2026-11-17T09:00:00+00:00",
                        data={"skill": "viejo"}),
        SimpleNamespace(event_type="skill_loaded", task_id="T-1",
                        timestamp="2026-11-17T11:00:00+00:00",
                        data={"skill": "review-x"}),
    ]
    tracer = CognitiveTracer(SimpleNamespace(load_traces=lambda chain: events))

    assert tracer.has_skill_load("c", task_id="T-1", skills={"review-x"},
                                 since="2026-11-17T10:00:00+00:00") is True
    assert tracer.has_skill_load("c", task_id="T-1", skills={"otro"}) is False
    assert tracer.has_skill_load("c", skills={"review-x"},
                                 since="2026-11-17T12:00:00+00:00") is False


def test_load_skill_empuja_evento_al_trace(tmp_path, monkeypatch):
    from fast_mcp.memories.context import MemoryContext
    from fast_mcp.memories.handlers import knowledge as handlers

    root = tmp_path / "repo"
    (root / ".agents" / "skills" / "demo").mkdir(parents=True)
    (root / ".agents" / "skills" / "demo" / "SKILL.md").write_text(
        "---\nname: demo\ndescription: demo skill\n---\ncuerpo\n"
    )
    pushed: list[dict] = []

    class FakeService:
        def push_event(self, chain_dir, data):
            pushed.append(data)
            return {"ok": True}

    monkeypatch.setattr(
        "fast_mcp.features.event_tracing.application.services.trace_factory.build_trace_service",
        lambda chain_dir: FakeService(),
    )
    ctx = MemoryContext(chain_dir=str(root), project_root=str(root),
                        project_id="demo", data_dir="")

    handlers.handle_load_skill(ctx, name="demo")

    assert pushed and pushed[0]["event_type"] == "skill_loaded"
    assert pushed[0]["data"]["skill"] == "demo"
    assert pushed[0]["tool"] == "load_skill"
