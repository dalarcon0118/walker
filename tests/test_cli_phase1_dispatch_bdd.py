"""test_cli_phase1_dispatch_bdd.py — el CLI despacha las tools de la Fase 1.

El plugin le dice al usuario `walker3 _/prepare '{"path":"..."}'` cuando el
proyecto no está registrado, y `_runner_for` solo miraba Fase 2/3: el comando
respondía `tool desconocida: 'prepare'`. Un uso documentado que no existe es
peor que un error: deja al agente sin salida.

Este test fija el contrato: prepare/list/status se resuelven por el composition
root (contexto cableado), no por una copia de la lógica en el CLI.
"""
from __future__ import annotations

import json

import pytest

from fast_mcp import cli
from fast_mcp.phases.phase1_prepare import _FASE1_TOOLS


def test_las_tres_tools_de_fase1_tienen_runner():
    for name in _FASE1_TOOLS:
        runner = cli._runner_for(name)
        assert callable(runner), f"{name} no tiene runner en el CLI"


def test_tool_desconocida_sigue_fallando():
    assert cli._runner_for("no_existe") is None


@pytest.mark.parametrize("tool", ["prepare", "list", "status"])
def test_runtask_despacha_fase1_por_composition(tool, tmp_path, monkeypatch, capsys):
    from fast_mcp import composition

    seen: dict = {}

    def fake_factory(name):
        def runner(**kwargs):
            seen["name"] = name
            seen.update(kwargs)
            return {"type": "fake", "tool": name}

        return runner

    monkeypatch.setattr(composition, "project_tool_runner", fake_factory)

    project = "_" if tool == "prepare" else "walker"
    payload = {"path": str(tmp_path)} if tool == "prepare" else {}
    rc = cli.run_task([f"{project}/{tool}", json.dumps(payload)])

    assert rc == 0
    assert seen["name"] == tool
    printed = json.loads(capsys.readouterr().out.strip().splitlines()[0])
    assert printed == {"type": "fake", "tool": tool}
