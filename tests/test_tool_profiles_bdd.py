"""test_tool_profiles_bdd.py — superficie de tools: 33 vs 10.

Medido: el server registra 33 tools = ~3.500 tokens de descripción+esquema que
el agente paga en cada turno y entre las que tiene que elegir. Varias se solapan
con tools nativas (`read_code`/`read`, `code_search`/`grep`) y otras son
telemetría que escribe el plugin (`trace_push`, `trace_message`).

Un perfil es la lista blanca. Estos tests fijan el contrato: `full` no rompe, y
`agent` no expone nada que no use para resolver una tarea.
"""

from __future__ import annotations

import asyncio
import json
import os

import pytest

from fast_mcp import tool_profiles
from fast_mcp.server import build_server


def _tools(monkeypatch, profile: str | None):
    if profile is None:
        monkeypatch.delenv("WALKER_MCP_PROFILE", raising=False)
    else:
        monkeypatch.setenv("WALKER_MCP_PROFILE", profile)
    server = build_server()
    tools = asyncio.run(server.list_tools())
    return {t.name: t for t in tools}


def _surface_chars(tools: dict) -> int:
    return sum(len(t.description or "") + len(json.dumps(t.inputSchema or {}))
               for t in tools.values())


def test_sin_var_de_entorno_no_se_recorta_nada(monkeypatch):
    tools = _tools(monkeypatch, None)

    assert len(tools) == 37, "el default debe ser el contrato histórico"


def test_perfil_agent_expone_las_tools_del_perfil(monkeypatch):
    tools = _tools(monkeypatch, "agent")

    assert set(tools) == set(tool_profiles.AGENT)


def test_perfil_agent_no_expone_telemetria_ni_mantenimiento(monkeypatch):
    tools = _tools(monkeypatch, "agent")

    for name in ("trace_push", "trace_message", "build_code_index",
                 "update_incremental_code", "index_repo", "memory_schema",
                 "documenting_knowledge", "extract_knowledge"):
        assert name not in tools, f"{name} es plumbing, no superficie de agente"


def test_perfil_agent_no_duplica_tools_nativas(monkeypatch):
    tools = _tools(monkeypatch, "agent")

    # read_code / code_search / code_context reimplementan read/grep/glob del
    # agente: quién gana la elección es ruido, no capacidad.
    for name in ("read_code", "code_search", "code_context", "inspect_codebase"):
        assert name not in tools, f"{name} solapa con una tool nativa"


def test_perfil_agent_conserva_el_ciclo_de_trabajo(monkeypatch):
    tools = _tools(monkeypatch, "agent")

    assert {"workflow_state", "submit", "advance", "transition", "status"} <= set(tools)
    assert {"agent_context", "search_skills", "load_skill"} <= set(tools)
    assert {"run_tests", "verify_predicates", "capture"} <= set(tools)


def test_perfil_agent_baja_la_superficie_al_menos_70_por_ciento(monkeypatch):
    full = _surface_chars(_tools(monkeypatch, "full"))
    agent = _surface_chars(_tools(monkeypatch, "agent"))

    assert agent < full * 0.30, f"agent={agent} chars vs full={full}"


def test_perfil_admin_es_superconjunto_de_agent(monkeypatch):
    agent = set(_tools(monkeypatch, "agent"))
    admin = set(_tools(monkeypatch, "admin"))

    assert agent < admin
    assert "prepare" in admin and "build_code_index" in admin


def test_perfil_desconocido_cae_a_full(monkeypatch):
    monkeypatch.setenv("WALKER_MCP_PROFILE", "inexistente")

    assert tool_profiles.active_profile() == "full"
    assert len(_tools(monkeypatch, "inexistente")) == 37


def test_perfil_con_nombre_inexistente_avisa_pero_no_rompe(monkeypatch, caplog):
    """Si una tool del perfil desaparece, el boot sigue (warning, no excepción)."""
    monkeypatch.setitem(tool_profiles.PROFILES, "roto",
                        frozenset({"status", "no_existe_esta_tool"}))
    monkeypatch.setenv("WALKER_MCP_PROFILE", "roto")

    with caplog.at_level("WARNING"):
        tools = _tools(monkeypatch, "roto")

    assert set(tools) == {"status"}
    assert "no_existe_esta_tool" in caplog.text


def test_ablation_inyecta_el_perfil(monkeypatch):
    from scripts import ablation

    lean = ablation.build_cmd("/r", "/t.json", "lean", 1, None, "/o")
    full = ablation.build_cmd("/r", "/t.json", "full", 1, None, "/o")
    base = ablation.build_cmd("/r", "/t.json", "baseline", 1, None, "/o")

    assert lean[lean.index("--tool-profile") + 1] == "agent"
    assert full[full.index("--tool-profile") + 1] == "full"
    assert "--tool-profile" not in base and "--no-mcp" in base
