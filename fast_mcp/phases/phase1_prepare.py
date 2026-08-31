"""fast_mcp.phases.phase1_prepare — Fase 1: prepare / list / status.

Los 3 casos de uso del workflow core VIVEN dentro de fast_mcp
(`fast_mcp.use_cases`, stdlib only) — portados del legacy según el
ADR-WALKER-071 §0 #4/#5: fast_mcp NO importa `mcp/` NUNCA.

El inputSchema de cada tool se declara aquí como SSOT local de la fase
(coincide con el contrato legacy) y se adjunta a `Tool.parameters`
(R-03bis: el schema servido al agente es el declarativo).
"""

from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from fast_mcp.registration import register_domain_tool
from fast_mcp import use_cases

logger = logging.getLogger(__name__)

# Nombres de las 3 tools del workflow core (Fase 1).
_FASE1_TOOLS = ("prepare", "list", "status")

# SSOT local del inputSchema (contrato legacy, declarativo — no inferido).
_INPUT_SCHEMAS: dict[str, dict] = {
    "prepare": {
        "type": "object",
        "properties": {"path": {"type": "string"}},
    },
    "list": {"type": "object", "properties": {}},
    "status": {
        "type": "object",
        "properties": {
            "_project_id": {"type": "string"},
            "project_id": {"type": "string"},
        },
    },
}

_DESCRIPTIONS = {
    "prepare": "Initialize project at path (creates .cognitive/).",
    "list": "List projects managed by walker.",
    "status": "Project workflow state + memory stats: node, sub_state, instructions, gates, CPG/CKG/Delta counts.",
}


def _runner(name: str):
    """Handler de la tool → caso de uso local (vive en fast_mcp.use_cases)."""

    def runner(**kwargs: Any) -> dict:
        if name == "prepare":
            return use_cases.prepare_use_case(
                project_path=kwargs.get("path", ""), args=kwargs
            )
        if name == "list":
            return use_cases.list_use_case(kwargs)
        if name == "status":
            return use_cases.status_use_case(kwargs)
        raise ValueError(f"tool '{name}' sin caso de uso local")

    return runner


def install(server: FastMCP) -> None:
    """Registra las 3 tools de la Fase 1 (implementación local, sin mcp/)."""
    for name in _FASE1_TOOLS:
        register_domain_tool(
            server,
            name=name,
            runner=_runner(name),
            description=_DESCRIPTIONS[name],
            input_schema=_INPUT_SCHEMAS[name],
        )
    logger.info("fast_mcp fase1: %s registradas (autónomas)", ", ".join(_FASE1_TOOLS))
