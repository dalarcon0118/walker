"""Tests BDD (pytest-bdd) — Regresión de recall_knowledge por la ruta MCP real.

Escenarios: bdd/features/recall_knowledge_integration.feature

Cubre el bug F821 (undefined name 'ckg_analyze' en kg.py) que los tests de
capa (test_cognitive_graph_bdd) no ejercitaban: aquí se despacha la task
pública vía el protocolo MCP (_handle_request → router → dispatch → task),
la misma cadena que usa el agente en producción.
"""
from __future__ import annotations

import json

import pytest
from pytest_bdd import given, scenarios, then, when

scenarios("bdd/features/recall_knowledge_integration.feature")


@pytest.fixture
def kg_mcp_ctx() -> dict:
    return {}


# ── Given ─────────────────────────────────────────────────────────────────────


@given("que el servidor MCP compuesto está registrado")
def _server_registered(kg_mcp_ctx: dict):
    # Fuerza el build del servidor compuesto (igual que el arranque del MCP).
    from features.main.cli_interface.mcp_adapter import _build_server

    kg_mcp_ctx["server"] = _build_server()


@given("que consulto las herramientas desde el protocolo MCP")
def _tools_listable(kg_mcp_ctx: dict):
    server = kg_mcp_ctx["server"]
    names = {t["name"] for t in server.list_tools()}
    assert "recall_knowledge" in names, f"recall_knowledge no está entre las tools: {sorted(names)}"


@given("que el argumento query llega como string (flags_builder de la ruta)")
def _query_as_string(kg_mcp_ctx: dict):
    kg_mcp_ctx["query_arg"] = "authentication"


# ── When ──────────────────────────────────────────────────────────────────────


def _call_recall_knowledge(arguments: dict, kg_mcp_ctx: dict):
    from features.main.cli_interface.mcp_adapter import _handle_request

    kg_mcp_ctx["resp"] = _handle_request(
        {
            "jsonrpc": "2.0",
            "id": 99,
            "method": "tools/call",
            "params": {"name": "recall_knowledge", "arguments": arguments},
        }
    )


@when("se hace tools/call a recall_knowledge con query")
def _tools_call_recall_knowledge(kg_mcp_ctx: dict):
    _call_recall_knowledge({"query": {"text": "authentication"}}, kg_mcp_ctx)


@when("se hace tools/call a recall_knowledge con query string")
def _tools_call_recall_knowledge_string(kg_mcp_ctx: dict):
    _call_recall_knowledge({"query": kg_mcp_ctx["query_arg"]}, kg_mcp_ctx)


# ── Then ──────────────────────────────────────────────────────────────────────


@then("la respuesta tiene id y sin error JSON-RPC")
def _assert_jsonrpc_ok(kg_mcp_ctx: dict):
    resp = kg_mcp_ctx["resp"]
    assert resp.get("id") == 99, resp
    assert "error" not in resp, resp


@then("el content[0].text parseado tiene status_code 200")
def _assert_status_200(kg_mcp_ctx: dict):
    resp = kg_mcp_ctx["resp"]
    content = (resp.get("result") or {}).get("content") or []
    assert content, "tools/call recall_knowledge sin content"
    text = content[0].get("text", "")
    payload = json.loads(text) if text.startswith("{") else {}
    assert payload.get("status_code") == 200, payload


@then("el resultado contiene el índice ckg de la memoria del proyecto")
def _assert_ckg_index(kg_mcp_ctx: dict):
    resp = kg_mcp_ctx["resp"]
    content = (resp.get("result") or {}).get("content") or []
    text = content[0].get("text", "")
    payload = json.loads(text) if text.startswith("{") else {}
    data = payload.get("data", {}) or {}
    result = data.get("result", {}) or {}
    # recall_knowledge devuelve {took, index, hits,...} — el índice default es ckg.
    assert result.get("index") == "ckg", result.keys()