"""test_hooks_public_api_bdd.py — la API que ven los autores (`fast_mcp.hooks`).

El contrato público vive en `fast_mcp/hooks.py`; `shared_kernel.hooks` es la
implementación y puede cambiar. Sin esta capa, cada autor leía stdin a mano,
adivinaba el nombre de las variables de entorno y dejaba tracebacks sin contexto
en el log del commit.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

import fast_mcp.hooks as api
from fast_mcp.hooks import (
    event,
    fail,
    known_events,
    payload,
    project_id,
    python,
    repo_path,
    run,
    walker_root,
    warn,
)


@pytest.fixture(autouse=True)
def _reset_payload(monkeypatch):
    monkeypatch.setattr(api, "_PAYLOAD_CACHE", None)


def _stdin(monkeypatch, text: str) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(text))


def test_payload_parsea_json_de_stdin(monkeypatch):
    _stdin(monkeypatch, '{"task": "run_tests", "ok": true}')
    assert payload() == {"task": "run_tests", "ok": True}


def test_payload_vacio_devuelve_dict(monkeypatch):
    _stdin(monkeypatch, "")
    assert payload() == {}


def test_payload_json_invalido_no_lanza(monkeypatch):
    _stdin(monkeypatch, "no-json")
    assert payload() == {"raw": "no-json"}


def test_payload_se_lee_una_sola_vez(monkeypatch):
    calls = {"read": 0}

    class Counting(io.StringIO):
        def read(self, *args, **kwargs):  # type: ignore[override]
            calls["read"] += 1
            return super().read(*args, **kwargs)

    monkeypatch.setattr(sys, "stdin", Counting('{"a": 1}'))
    payload()
    payload()

    assert calls["read"] == 1, "el payload se cachea (stdin no se puede releer)"


def test_contexto_desde_el_entorno(monkeypatch, tmp_path):
    monkeypatch.setenv("WALKER_EVENT", "cpg.indexed")
    monkeypatch.setenv("WALKER_PROJECT", "walker")
    monkeypatch.setenv("WALKER_REPO", str(tmp_path))
    monkeypatch.setenv("WALKER_ROOT", str(tmp_path))
    monkeypatch.setenv("WALKER_PYTHON", "/usr/bin/python3")

    assert event() == "cpg.indexed"
    assert project_id() == "walker"
    assert repo_path() == tmp_path.resolve()
    assert walker_root() == tmp_path.resolve()
    assert python() == "/usr/bin/python3"


def test_known_events_es_el_catalogo_real():
    from fast_mcp.shared_kernel.hooks.events import EVENTS

    assert sorted(known_events()) == sorted(EVENTS)


def test_run_devuelve_el_codigo_de_main(monkeypatch):
    _stdin(monkeypatch, '{"n": 2}')
    assert run(lambda data: data["n"]) == 2
    assert run(lambda data: None) == 0


def test_run_convierte_excepcion_en_exit_code_y_stderr(monkeypatch, capsys):
    _stdin(monkeypatch, "{}")

    def boom(data):
        raise RuntimeError("algo se rompió")

    assert run(boom) == 1
    err = capsys.readouterr().err
    assert "RuntimeError: algo se rompió" in err


def test_warn_y_fail_escriben_a_stderr(capsys):
    assert fail("no se pudo", code=3) == 3
    warn("ojo")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "ERROR: no se pudo" in captured.err
    assert "ojo" in captured.err


def test_hook_real_usa_la_api_a_traves_del_bus(tmp_path):
    """End-to-end: paquete con entry que usa fast_mcp.hooks, corrido por el bus."""
    from fast_mcp.shared_kernel.hooks.bus import HookBus, HookSpec

    package = tmp_path / "pkg"
    package.mkdir()
    (package / "hook.json").write_text(json.dumps({
        "id": "api-demo", "version": "1.0.0", "walker": ">=3.0,<4",
        "events": ["task.after"], "entry": "main.py",
        "permissions": ["env:all", "fs:repo"],
    }))
    (package / "main.py").write_text(
        "from fast_mcp.hooks import event, payload\n"
        "print(f'{event()}:{payload()[\"task\"]}')\n"
    )
    project = tmp_path / "repo"
    project.mkdir()
    bus = HookBus(
        specs=[HookSpec(id="api-demo", event="task.after",
                        command=f'"$WALKER_PYTHON" "{package}/main.py"',
                        permissions=("env:all", "fs:repo"))],
        project_root=project, walker_root=Path(__file__).resolve().parents[1],
    )

    result = bus.emit("task.after", {"task": "capture"})[0]

    assert result.ok is True, result.stderr
    assert result.stdout.strip() == "task.after:capture"


def test_doc_lista_todos_los_eventos_y_la_api():
    """Anti-drift: si se agrega un evento al catálogo, la doc lo tiene que decir."""
    from fast_mcp.shared_kernel.hooks.events import known_events

    doc = (Path(__file__).resolve().parents[1] / "docs" / "HOOKS.md").read_text()

    for event in known_events():
        assert f"`{event}`" in doc, f"el evento {event} no está documentado"
    assert "fast_mcp.hooks" in doc, "la doc debe apuntar a la API pública"
    assert "Crear un hook" in doc
