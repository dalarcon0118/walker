"""test_cli_task_bdd.py — el protocolo CLI que usan los plugins del agente.

Bug medido 2026-11-17: `bin/walker3 <project>/<tool> <json>` ignoraba los
argumentos y arrancaba el server MCP por stdio. Con stdin vacío salía rc=0 sin
imprimir nada, así que TODAS las llamadas del plugin de OpenCode (trace, gates,
CKG, contexto) eran no-ops silenciosos: el agente nunca veía walker.

El contrato ahora: `<project>/<tool> [json]` ejecuta la tool y devuelve UN JSON
por stdout (el plugin hace `JSON.parse`), con exit code útil. Excepción: las
tools de verificación (`run_tests`, `verify_predicates`) agregan una segunda
línea `WALKER_RESULT ...` como contrato de certificación (SPEC-WALKER-044).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from fast_mcp.cli import run_task

BIN = Path(__file__).resolve().parents[1] / "bin" / "walker3"


def test_workflow_state_devuelve_estado(capsys):
    code = run_task(["walker/workflow_state", "{}"])

    out = capsys.readouterr().out.strip()
    assert code == 0
    data = json.loads(out)
    assert data.get("ok") is True
    assert data.get("current_position"), "el plugin necesita la posición actual"
    assert isinstance(data.get("available_gates"), list)


def test_memory_tool_via_cli(capsys):
    code = run_task(["walker/search_skills", json.dumps({"tags": ["design"], "limit": 1})])

    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert "skills" in data


def test_tool_desconocida_falla_con_json(capsys):
    code = run_task(["walker/no-existe", "{}"])

    assert code == 2
    data = json.loads(capsys.readouterr().out)
    assert "desconocida" in data["error"]


def test_json_invalido_falla(capsys):
    code = run_task(["walker/workflow_state", "{no-json"])

    assert code == 2
    assert "JSON inválido" in json.loads(capsys.readouterr().out)["error"]


@pytest.mark.parametrize("spec", ["/help", "walker/"])
def test_spec_invalida_falla(spec, capsys):
    code = run_task([spec, "{}"])

    assert code == 2
    assert "spec inválida" in json.loads(capsys.readouterr().out)["error"]


def test_sin_slash_muestra_el_uso(capsys):
    code = run_task(["walker", "{}"])

    assert code == 2
    assert "uso:" in json.loads(capsys.readouterr().out)["error"]


def test_project_id_del_payload_no_pisa_la_spec(capsys, monkeypatch):
    seen: dict = {}

    def fake_runner(**kwargs):
        seen.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr("fast_mcp.cli._runner_for", lambda tool: fake_runner)
    run_task(["walker/workflow_state", json.dumps({"project_id": "otro", "x": 1})])

    assert seen["project_id"] == "walker"
    assert seen["x"] == 1


def test_bin_walker3_responde_json_por_stdout():
    """End-to-end del binario que invoca el plugin (stdout sin ruido)."""
    proc = subprocess.run(
        [sys.executable, str(BIN), "walker/workflow_state", "{}"],
        capture_output=True, text=True, timeout=120,
    )

    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data.get("current_position")


def test_run_tests_emite_json_y_marcador(capsys, monkeypatch):
    """SPEC-WALKER-044: el marcador final es el contrato de certificación."""
    def runner(**_kwargs):
        return {
            "ok": True,
            "data": {
                "task_id": "t",
                "passed": True,
                "total": 7,
                "failures": 0,
                "errors": 0,
                "returncode": 0,
                "runner": "pytest -q",
            },
        }

    monkeypatch.setattr("fast_mcp.cli._runner_for", lambda tool: runner)
    code = run_task(["walker/run_tests", "{}"])
    lines = capsys.readouterr().out.strip().splitlines()

    assert code == 0
    assert json.loads(lines[0])["data"]["passed"] is True
    assert lines[-1] == (
        "WALKER_RESULT tool=run_tests ok=1 passed=7 failed=0 errors=0 exit=0"
    )


def test_verify_predicates_rojo_emite_marcador_no_verde(capsys, monkeypatch):
    """Un predicado fallido no puede certificar aunque el comando salga 0."""
    def runner(**_kwargs):
        return {
            "ok": True,
            "data": {
                "all_satisfied": False,
                "passed_count": 1,
                "failed_count": 1,
                "total": 2,
            },
        }

    monkeypatch.setattr("fast_mcp.cli._runner_for", lambda tool: runner)
    run_task(["walker/verify_predicates", "{}"])
    lines = capsys.readouterr().out.strip().splitlines()

    marker = lines[-1]
    assert marker.startswith("WALKER_RESULT tool=verify_predicates ok=0")
    assert "failed=1" in marker and "exit=1" in marker


def test_trace_push_conserva_session_id_en_metadata():
    """El plugin manda session_id; el evento debe permitir correlacionar."""
    import sqlite3

    from fast_mcp.features.event_tracing.application.services.trace_factory import (
        invalidate_trace_service,
    )

    code = run_task(["walker/trace_push", json.dumps({
        "event_type": "smoke_session_probe",
        "session_id": "ses_correlation_test",
        "agent_id": "build",
    })])
    assert code == 0

    from fast_mcp.shared_kernel.project_support import ProjectService

    paths = ProjectService.resolve_paths("walker")
    conn = sqlite3.connect(f"file:{paths.data_dir}/cognitive.sqlite3?mode=ro", uri=True)
    rows = [row[0] for row in conn.execute(
        "SELECT data FROM docs WHERE `index`='trace' AND data LIKE '%ses_correlation_test%'"
    )]
    assert rows, "el session_id debe quedar persistido"
    invalidate_trace_service("walker")


def test_project_for_path_resuelve_por_registro(capsys):
    code = run_task(["_/project_for_path", json.dumps({
        "path": str(Path("/Users/davidmartinez/develop/Active-projects/game_reset")),
    })])

    data = json.loads(capsys.readouterr().out)
    if code != 0:
        pytest.skip("game_reset no está registrado en esta máquina")
    assert data["project_id"] == "game_reset"
    assert data["ok"] is True


def test_project_for_path_sin_registro_falla_suave(capsys):
    code = run_task(["_/project_for_path", json.dumps({"path": "/tmp/no-registrado-xyz"})])

    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False
    assert "sin proyecto registrado" in data["error"]
