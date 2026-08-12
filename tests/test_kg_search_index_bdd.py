"""tests/test_kg_search_index_bdd.py — Regresión del flags_builder de kg_search.

ANTES: el route flags_builder hacía {"_payload": a.get("query", {})} — descartaba
`index`. Un agente que pedía index="delta" consultaba el índice DEFAULT (ckg)
sin error ni aviso: fallo silencioso de dispatch.

AHORA: el payload completo del tool se preserva (query/index/_index/_project_id)
y la task kg_search acepta el alias index → _index.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from tests.conftest import register_project_in_global_registry


@pytest.fixture
def _kg_project(tmp_path):
    """Proyecto con spring + spec en delta, registrado (reversible)."""
    from features.delta_tracking import SpringService

    tmp = str(tmp_path)
    reg_ctx = register_project_in_global_registry(tmp).__enter__()
    try:
        cog = Path(tmp) / ".cognitive" / "projects" / "walker"
        cog.mkdir(parents=True)
        (cog / "data").mkdir()
        (cog / "data" / "project.json").write_text(json.dumps({
            "project_id": "walker", "name": "walker", "repo_path": tmp,
            "language": "python", "framework": "unknown", "status": "active",
        }))
        from features.delta_tracking.infrastructure.cognitive_delta_repo import (
            CognitiveDeltaRepository,
        )

        CognitiveDeltaRepository(str(cog))
        svc = SpringService(str(cog))
        svc.create_spring("sprint-kg", goal="KG sprint", status="active",
                          project_id="walker", session_id="s1")
        yield str(cog)
    finally:
        reg_ctx.__exit__(None, None, None)
        from features.engine.bootstrap import reset_module_registry

        reset_module_registry()
        from features.cli_interface import mcp_adapter

        mcp_adapter._SERVER = None


def _mcp_call(name: str, arguments: dict) -> dict:
    from features.cli_interface.mcp_adapter import _handle_request

    msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
           "params": {"name": name, "arguments": arguments}}
    return _handle_request(msg)


def _parse(resp: dict) -> dict:
    content = resp.get("result", {}).get("content", [])
    text = content[0].get("text", "") if content else ""
    return json.loads(text) if text.startswith("{") else {}


class TestKgSearchIndex:
    """kg_search respeta el índice solicitado (fix del fallo silencioso)."""

    def test_kg_search_index_delta_consulta_delta(self, _kg_project):
        resp = _mcp_call("kg_search", {
            "_project_id": "walker",
            "query": "login",
            "index": "delta",
        })
        data = _parse(resp)
        assert data.get("status_code") == 200, data
        d = data.get("data", {})
        assert d.get("ok") is True, f"kg_search no ok: {d}"
        result = d.get("result", {})
        # ANTES: consultaba ckg (index default) aunque pidieran delta
        assert result.get("index") == "delta", (
            f"kg_search consultó '{result.get('index')}' en vez de 'delta' — "
            f"fallo silencioso del flags_builder: {result}"
        )
        assert isinstance(result.get("hits", {}).get("total"), int)

    def test_kg_search_sin_index_usa_default_ckg(self, _kg_project):
        resp = _mcp_call("kg_search", {
            "_project_id": "walker",
            "query": "login",
        })
        data = _parse(resp)
        assert data.get("status_code") == 200, data
        d = data.get("data", {})
        assert d.get("ok") is True, f"kg_search no ok: {d}"
        result = d.get("result", {})
        assert result.get("index") == "ckg", (
            f"sin index debería consultar ckg, consultó {result.get('index')}"
        )

    def test_kg_search_con_under_index(self, _kg_project):
        """El formato legacy _index sigue funcionando."""
        resp = _mcp_call("kg_search", {
            "_project_id": "walker",
            "text": "login",
            "_index": "delta",
        })
        data = _parse(resp)
        assert data.get("status_code") == 200, data
        d = data.get("data", {})
        assert d.get("ok") is True, f"kg_search no ok: {d}"
        assert d.get("result", {}).get("index") == "delta"
