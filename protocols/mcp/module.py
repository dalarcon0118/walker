"""protocols/mcp/module.py — Manifiesto del adaptador MCP (ADR-066 F3).

Expone el servidor MCP (McpServer) como servicio del protocolo. No implementa
puertos del motor: es un input adapter (driver) de nivel protocolo que traduce
tools MCP a dispatch del Router.
"""
from __future__ import annotations

from protocols.mcp.mcp_server import McpServer
from shared.libs.module_system.contracts import ModuleManifest


MODULE = ModuleManifest(
    name="protocols_mcp",
    imports=["shared", "features"],
    providers=[McpServer],
    adapters=[],
    exports=["mcp_server", "mcp_adapter"],
)

# Auto-registro por import (ADR-WALKER-068): el composition root importa este
# modulo; al importarlo se registra su manifest. Sin lista central de paths.
from shared.libs.module_system.global_registry import register_manifest as _register_manifest

_register_manifest(MODULE)
