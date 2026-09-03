"""protocol_builder.py — Genera el protocolo MCP (versión estática adaptada a fast_mcp).

Restaurado desde el motor heredado (eliminado por commit 63efa79) y
simplificado para fast_mcp. El original generaba secciones dinámicas
(tools listing, workflow map, capture_task section) que dependían de
infraestructura muerta (tool_catalog, module_registry, compose_workflow_map,
WorkflowRepository de shared/core/engine).

Esta versión retorna la plantilla estática protocol/mcp.md que contiene
las instrucciones core del protocolo MCP. Las secciones dinámicas pueden
ser reintegradas cuando el tool_catalog de fast_mcp esté disponible.
"""
from __future__ import annotations

from .static_loader import load_static


def build_mcp_protocol() -> str:
    """Genera el protocolo MCP desde la plantilla canónica estática."""
    return load_static("protocol/mcp")
