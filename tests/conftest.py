"""Fixtures globales de la suite de tests.

Seguridad: fuerza SessionStore.SESSIONS_DIR a un directorio temporal por
test para que NINGUNA prueba toque el <cognitive>/sessions/ real.

Motivación (incidente real): cuando un test ejercita la cadena pública
(use_cases -> WorkflowAPI.ensure_session -> SessionStore.gc_stale) con
SESSIONS_DIR en None, la resolución via config apunta al dir real del
proyecto y un GC de sesiones stale puede borrar datos productivos.
Este fixture hace que el default de toda la suite sea un tamptdir aislado.
"""
from __future__ import annotations

import json

import pytest

from features.engine.domain.session import SessionStore


@pytest.fixture(autouse=True)
def _sandbox_sessions_dir(tmp_path, monkeypatch):
    """Apunta SessionStore a un sessions_dir temporal por cada test."""
    sdir = tmp_path / "walkertest-sessions"
    sdir.mkdir(exist_ok=True)
    monkeypatch.setattr(SessionStore, "SESSIONS_DIR", str(sdir))
    monkeypatch.setattr(SessionStore, "ACTIVE_FILE", None)
    monkeypatch.setattr(SessionStore, "_LAST_GC", 0.0)
    yield
    # Restaura el estado de clase para no filtrar tmp_path entre tests.
    SessionStore.SESSIONS_DIR = None
    SessionStore.ACTIVE_FILE = None
    SessionStore._LAST_GC = 0.0


@pytest.fixture
def mcp_project(tmp_path):
    """Crea un proyecto temporal con .cognitive/ listo para tests MCP."""
    cog = tmp_path / ".cognitive" / "projects" / "walker"
    cog.mkdir(parents=True)
    (cog / "data").mkdir()
    (cog / "data" / "spring").mkdir()
    project_json = {
        "project_id": "walker",
        "name": "walker",
        "repo_path": str(tmp_path),
        "language": "python",
        "framework": "unknown",
        "status": "active",
    }
    with open(cog / "data" / "project.json", "w") as f:
        json.dump(project_json, f)
    from features.delta_tracking.infrastructure.cognitive_delta_repo import (
        CognitiveDeltaRepository,
    )
    CognitiveDeltaRepository(str(cog))
    return str(cog)


def call_mcp_tool(project_root: str, tool_name: str, arguments: dict) -> dict:
    """Helper reutilizable: invoca una tool MCP via JSON-RPC y devuelve el payload."""
    from features.cli_interface.mcp_adapter import _handle_request

    msg = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }
    resp = _handle_request(msg)
    content = resp.get("result", {}).get("content", [])
    text = content[0].get("text", "") if content else ""
    try:
        return json.loads(text) if text.startswith("{") else {"raw": text}
    except json.JSONDecodeError:
        return {"raw": text}