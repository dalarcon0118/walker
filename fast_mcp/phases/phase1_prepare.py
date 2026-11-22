"""fast_mcp.phases.phase1_prepare — Fase 1: prepare / list / status.

Las 3 tools del workflow core (ADR §3 Fase 1). Los use cases VIVEN en
`fast_mcp.features.project` y esta fase solo los REGISTRA recibiendo el
contexto ya cableado por `fast_mcp.composition` (regla de coherencia §3bis 3).
Sin imports de `mcp/` (ADR-WALKER-071 §0 #4/#5).
"""

from __future__ import annotations

import logging

from mcp.server.fastmcp import FastMCP

from fast_mcp import composition
from fast_mcp.registration import register_domain_tool

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
            "session_id": {"type": "string", "description": "Sesión para resolver el sprint activo."},
        },
    },
}

_DESCRIPTIONS = {
    "prepare": "Initialize project at path (creates .cognitive/).",
    "list": "List projects managed by walker.",
    "status": "Project workflow state + memory stats: node, sub_state, instructions, gates, CPG/CKG/Delta counts.",
}


def install(server: FastMCP) -> None:
    """Registra las 3 tools de la Fase 1.

    Los runners llegan YA CABLEADOS desde composition (regla de coherencia
    §3bis 2/3): esta fase declara nombres/schemas y registra — sin tocar
    features.
    """
    for name in _FASE1_TOOLS:
        register_domain_tool(
            server,
            name=name,
            runner=composition.project_tool_runner(name),
            description=_DESCRIPTIONS[name],
            input_schema=_INPUT_SCHEMAS[name],
        )
    logger.info("fast_mcp fase1: %s registradas (autónomas)", ", ".join(_FASE1_TOOLS))
