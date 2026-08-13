"""tests/test_agent_session_bdd.py — Agent Orchestration E2E: valida el ciclo de vida
completo del agente via protocolo MCP (initialize → tools → session state).

Nivel 4 de testing (según SPEC-003):
- test_agent_full_session_lifecycle
- test_agent_multi_tool_sequence
- test_agent_context_preservation_across_tools
- test_agent_error_recovery_workflow
- test_agent_side_effects_chain
"""
from __future__ import annotations

import json
from pathlib import Path

from features.main.cli_interface.domain.entities import McpSession
from tests.conftest import make_walker_project


# ── Helpers ─────────────────────────────────────────────────────────────

def _setup_project(tmp_path: Path) -> Path:
    """Crea el proyecto walker cognitivo (builder compartido en conftest)."""
    make_walker_project(tmp_path)
    return tmp_path


def _mcp_call(name: str, arguments: dict, session_id: str | None = None) -> dict:
    from features.main.cli_interface.mcp_adapter import _handle_request

    msg = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }
    if session_id:
        msg["params"]["_session_id"] = session_id
    return _handle_request(msg)


def _parse_mcp_result(resp: dict) -> dict:
    content = resp.get("result", {}).get("content", [])
    text = content[0].get("text", "") if content else ""
    return json.loads(text) if text.startswith("{") else {}


def _initialize_session() -> McpSession:
    session, _ = McpSession.create()
    session.initialize("test-agent", "1.0")
    session.activate()
    return session


# ── Tests Agent Orchestration ────────────────────────────────────────────

class TestAgentSession:
    """Ciclo de vida del agente via protocolo MCP."""

    def test_agent_full_session_lifecycle(self, tmp_path):
        _setup_project(tmp_path)
        session = _initialize_session()

        resp = _mcp_call("status", {"project_id": "walker"}, session_id=session.session_id.value)
        assert resp.get("result", {}).get("isError") is not True
        data = _parse_mcp_result(resp)
        assert data.get("status_code") == 200

    def test_agent_multi_tool_sequence(self, tmp_path):
        _setup_project(tmp_path)
        session = _initialize_session()

        tools = ["status", "list", "kg_search"]
        for tool in tools:
            args = {"project_id": "walker"} if tool == "status" else {}
            if tool == "kg_search":
                args = {"query": "test"}
            resp = _mcp_call(tool, args, session_id=session.session_id.value)
            assert resp.get("result", {}).get("isError") is not True
            data = _parse_mcp_result(resp)
            # Endurecido (fix de asserts débiles): kg_search debe devolver ok=True
            # con result estructurado — no un fallback (projects_list) ni vacío.
            if tool == "kg_search":
                d = data.get("data", {})
                assert d.get("type") != "projects_list", (
                    f"kg_search cayó al fallback: {d.get('type')}"
                )
                assert d.get("ok") is True, f"kg_search no ok: {d}"
                assert isinstance(d.get("result", {}), dict), (
                    f"kg_search result no estructurado: {d}"
                )

    def test_agent_context_preservation_across_tools(self, tmp_path):
        _setup_project(tmp_path)
        session = _initialize_session()

        resp1 = _mcp_call("status", {"project_id": "walker"}, session_id=session.session_id.value)
        data1 = _parse_mcp_result(resp1)
        assert data1.get("status_code") == 200

        resp2 = _mcp_call("list", {}, session_id=session.session_id.value)
        data2 = _parse_mcp_result(resp2)
        assert data2.get("status_code") == 200

    def test_agent_error_recovery_workflow(self, tmp_path):
        _setup_project(tmp_path)
        session = _initialize_session()

        resp = _mcp_call("nonexistent_tool_xyz", {}, session_id=session.session_id.value)
        data = _parse_mcp_result(resp)
        assert "error" in data or resp.get("result", {}).get("isError") is True

        resp2 = _mcp_call("status", {"project_id": "walker"}, session_id=session.session_id.value)
        assert resp2.get("result", {}).get("isError") is not True

    def test_agent_side_effects_chain(self, tmp_path):
        _setup_project(tmp_path)
        session = _initialize_session()

        resp1 = _mcp_call("capture_task", {
            "_project_id": "walker",
            "objective": "Agent side effects test",
            "domain": "test",
            "files": ["test.py"],
        }, session_id=session.session_id.value)
        assert resp1.get("result", {}).get("isError") is not True

        resp2 = _mcp_call("kg_search", {
            "query": "Agent side effects test",
            "index": "delta",
        }, session_id=session.session_id.value)
        assert resp2.get("result", {}).get("isError") is not True
