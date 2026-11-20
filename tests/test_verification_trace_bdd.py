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


# ── evidencia de escritura sin hook (P1.12) ────────────────────────


def test_scan_detecta_cambios_desde_el_ultimo_scan(tmp_path, monkeypatch):
    from fast_mcp.shared_kernel.project_support.watcher import changed_since_last_scan

    (tmp_path / "a.py").write_text("x = 1\n")
    assert changed_since_last_scan(str(tmp_path)) == [], "el primer scan es baseline"

    (tmp_path / "a.py").write_text("x = 2\n")
    (tmp_path / "b.py").write_text("y = 2\n")

    changed = changed_since_last_scan(str(tmp_path))
    assert "a.py" in changed and "b.py" in changed


def test_run_tests_registra_la_escritura_que_walker_ve(tmp_path, monkeypatch):
    events = _spy(monkeypatch)
    _pytest_project(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "fast_mcp.features.tasks.handlers.verification.changed_since_last_scan",
        lambda root, session_id="": ["src/a.py", "src/b.py"],
    )
    monkeypatch.setattr(impl.subprocess, "run", lambda *a, **k: _Result(0, "2 passed"))

    out = impl.run_tests(_ctx(str(tmp_path)), {"session_id": "S-1"})

    assert out.ok
    assert out.data["wrote"] is True and out.data["touched"] == ["src/a.py", "src/b.py"]
    data = events[-1]["data"]
    assert data["wrote"] is True and data["writes"] == 2
    assert data["touched"] == ["src/a.py", "src/b.py"]
    assert data["touched_source"] == "watcher"


def test_sin_cambios_no_se_marca_wrote(tmp_path, monkeypatch):
    events = _spy(monkeypatch)
    _pytest_project(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "fast_mcp.features.tasks.handlers.verification.changed_since_last_scan",
        lambda root, session_id="": [],
    )
    monkeypatch.setattr(impl.subprocess, "run", lambda *a, **k: _Result(0, "1 passed"))

    impl.run_tests(_ctx(str(tmp_path)), {"session_id": "S-1"})

    assert "wrote" not in events[-1]["data"], "sin evidencia no se inventa escritura"


def test_session_context_completa_wrote_desde_el_scan(monkeypatch):
    from fast_mcp.memories.handlers.knowledge import merge_write_evidence

    flight = merge_write_evidence(
        {"verified": True},
        "/proyecto",
        scanner=lambda root, session_id="": ["x.py", "y.py"],
    )
    assert flight["wrote"] is True and flight["writes"] == 2
    assert flight["touched"] == ["x.py", "y.py"]
    assert flight["touched_source"] == "watcher"

    # Lo observado por el plugin manda: no se pisa.
    observed = {"wrote": True, "writes": 1, "touched": ["solo.py"]}
    assert (
        merge_write_evidence(
            observed, "/proyecto", scanner=lambda r, session_id="": ["x.py"]
        )
        == observed
    )

    # Sin cambios (o scan roto) no se inventa nada.
    assert (
        merge_write_evidence({}, "/proyecto", scanner=lambda r, session_id="": []) == {}
    )


def test_scan_ignora_el_estado_de_walker(tmp_path):
    """El diff no debe contar el propio estado del motor como escritura.

    Medido en el harness: `idem` y `shellmut` pasaron a "mutaciones" por
    `.workflow-state.json` y `data/cognitive.sqlite3`.
    """
    from fast_mcp.shared_kernel.project_support.watcher import changed_since_last_scan

    (tmp_path / "pyproject.toml").write_text("[project]\n")
    assert changed_since_last_scan(str(tmp_path)) == []

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n")
    (tmp_path / ".workflow-state.json").write_text("{}")
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data" / "cognitive.sqlite3").write_text("")
    # WAL/SHM del store: los escribe el motor. Medido: `touched` los incluía.
    (tmp_path / "data" / "cognitive.sqlite3-wal").write_text("")
    (tmp_path / "data" / "cognitive.sqlite3-shm").write_text("")

    assert changed_since_last_scan(str(tmp_path)) == ["src/a.py"]


