"""test_capture_side_effects_bdd.py — captura acoplada a las tools (P1.3).

El motor ejecuta `capture_knowledge` en KNOWLEDGE_UPDATE, pero esa rama solo
corre con `submit`. El agente usa `run_tests`, `spec_status` y `session_close`:
ahí es donde la obligación tiene que viajar. Medido: 227 `kg_query` y 0
`capture_knowledge` en la traza.

Contrato: la captura es un efecto secundario VISIBLE (viaja en `data.knowledge`)
y nunca convierte una tool verde en roja.
"""
from __future__ import annotations

import json
import types
from pathlib import Path

import pytest

from fast_mcp.features.tasks.domain.task_registry import ExecutionContext
from fast_mcp.features.tasks.handlers import _di
from fast_mcp.features.tasks.handlers import implement, knowledge, spec_status


@pytest.fixture()
def project_dir(tmp_path: Path) -> str:
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "project.json").write_text(
        json.dumps(
            {
                "project": {
                    "meta": {"project_id": "cap-bdd"},
                    "location": {"repo_path": str(tmp_path)},
                    # El runner sale de acá: sin declaración, run_tests falla
                    # a propósito (no se asume pytest).
                    "coding": {"test_framework": "pytest"},
                }
            }
        )
    )
    (tmp_path / "tests").mkdir()
    return str(tmp_path)


def _ctx(project_dir: str, task_id: str = "T-1"):
    return ExecutionContext.create(chain_dir=project_dir, task_id=task_id)


def _spy(monkeypatch, result=None):
    calls: list[dict] = []
    payload = result or {"ok": True, "fact": "session-x-T-1"}

    def _fake(ctx, *, session_id="", task_id="", spring_id=""):
        calls.append(
            {"session_id": session_id, "task_id": task_id, "spring_id": spring_id}
        )
        return dict(payload)

    monkeypatch.setattr(knowledge, "capture_session_fact", _fake)
    return calls


def test_run_tests_verde_captura(project_dir, monkeypatch):
    monkeypatch.setattr(
        implement.subprocess,
        "run",
        lambda *a, **k: types.SimpleNamespace(
            returncode=0, stdout="10 passed in 0.2s", stderr=""
        ),
    )
    calls = _spy(monkeypatch)

    out = implement.run_tests(
        _ctx(project_dir), {"session_id": "S-1", "task_id": "T-1"}
    )

    assert out.ok and out.data["passed"] is True
    assert out.data["knowledge"]["fact"] == "session-x-T-1"
    assert calls == [{"session_id": "S-1", "task_id": "T-1", "spring_id": ""}]


def test_run_tests_rojo_no_captura(project_dir, monkeypatch):
    monkeypatch.setattr(
        implement.subprocess,
        "run",
        lambda *a, **k: types.SimpleNamespace(
            returncode=1, stdout="9 passed, 1 failed in 0.2s", stderr=""
        ),
    )
    calls = _spy(monkeypatch)

    out = implement.run_tests(_ctx(project_dir), {"session_id": "S-1"})

    assert not out.ok
    assert calls == [], "una suite roja no documenta un cierre"


def test_helper_no_pasa_el_placeholder_mcp(tmp_path, monkeypatch):
    """`mcp` es el canal, no la sesión: debe caer a la sesión de la spec."""
    seen: dict = {}

    def _fake(ctx, data):
        seen.update(data)
        from fast_mcp.features.tasks.domain.task_registry import TaskResult

        return TaskResult.success(data={"fact": "x"})

    monkeypatch.setattr(knowledge, "capture_knowledge", _fake)

    knowledge.capture_session_fact(
        ExecutionContext.create(chain_dir=str(tmp_path), task_id="T-1"),
        session_id="mcp",
        task_id="T-1",
    )

    assert seen["session_id"] == ""


class _FakeService:
    def __init__(self, status="completed"):
        self.status = status
        self.updated: dict = {}

    def get_session(self, session_id):
        return {"session_id": session_id, "spring_id": "SPR-1"}

    def get_spec(self, spring_id, spec_id):
        return {"spec_id": spec_id, "spring_id": spring_id,
                "task_context": {"task_id": "T-1"}, "status": "in_progress"}

    def update_spec_status(self, spring_id, spec_id, status, artifacts=None):
        self.updated = {"spring_id": spring_id, "spec_id": spec_id,
                        "status": status, "artifacts": artifacts or {}}
        return {"spec_id": spec_id, "status": status,
                "task_context": {"task_id": "T-1"}}


def test_spec_status_completed_captura(project_dir, monkeypatch):
    service = _FakeService()
    monkeypatch.setattr(_di, "_SPRINT_FACTORY", lambda chain_dir: service)
    calls = _spy(monkeypatch)

    out = spec_status.spec_status(
        _ctx(project_dir),
        {"spec_id": "SPEC-001", "status": "completed", "session_id": "S-1",
         "commit_hash": "abc", "evidence": "suite verde",
         "review_verdict": "ok", "completed_at": "2026-11-19T00:00:00Z"},
    )

    assert out.ok
    assert out.data["knowledge"]["fact"] == "session-x-T-1"
    assert calls == [{"session_id": "S-1", "task_id": "T-1", "spring_id": "SPR-1"}]


