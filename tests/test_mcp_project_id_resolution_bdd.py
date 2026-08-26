"""tests/test_mcp_project_id_resolution_bdd.py — Regresión del dispatch MCP por
`_project_id` (fix del fallback silencioso).

ANTES: _resolve_project_id solo leía `project_id`. Un agente que llamara
save_to_ckg/recall_knowledge desde un CWD que NO es el proyecto, pasando `_project_id`,
caía SILENCIOSAMENTE al fallback projects_list (status 200) — el dispatch
resolvía la tool equivocada sin error.

AHORA: _project_id se lee como fallback — la tool se despacha al proyecto
correcto sin importar el CWD.
"""
from __future__ import annotations

import json
from pathlib import Path

from features.delta_tracking import spring_service
from tests.conftest import make_chain_dir, make_walker_project


def _setup_project(tmp_path: Path) -> tuple[str, str]:
    """Crea el proyecto walker + chain_dir con delta (spec indexable)."""
    project_root = make_walker_project(tmp_path)
    chain_dir = make_chain_dir(tmp_path, "sprint-proj")
    svc = spring_service(chain_dir=chain_dir)
    assert svc is not None
    spring_id = svc.get_spring_id(chain_dir)
    svc.create_spring(spring_id, goal="Proj ID test", project_id="walker", session_id="sess")
    svc.add_spec(spring_id, objective="spec proyecto", domain="test", spec_type="feature", spec_index=0, status="draft")
    return project_root, chain_dir


def _mcp_call(name: str, arguments: dict, cwd: str) -> dict:
    """Invoca la tool MCP con un CWD arbitrario (simula agente fuera del proyecto)."""
    import os

    from features.main.cli_interface.mcp_adapter import _handle_request

    old = os.getcwd()
    try:
        os.chdir(cwd)
        msg = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        return _handle_request(msg)
    finally:
        os.chdir(old)


def _parse(resp: dict) -> dict:
    content = resp.get("result", {}).get("content", [])
    text = content[0].get("text", "") if content else ""
    return json.loads(text) if text.startswith("{") else {}


class TestProjectIdResolution:
    """El dispatch MCP respeta _project_id aunque el CWD no sea el proyecto."""

    def test_save_to_ckg_con_project_id_desde_cwd_arbitrario(self, tmp_path):
        _, chain_dir = _setup_project(tmp_path)
        # CWD arbitrario: un directorio que NO es el proyecto (otra raíz tmp).
        cwd_otro = str(tmp_path / "otro-dir")
        Path(cwd_otro).mkdir(exist_ok=True)

        resp = _mcp_call("save_to_ckg", {
            "_project_id": "walker",
            "statement": "decision desde CWD arbitrario",
            "type": "decision",
            "tags": ["proj-id"],
        }, cwd=cwd_otro)
        data = _parse(resp)
        # ANTES: caía a projects_list (data.type == "projects_list") sin error.
        assert data.get("data", {}).get("type") != "projects_list", (
            f"dispatch cayó al fallback projects_list: {data.get('data', {}).get('type')}"
        )
        assert data.get("status_code") == 200, data
        assert data.get("data", {}).get("ok") is True, data

    def test_recall_knowledge_con_project_id_desde_cwd_arbitrario(self, tmp_path):
        _, chain_dir = _setup_project(tmp_path)
        cwd_otro = str(tmp_path / "otro-dir-2")
        Path(cwd_otro).mkdir(exist_ok=True)

        resp = _mcp_call("recall_knowledge", {
            "_project_id": "walker",
            "query": "spec proyecto",
            "index": "delta",
        }, cwd=cwd_otro)
        data = _parse(resp)
        assert data.get("data", {}).get("type") != "projects_list", (
            f"dispatch cayó al fallback projects_list: {data.get('data', {}).get('type')}"
        )
        assert data.get("status_code") == 200, data
        d = data.get("data", {})
        assert d.get("ok") is True, data

    def test_tool_sin_project_id_sigue_resolviendo_desde_cwd(self, tmp_path):
        """Sin _project_id ni project_id, el dispatch resuelve desde el CWD."""
        _, chain_dir = _setup_project(tmp_path)
        # CWD = la raíz del proyecto (make_walker_project crea .cognitive ahí).
        resp = _mcp_call("save_to_ckg", {
            "statement": "resolver por CWD",
            "type": "decision",
        }, cwd=str(tmp_path))
        data = _parse(resp)
        assert data.get("status_code") == 200, data
