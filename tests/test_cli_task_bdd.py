"""test_cli_task_bdd.py — el protocolo CLI que usan los plugins del agente.

Bug medido 2026-11-17: `bin/walker3 <project>/<tool> <json>` ignoraba los
argumentos y arrancaba el server MCP por stdio. Con stdin vacío salía rc=0 sin
imprimir nada, así que TODAS las llamadas del plugin de OpenCode (trace, gates,
CKG, contexto) eran no-ops silenciosos: el agente nunca veía walker.

El contrato ahora: `<project>/<tool> [json]` ejecuta la tool y devuelve UN JSON
por stdout (el plugin hace `JSON.parse`), con exit code útil.
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
