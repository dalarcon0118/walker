"""test_cockpit_bdd.py — Cockpit del desarrollador (SPR-COCKPIT SPEC-001).

Contrato: una vista única (≤30 líneas, ≤1200 chars) que compone brief +
session_context + plan_diff en texto humano, sin JSON. Si una fuente cae, la
sección se omite y se declara en `missing`.
"""
from __future__ import annotations

from types import SimpleNamespace

from returns.result import Success

import fast_mcp.composition as composition
import fast_mcp.memories.handlers.knowledge as knowledge
from fast_mcp.features.cognitive_knowledge.application.services.ckg_read import (
    CkgReadService,
)
from fast_mcp.memories.handlers.cockpit import handle_cockpit


class _Ctx:
    chain_dir = "/tmp/proyecto"
    project_root = "/tmp/proyecto"
    project_id = "walker"


def _plan(**over):
    plan = {"task": "SPEC-001", "acceptance": ["a"], "scope": ["a.py"],
            "next": "verificar", "approved_by_user": True}
    plan.update(over)
    return plan


def _service(plan=None):
    plan = plan or _plan()
    spec = {"spec_id": "SPEC-001", "task_context": {"task_id": "SPEC-001"},
            "acceptance_criteria": ["a"], "files": ["a.py"],
            "status": "in_progress", "predicates": {}}
    return SimpleNamespace(
        get_session=lambda _sid: {"spring_id": "SPR-1", "flight_plan": plan},
        get_session_plan=lambda _sid: plan,
        get_specs=lambda _spr: [spec],
        list_sessions=lambda **_kw: [],
    )


def _wire(monkeypatch, events=None, plan=None):
    events = events or []
    plan = plan or _plan()
    knowledge._IMPACT_CACHE.clear()
    knowledge._SELF_CACHE.clear()
    monkeypatch.setattr(
        knowledge,
        "_resolve_anchor",
        lambda *_: ({"type": "file", "value": "a.py", "resolved": True}, ["a.py"], None),
    )
    monkeypatch.setattr(
        knowledge,
        "handle_preflight",
        lambda _ctx, **_: {"files": [{"file": "a.py", "callers": 5, "heaviest": []}],
                           "tests": ["tests/test_x.py"], "unmapped": []},
    )
    monkeypatch.setattr(CkgReadService, "gather_context",
                        staticmethod(lambda *_a, **_k: Success([])))
    monkeypatch.setattr(composition, "build_sprint_service",
                        lambda _d: _service(plan))
    monkeypatch.setattr(composition, "workflow_tool_runner",
                        lambda _n: (lambda **_kw: {"current_position": "IMPLEMENT.WORK"}))

    # session_context mock: build a fake trace service
    from fast_mcp.features.event_tracing.application.services import trace_factory

    class _Trace:
        def load_traces(self, _chain_dir):
            return list(events)

    monkeypatch.setattr(trace_factory, "build_trace_service", lambda _d: _Trace())


def test_cockpit_devuelve_texto_humano(monkeypatch):
    _wire(monkeypatch)
    out = handle_cockpit(_Ctx(), _payload={"session_id": "ses_x", "file": "a.py"})

    assert out["type"] == "cockpit"
    assert isinstance(out["text"], str)
    assert out["lines"] <= 30
    assert len(out["text"]) <= 1200
    assert "━━━" in out["text"]
    assert "Tarea:" in out["text"]
    assert "Paso:" in out["text"]
    assert "Verificación:" in out["text"]


def test_cockpit_no_incluye_json(monkeypatch):
    _wire(monkeypatch)
    out = handle_cockpit(_Ctx(), _payload={"session_id": "ses_x", "file": "a.py"})

    text = out["text"]
    assert "{" not in text and "}" not in text, "el cockpit es texto humano, no JSON"


def test_cockpit_declara_fuente_caida(monkeypatch):
    _wire(monkeypatch)
    # trace service que falla
    from fast_mcp.features.event_tracing.application.services import trace_factory

    monkeypatch.setattr(trace_factory, "build_trace_service", lambda _d: (_ for _ in ()).throw(RuntimeError("boom")))

    out = handle_cockpit(_Ctx(), _payload={"session_id": "ses_x", "file": "a.py"})

    assert out["degraded"] is True
    assert any("session_context" in m for m in out["missing"])


def test_cockpit_sin_session_id_degrada(monkeypatch):
    _wire(monkeypatch)
    out = handle_cockpit(_Ctx(), _payload={"file": "a.py"})

    assert out["degraded"] is True
    assert any("session_context" in m for m in out["missing"])


def test_cockpit_capa_30_lineas(monkeypatch):
    _wire(monkeypatch)
    out = handle_cockpit(_Ctx(), _payload={"session_id": "ses_x", "file": "a.py"})

    assert out["lines"] <= 30
    assert len(out["text"]) <= 1200