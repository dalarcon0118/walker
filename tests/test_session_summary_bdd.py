from __future__ import annotations

from types import SimpleNamespace

import fast_mcp.memories.handlers.knowledge as knowledge
from fast_mcp.memories.handlers.cockpit import handle_session_summary


class _Ctx:
    chain_dir = "/tmp/proyecto"
    project_root = "/tmp/proyecto"
    project_id = "walker"


def _wire(monkeypatch, session_ctx=None, brief=None, plan_diff=None):
    session_ctx = session_ctx or {
        "session_id": "ses_x",
        "session": {"goal": "objetivo de prueba", "spring_id": "SPR-1"},
        "sprint": {"spring_id": "SPR-1", "goal": "sprint goal", "status": "active"},
        "tasks": [{"spec_id": "SPEC-1", "status": "completed", "objective": "hacer algo"}],
        "files": ["a.py"],
        "flight_state": {"writes": 3, "verified": True, "denials": {"total": 0}, "touched": ["a.py"]},
        "trace": {"count": 5, "types": ["tool_call", "session_metrics"]},
    }
    brief = brief or {
        "decisions": [{"id": "adr-1", "title": "t"}],
        "gates": {"next": "verificar"},
    }
    plan_diff = plan_diff or {"unverified": ["c"], "missing": []}
    monkeypatch.setattr(knowledge, "handle_session_context", lambda _ctx, **_: session_ctx)
    monkeypatch.setattr(knowledge, "handle_brief", lambda _ctx, **_: brief)
    monkeypatch.setattr(knowledge, "handle_plan_diff", lambda _ctx, **_: plan_diff)


def test_session_summary_texto_humano(monkeypatch):
    _wire(monkeypatch)
    out = handle_session_summary(_Ctx(), _payload={"session_id": "ses_x"})

    assert out["type"] == "session_summary"
    assert out["lines"] <= 30
    assert len(out["text"]) <= 1200
    assert "Resumen de sesión" in out["text"]
    assert "Objetivo:" in out["text"]
    assert "Archivos:" in out["text"]
    assert "Verificación:" in out["text"]
    assert "Decisiones:" in out["text"]
    assert "Próximo:" in out["text"]
    assert "Criterios sin predicado" in out["text"]
    assert "{" not in out["text"]


def test_session_summary_sin_session_id_degrada(monkeypatch):
    _wire(monkeypatch)
    out = handle_session_summary(_Ctx(), _payload={})

    assert out["degraded"] is True
    assert "session_id requerido" in out["text"]


def test_session_summary_declara_fuente_caida(monkeypatch):
    _wire(monkeypatch)
    monkeypatch.setattr(
        knowledge, "handle_brief", lambda _ctx, **_: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    out = handle_session_summary(_Ctx(), _payload={"session_id": "ses_x"})

    assert out["degraded"] is True
    assert any("brief" in m for m in out["missing"])


def test_session_summary_capa_30_lineas(monkeypatch):
    _wire(monkeypatch)
    out = handle_session_summary(_Ctx(), _payload={"session_id": "ses_x"})

    assert out["lines"] <= 30
    assert len(out["text"]) <= 1200