def test_spec_status_in_progress_no_captura(project_dir, monkeypatch):
    service = _FakeService(status="in_progress")
    monkeypatch.setattr(_di, "_SPRINT_FACTORY", lambda chain_dir: service)
    calls = _spy(monkeypatch)

    out = spec_status.spec_status(
        _ctx(project_dir), {"spec_id": "SPEC-001", "status": "in_progress"}
    )

    assert out.ok
    assert "knowledge" not in out.data
    assert calls == []


# ── Alcance de sprint en la resolución de identidad (bug 2026-11-20) ──


def _two_springs():
    class _Service:
        def get_active_spring(self):
            return {"spring_id": "S1"}

        def list_springs(self):
            return [{"spring_id": "S1"}, {"spring_id": "S2"}]

        def get_specs(self, spring_id):
            return [{"spec_id": "SPEC-012", "spring_id": spring_id,
                     "task_context": {}, "status": "in_progress"}]

    return _Service()


def test_current_spec_no_elige_entre_dos_sprints(monkeypatch):
    """`SPEC-012` existe en dos sprints: sin alcance no se elige el primero."""
    monkeypatch.setattr(_di, "_SPRINT_FACTORY", lambda _d: _two_springs())

    assert _di.current_spec("x", task_id="SPEC-012", exact=True) is None
    scoped = _di.current_spec("x", task_id="SPEC-012", exact=True, spring_id="S2")
    assert scoped["spring_id"] == "S2"


def test_captura_rechaza_un_spec_id_sin_alcance(project_dir, monkeypatch):
    monkeypatch.setattr(_di, "session_spring", lambda *_a, **_k: "")

    out = knowledge.capture_knowledge(
        _ctx(project_dir), {"task_id": "SPEC-012", "session_id": ""}
    )

    assert not out.ok
    assert "local a un sprint" in out.error


def test_fact_for_pasa_el_sprint_a_la_resolucion(project_dir, monkeypatch):
    seen: dict = {}

    def _current(chain_dir, task_id="", prefer_status=("in_progress",),
                 exact=False, spring_id=""):
        seen.update(task_id=task_id, spring_id=spring_id)
        return {"spec_id": "SPEC-012", "objective": "x", "files": [],
                "task_context": {}}

    monkeypatch.setattr(_di, "current_spec", _current)
    monkeypatch.setattr(_di, "session_spring", lambda *_a, **_k: "S2")

    fact = knowledge._fact_for(
        _ctx(project_dir), {"task_id": "SPEC-012", "session_id": "s1"}
    )

    assert fact is not None
    assert seen == {"task_id": "SPEC-012", "spring_id": "S2"}


def test_captura_sin_sesion_falla_en_vez_de_crear_un_nodo_huerfano(
    project_dir, monkeypatch
):
    """`session-sin-id-…` no se puede encontrar por session_context ni podar."""
    monkeypatch.setattr(
        _di,
        "current_spec",
        lambda *a, **k: {"spec_id": "SPEC-005", "objective": "x", "files": [],
                         "task_context": {}},
    )
    monkeypatch.setattr(_di, "session_events", lambda *a, **k: [])
    monkeypatch.setattr(_di, "session_spring", lambda *a, **k: "")
    monkeypatch.setattr(_di, "sprint_service", lambda *a, **k: None)

    out = knowledge.capture_knowledge(
        _ctx(project_dir), {"task_id": "SPEC-005", "spring_id": "SPR-1"}
    )

    assert not out.ok
    assert "no tiene sesión" in out.error


def test_spec_status_sin_sesion_reporta_la_captura_no_ligada(project_dir, monkeypatch):
    """El cierre administrativo sin sesión no crea un nodo huérfano: lo dice."""
    service = _FakeService()
    monkeypatch.setattr(_di, "_SPRINT_FACTORY", lambda chain_dir: service)
    monkeypatch.setattr(
        _di, "current_spec",
        lambda *a, **k: {"spec_id": "SPEC-001", "objective": "x", "files": [],
                         "task_context": {}},
    )
    monkeypatch.setattr(_di, "session_events", lambda *a, **k: [])

    out = spec_status.spec_status(
        _ctx(project_dir),
        {"spec_id": "SPEC-001", "status": "completed", "spring_id": "SPR-1",
         "commit_hash": "abc", "evidence": ["suite verde"],
         "review_verdict": "ok", "completed_at": "2026-11-20T00:00:00Z"},
    )

    assert out.ok, "la spec se completa igual"
    assert out.data["knowledge"]["ok"] is False
    assert "sesión" in out.data["knowledge"]["error"]
