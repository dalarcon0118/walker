from __future__ import annotations

from types import SimpleNamespace

import fast_mcp.memories.handlers.knowledge as knowledge
from fast_mcp.memories.handlers.cockpit import handle_what_changed


class _Ctx:
    chain_dir = "/tmp/proyecto"
    project_root = "/tmp/proyecto"
    project_id = "walker"


def _wire(monkeypatch, session_ctx=None, plan_diff=None):
    session_ctx = session_ctx or {
        "session_id": "ses_x",
        "session": {"goal": "g"},
        "sprint": {"spring_id": "SPR-1", "goal": "sprint goal", "status": "active"},
        "tasks": [{"spec_id": "SPEC-1", "status": "completed"}],
        "files": ["a.py", "b.py"],
        "flight_state": {"writes": 2, "verified": True, "denials": {"total": 0}},
    }
    plan_diff = plan_diff or {
        "unverified": ["criterio sin predicado"],
        "scope_expanded": ["x.py"],
        "spec_files_missing_from_plan": [],
        "missing": [],
    }
    monkeypatch.setattr(
        knowledge, "handle_session_context", lambda _ctx, **_: session_ctx
    )
    monkeypatch.setattr(knowledge, "handle_plan_diff", lambda _ctx, **_: plan_diff)


def test_what_changed_texto_humano(monkeypatch):
    _wire(monkeypatch)
    out = handle_what_changed(_Ctx(), _payload={"session_id": "ses_x"})

    assert out["type"] == "what_changed"
    assert out["lines"] <= 30
    assert len(out["text"]) <= 1200
    assert "Cambios en la sesión" in out["text"]
    assert "Archivos tocados: 2" in out["text"]
    assert "a.py" in out["text"]
    assert "Criterios sin predicado" in out["text"]
    assert "Archivos fuera del alcance" in out["text"]
    assert "{" not in out["text"]


def test_what_changed_sin_session_id_degrada(monkeypatch):
    _wire(monkeypatch)
    out = handle_what_changed(_Ctx(), _payload={})

    assert out["degraded"] is True
    assert "session_id requerido" in out["text"]


def test_what_changed_declara_fuente_caida(monkeypatch):
    _wire(monkeypatch)
    monkeypatch.setattr(
        knowledge, "handle_session_context", lambda _ctx, **_: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    out = handle_what_changed(_Ctx(), _payload={"session_id": "ses_x"})

    assert out["degraded"] is True
    assert any("session_context" in m for m in out["missing"])


def test_what_changed_capa_30_lineas(monkeypatch):
    _wire(monkeypatch, session_ctx={
        "session_id": "ses_x",
        "session": {"goal": "g"},
        "sprint": {"spring_id": "SPR-1", "goal": "g", "status": "active"},
        "tasks": [{"spec_id": f"SPEC-{i}", "status": "completed"} for i in range(20)],
        "files": [f"f{i}.py" for i in range(20)],
        "flight_state": {"writes": 2, "verified": True, "denials": {"total": 0}},
    })
    out = handle_what_changed(_Ctx(), _payload={"session_id": "ses_x"})

    assert out["lines"] <= 30
    assert len(out["text"]) <= 1200