def test_el_snapshot_es_por_sesion_no_del_proyecto(tmp_path):
    """P1.13: el snapshot global arrastra escrituras de sesiones previas.

    Medido: una sesión nueva veía `wrote` por los cambios de la anterior. El
    baseline por sesión se establece en la primera lectura y sólo cuenta lo
    que cambió DESPUÉS.
    """
    from fast_mcp.shared_kernel.project_support.watcher import changed_since_last_scan

    (tmp_path / "pyproject.toml").write_text("[project]\n")
    (tmp_path / "a.py").write_text("x = 1\n")

    # Sesión A: establece baseline y escribe.
    assert changed_since_last_scan(str(tmp_path), session_id="S-A") == []
    (tmp_path / "b.py").write_text("y = 1\n")
    assert changed_since_last_scan(str(tmp_path), session_id="S-A") == ["b.py"]

    # Sesión B (nueva): no hereda b.py. Su baseline es AHORA.
    assert changed_since_last_scan(str(tmp_path), session_id="S-B") == []
    (tmp_path / "c.py").write_text("z = 1\n")
    assert changed_since_last_scan(str(tmp_path), session_id="S-B") == ["c.py"]
    # Límite conocido: el snapshot se refresca en cada lectura (igual que el
    # global), así que una escritura POSTERIOR de otra sesión sí se ve. Lo que
    # se corrige es heredar las escrituras ANTERIORES al arranque de la sesión.


def test_session_context_sin_flight_state_igual_detecta_escritura(tmp_path, monkeypatch):
    """El caso que quedaba: sesión sin estado de vuelo que sí escribió.

    El baseline se establece en la primera lectura (hydrate del plugin) y la
    segunda ya ve la escritura, sin inventar deuda en la primera.
    """
    from fast_mcp.memories.handlers.knowledge import merge_write_evidence

    calls = []

    def scanner(root, session_id=""):
        calls.append(session_id)
        return ["src/a.py"] if len(calls) > 1 else []

    assert merge_write_evidence({}, str(tmp_path), session_id="S-1", scanner=scanner) == {}
    out = merge_write_evidence({}, str(tmp_path), session_id="S-1", scanner=scanner)

    assert out["wrote"] is True and out["touched"] == ["src/a.py"]
    assert out["touched_source"] == "watcher"
    assert calls == ["S-1", "S-1"], "la evidencia se pide por sesión, no global"


# ── baseline de escritura al abrir/vincular la sesión (P1.16) ────────


def test_el_baseline_no_se_refresca_y_es_idempotente(tmp_path):
    from fast_mcp.shared_kernel.project_support.watcher import (
        changed_since_last_scan,
        seed_session_baseline,
    )

    (tmp_path / "pyproject.toml").write_text("[project]\n")
    (tmp_path / "a.py").write_text("x = 1\n")

    assert seed_session_baseline(str(tmp_path), session_id="S") is True
    (tmp_path / "b.py").write_text("y = 1\n")

    # Ya existe: no se refresca (si se refrescara, b.py se absorbería).
    assert seed_session_baseline(str(tmp_path), session_id="S") is False
    assert changed_since_last_scan(str(tmp_path), session_id="S") == ["b.py"]

    # El canal anónimo no tiene sesión propia: baseline compartido = contaminar.
    assert seed_session_baseline(str(tmp_path), session_id="mcp") is False


def test_session_open_fija_el_baseline_antes_del_primer_context(tmp_path, monkeypatch):
    """La escritura previa al primer `session_context` también deja deuda."""
    from fast_mcp.features.tasks.handlers import session as sessions
    from fast_mcp.shared_kernel.project_support.watcher import changed_since_last_scan

    class FakeService:
        def open_session(self, sid, **kw):
            return {"session_id": sid}

    monkeypatch.setattr(sessions._di, "sprint_service", lambda chain_dir: FakeService())
    (tmp_path / "pyproject.toml").write_text("[project]\n")

    out = sessions.session_open(_ctx(str(tmp_path)), {"session_id": "S-BASE"})

    assert out.ok and out.data["write_baseline"] is True
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n")
    assert changed_since_last_scan(str(tmp_path), session_id="S-BASE") == ["src/a.py"]


def test_session_bind_fija_el_baseline(tmp_path, monkeypatch):
    from fast_mcp.features.tasks.handlers import session as sessions
    from fast_mcp.shared_kernel.project_support.watcher import changed_since_last_scan

    class FakeService:
        def list_springs(self):
            return []

        def get_session(self, sid):  # noqa: ARG002
            return None

        def get_spring(self, sid):  # noqa: ARG002
            return None

    monkeypatch.setattr(sessions._di, "sprint_service", lambda chain_dir: FakeService())
    (tmp_path / "pyproject.toml").write_text("[project]\n")

    out = sessions.session_bind(_ctx(str(tmp_path)), {"session_id": "S-BIND"})

    assert out.ok, out.error
    (tmp_path / "b.py").write_text("y = 1\n")
    assert changed_since_last_scan(str(tmp_path), session_id="S-BIND") == ["b.py"]
