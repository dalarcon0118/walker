"""test_session_reflect_bdd.py — F2: consolidación al cerrar la sesión.

Contrato:
  - `session_close` invoca `session_reflect` y reporta el resultado en
    `data.reflection`.
  - Si no hay task_ledger previo, se construye desde el trace y se persiste.
  - Si ya existe, se salta (idempotente).
  - Un fallo de reflexión no rompe el cierre.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from fast_mcp.features.tasks.domain.task_registry import ExecutionContext
from fast_mcp.features.tasks.handlers import _di
from fast_mcp.features.tasks.handlers import session as session_handlers


class _FakeSprintService:
    def __init__(self):
        self.closed: list[tuple[str, str]] = []

    def close_session(self, session_id, outcome="", stamp=None, cost_usd=None, tokens=None):
        self.closed.append((session_id, outcome))
        return {
            "session_id": session_id,
            "status": "closed",
            "outcome": outcome,
            "stamp": stamp or {},
        }

    def get_session(self, session_id):
        return {"session_id": session_id, "spring_id": "SPR-1", "flight_plan": {"task": "T-1"}}

    def get_specs(self, spring_id):
        return [{"spec_id": "SPEC-1", "task_context": {"task_id": "T-1"}, "files": ["a.py"],
                 "acceptance_criteria": ["a"], "predicates": {}}]

    def list_sessions(self, project_id=""):
        return []


@pytest.fixture()
def project_dir(tmp_path: Path) -> str:
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "project.json").write_text(
        json.dumps({"project": {"meta": {"project_id": "reflect-bdd"}}})
    )
    return str(tmp_path)


@pytest.fixture()
def fake_service(monkeypatch):
    service = _FakeSprintService()
    monkeypatch.setattr(_di, "_SPRINT_FACTORY", lambda chain_dir: service)
    return service


def _trace_event(event_type: str, data: dict | None = None, session_id: str = "S-1"):
    return SimpleNamespace(
        event_type=event_type,
        data=data or {},
        metadata={"session_id": session_id},
        files=[],
        task_id="",
        timestamp="2026-11-22T00:00:00+00:00",
    )


def test_session_close_refleja_y_reporta(project_dir, fake_service, monkeypatch):
    events = [
        _trace_event("session_state", {"writes": 2, "verified": True, "touched": ["a.py"]}),
    ]

    class _Trace:
        def load_traces(self, _chain_dir):
            return list(events)

    from fast_mcp.features.event_tracing.application.services import trace_factory
    monkeypatch.setattr(trace_factory, "build_trace_service", lambda _d: _Trace())

    out = session_handlers.session_close(
        ExecutionContext.create(chain_dir=project_dir, task_id="T-1", session_id="S-1"),
        {"session_id": "S-1", "outcome": "succeeded", "task_id": "T-1"},
    )

    assert out.ok is True
    assert out.data["session"]["status"] == "closed"
    assert out.data["reflection"]["saved"] is True
    assert out.data["reflection"]["node_id"] == "task_ledger-S-1-T-1"


def test_session_close_reflection_idempotente(project_dir, fake_service, monkeypatch):
    events = [_trace_event("session_state", {"writes": 1, "verified": True, "touched": ["a.py"]})]

    class _Trace:
        def load_traces(self, _chain_dir):
            return list(events)

    from fast_mcp.features.event_tracing.application.services import trace_factory
    monkeypatch.setattr(trace_factory, "build_trace_service", lambda _d: _Trace())

    ctx = ExecutionContext.create(chain_dir=project_dir, task_id="T-1", session_id="S-1")
    session_handlers.session_close(ctx, {"session_id": "S-1", "outcome": "succeeded", "task_id": "T-1"})
    out2 = session_handlers.session_close(ctx, {"session_id": "S-1", "outcome": "succeeded", "task_id": "T-1"})

    assert out2.ok is True
    assert out2.data["reflection"].get("skipped") is True


def test_session_close_no_rompe_si_reflection_falla(project_dir, fake_service, monkeypatch):
    from fast_mcp.features.event_tracing.application.services import trace_factory
    monkeypatch.setattr(trace_factory, "build_trace_service", lambda _d: (_ for _ in ()).throw(RuntimeError("boom")))

    out = session_handlers.session_close(
        ExecutionContext.create(chain_dir=project_dir, task_id="T-1", session_id="S-1"),
        {"session_id": "S-1", "outcome": "succeeded", "task_id": "T-1"},
    )

    assert out.ok is True
    assert out.data["session"]["status"] == "closed"
    # La reflexión no rompe el cierre: si el trace falla, se salta (ok=True,
    # skipped) o falla (ok=False); en ambos casos el cierre queda verde.
    assert out.data["reflection"].get("ok") is True or out.data["reflection"].get("skipped") is True