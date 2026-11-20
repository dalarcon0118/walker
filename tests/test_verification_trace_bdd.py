"""test_verification_trace_bdd.py — la verificación queda en el TRACE (P1.11).

El plugin certifica el cierre con lo que observa de los comandos. Cuando la
tool se invoca por fuera de su wrapper (Code Mode, shell del host, subagente),
el `run_tests` verde nunca llega al hook y el gate vuelve a pedir verificación
aunque ya exista.

La tool registra su resultado como `session_state` en el trace; el plugin lo
hidrata desde `session_context.flight_state` antes de nudgar. Fail-closed: sin
`session_id` real no se escribe (marcaría como verde a cualquier sesión).
"""
from __future__ import annotations

from fast_mcp.features.tasks.domain.task_registry import ExecutionContext
from fast_mcp.features.tasks.handlers import _di
from fast_mcp.features.tasks.handlers import implement as impl
from fast_mcp.features.tasks.handlers import verify as verify_handlers
from fast_mcp.features.tasks.infrastructure import verify_engine as ve


class _Result:
    def __init__(self, rc: int, out: str = "") -> None:
        self.returncode = rc
        self.stdout = out
        self.stderr = ""


def _ctx(chain_dir: str, session_id: str = "S-1") -> ExecutionContext:
    return ExecutionContext.create(
        chain_dir=chain_dir, session_id=session_id, task_id="T-1"
    )


def _spy(monkeypatch) -> list[dict]:
    events: list[dict] = []
    monkeypatch.setattr(_di, "push_trace", lambda chain, ev: events.append(ev) or True)
    return events


def _pytest_project(tmp_path, monkeypatch):
    monkeypatch.setattr(impl, "_resolve_project_root", lambda d: str(tmp_path))
    monkeypatch.setattr(ve, "_read_declared_framework", lambda root: "pytest")


def test_run_tests_verde_registra_session_state(tmp_path, monkeypatch):
    events = _spy(monkeypatch)
    _pytest_project(tmp_path, monkeypatch)
    monkeypatch.setattr(impl.subprocess, "run", lambda *a, **k: _Result(0, "3 passed"))

    out = impl.run_tests(_ctx(str(tmp_path)), {"session_id": "S-1", "task_id": "T-1"})

    assert out.ok
    event = events[-1]
    assert event["event_type"] == "session_state"
    assert event["session_id"] == "S-1"
    assert event["source"] == "walker_task"
    assert event["task_id"] == "T-1"
    data = event["data"]
    assert data["verified"] is True
    assert data["verify_level"] == "tool"
    assert data["runner"] == "python3 -m pytest"
    assert data["returncode"] == 0
    assert data["verified_at"], "el plugin compara verified_at con dirty_since"


def test_run_tests_rojo_registra_el_fallo(tmp_path, monkeypatch):
    events = _spy(monkeypatch)
    _pytest_project(tmp_path, monkeypatch)
    monkeypatch.setattr(
        impl.subprocess, "run", lambda *a, **k: _Result(1, "1 failed, 2 passed")
    )

    out = impl.run_tests(_ctx(str(tmp_path)), {"session_id": "S-1"})

    assert out.ok is False
    data = events[-1]["data"]
    assert data["verified"] is False and data["verify_failed"] is True
    assert data["returncode"] == 1


def test_sin_sesion_real_no_escribe(tmp_path, monkeypatch):
    """`mcp` es el canal: un evento así marcaría verde a cualquier sesión."""
    events = _spy(monkeypatch)
    _pytest_project(tmp_path, monkeypatch)
    monkeypatch.setattr(impl.subprocess, "run", lambda *a, **k: _Result(0, "2 passed"))

    out = impl.run_tests(_ctx(str(tmp_path), session_id="mcp"), {})

    assert out.ok, "la verificación no depende del trace"
    assert events == []


def test_verify_predicates_registra_predicates(tmp_path, monkeypatch):
    events = _spy(monkeypatch)
    (tmp_path / "a.txt").write_text("x")

    out = verify_handlers.verify_predicates(
        _ctx(str(tmp_path)),
        {
            "session_id": "S-1",
            "task_id": "T-1",
            "project_root": str(tmp_path),
            "predicates": [{"type": "file_exists", "path": "a.txt"}],
        },
    )

    assert out.ok and out.data["all_satisfied"] is True
    data = events[-1]["data"]
    assert data["verified"] is True and data["verify_level"] == "predicates"
    assert events[-1]["session_id"] == "S-1"


def test_verify_predicates_rojo_tambien_registra(tmp_path, monkeypatch):
    events = _spy(monkeypatch)

    out = verify_handlers.verify_predicates(
        _ctx(str(tmp_path)),
        {
            "session_id": "S-1",
            "project_root": str(tmp_path),
            "predicates": [{"type": "file_exists", "path": "no-existe.txt"}],
        },
    )

    assert out.data["all_satisfied"] is False
    assert events[-1]["data"]["verified"] is False


def test_evento_cubre_lo_que_proyecta_session_context(monkeypatch):
    """`session_context.flight_state` proyecta estas claves del último evento."""
    from fast_mcp.features.tasks.handlers.verification import record_verification

    events = _spy(monkeypatch)
    record_verification(
        _ctx("/tmp", "S-1"), session_id="S-1", passed=True, level="tool",
        runner="r", returncode=0, task_id="T-1",
    )

    data = events[0]["data"]
    for key in ("verified", "verified_at", "verify_level", "dirty_since"):
        assert key in data, f"falta {key}: el flight_state no lo puede proyectar"


def test_push_trace_sin_writer_no_rompe(monkeypatch):
    monkeypatch.setattr(_di, "_TRACE_WRITER", None)
    assert _di.push_trace("/tmp", {"event_type": "session_state"}) is False
