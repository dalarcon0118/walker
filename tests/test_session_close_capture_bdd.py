"""test_session_close_capture_bdd.py — el cierre captura conocimiento (P1.3).

La obligación de memoria existía pero colgaba del sub-estado KNOWLEDGE_UPDATE
del motor (`action: capture_knowledge`), que solo corre con `submit`. El agente
trabaja con tools y nunca hace submit: medido en la traza real, 227 `kg_query`
(lecturas) y 0 `capture_knowledge` (escrituras).

El cable: `session_close` —que el plugin SÍ invoca en cada cierre— deriva el
hecho de la sesión y lo persiste. Si la captura falla, el cierre se completa
igual y el error viaja en la respuesta (nunca silencio).
"""
from __future__ import annotations

import pytest

from fast_mcp.features.tasks.domain.task_registry import ExecutionContext
from fast_mcp.features.tasks.handlers import _di
from fast_mcp.features.tasks.handlers import knowledge as knowledge_handlers
from fast_mcp.features.tasks.handlers import session as session_handlers


class _FakeSprintService:
    def __init__(self):
        self.closed: list[tuple[str, str]] = []
        self.stamps: list[dict] = []

    def close_session(self, session_id, outcome="", stamp=None, cost_usd=None, tokens=None):
        self.closed.append((session_id, outcome))
        self.stamps.append(stamp or {})
        return {
            "session_id": session_id,
            "status": "closed",
            "outcome": outcome,
            "stamp": stamp or {},
            "cost_usd": cost_usd,
            "tokens": tokens or {},
        }


@pytest.fixture()
def fake_service(monkeypatch):
    service = _FakeSprintService()
    monkeypatch.setattr(_di, "_SPRINT_FACTORY", lambda chain_dir: service)
    return service


def _ctx(tmp_path, task_id="T-1"):
    return ExecutionContext.create(chain_dir=str(tmp_path), task_id=task_id)


def test_session_close_captura_y_reporta(tmp_path, fake_service, monkeypatch):
    captured: dict = {}

    def _spy(ctx, data):
        captured.update(data)
        from fast_mcp.features.tasks.domain.task_registry import TaskResult

        return TaskResult.success(data={"fact": "session-abc-T-1", "statement": "hecho"})

    monkeypatch.setattr(knowledge_handlers, "capture_knowledge", _spy)

    out = session_handlers.session_close(
        _ctx(tmp_path), {"session_id": "S-1", "outcome": "succeeded"}
    )

    assert out.ok
    assert out.data["session"]["status"] == "closed"
    assert out.data["knowledge"]["fact"] == "session-abc-T-1"
    assert captured == {"session_id": "S-1", "task_id": "T-1"}


def test_session_close_persiste_el_hecho(tmp_path, fake_service, monkeypatch):
    """Cadena real de capture_knowledge: spec + eventos → nodo de sesión."""
    monkeypatch.setattr(
        _di,
        "current_spec",
        lambda chain_dir, task_id="", exact=False: {
            "objective": "Debe capturar al cerrar. Sin romper los tests.",
            "files": ["a.py"],
            "task_context": {"task_id": "T-1", "session_id": "S-1"},
        },
    )
    monkeypatch.setattr(_di, "session_events", lambda chain_dir, session_id: [])

    saved: dict = {}

    def _save(chain_dir, nodes, task_id="", session_id=""):
        saved.update({"nodes": nodes, "task_id": task_id, "session_id": session_id})
        return {"ok": True}

    monkeypatch.setattr(_di, "save_knowledge", _save)

    out = session_handlers.session_close(_ctx(tmp_path), {"session_id": "S-1"})

    assert out.ok
    assert out.data["knowledge"]["fact"].startswith("session-")
    node = saved["nodes"][0]
    assert node["type"] == "session"
    assert node["metadata"]["session_id"] == "S-1"
    assert node["metadata"]["task_id"] == "T-1"
    assert "a.py" in node["metadata"]["files"]
    assert saved["session_id"] == "S-1"


def test_captura_fallida_no_rompe_el_cierre(tmp_path, fake_service, monkeypatch):
    monkeypatch.setattr(
        _di,
        "current_spec",
        lambda chain_dir, task_id="", exact=False: {
            "objective": "Debe capturar",
            "files": [],
            "task_context": {"task_id": "T-1", "session_id": "S-1"},
        },
    )
    monkeypatch.setattr(_di, "session_events", lambda chain_dir, session_id: [])
    monkeypatch.setattr(_di, "save_knowledge", lambda *a, **k: None)

    out = session_handlers.session_close(_ctx(tmp_path), {"session_id": "S-1"})

    assert out.ok, "el cierre no debe depender de la captura"
    assert out.data["knowledge"]["ok"] is False
    assert "writer" in out.data["knowledge"]["error"]
