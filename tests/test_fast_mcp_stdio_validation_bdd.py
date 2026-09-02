"""test_fast_mcp_stdio_validation_bdd.py — Validación directa del server real.

Conecta al entry-point standalone `bin/walker-mcp` vía el transporte MCP stdio
(real, el mismo que usa opencode): initialize → tools/list (EXACTAMENTE las 3
tools del ADR §3 Fase 1) → tools/call status.
"""

from __future__ import annotations

import asyncio
import os

from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.session import ClientSession


_WALKER_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BIN = os.path.join(_WALKER_ROOT, "bin", "walker-mcp")


def _params() -> StdioServerParameters:
    return StdioServerParameters(
        command="python3",
        args=[_BIN],
        cwd=_WALKER_ROOT,
        env={**os.environ},
    )


def test_stdio_handshake_y_tools_list():
    """El server arranca por stdio y expone el contrato completo del ADR.

    Superficie canónica: 3 tools de Fase 1 (prepare/list/status) + 14 de Fase 2
    (memorias) + 4 de Fase 3 (tasks). Test de completitud (patrón AGENTS.md):
    si se registra una fase nueva, este conteo se actualiza explícitamente.
    """

    async def _go():
        async with stdio_client(_params()) as (read, write):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                assert init.serverInfo.name == "walker"
                tools = await session.list_tools()
                return {t.name for t in tools.tools}, tools

    names, tools = asyncio.run(_go())
    # Contrato mínimo del ADR (§3 Fase 1) — siempre presentes.
    assert {"prepare", "list", "status"} <= names, f"faltan tools Fase 1: {names}"
    # Superficie total registrada (F1: 3 + F2: 14 + F3: 7 = 24).
    assert len(names) == 24, f"superficie inesperada ({len(names)}): {sorted(names)}"
    status = next(t for t in tools.tools if t.name == "status")
    assert status.inputSchema.get("type") == "object"


def test_stdio_call_status():
    """Un tools/call real resuelve el health del proyecto (registro real)."""

    async def _go():
        async with stdio_client(_params()) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.call_tool("status", {"_project_id": "walker"})

    result = asyncio.run(_go())
    assert result.content, "sin content"
    text = result.content[0].text
    assert "walker" in text, f"status no devolvió proyecto: {text[:200]}"
