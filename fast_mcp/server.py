"""fast_mcp.server — Assembly del FastMCP server de Walker (AUTÓNOMO).

`build_server()` construye el FastMCP server (framework) y registra las fases
de la migración. Es la pieza reutilizable por los tests y el punto de entrada
(`main()` corre el transport).

AUTONOMÍA TOTAL (ADR-WALKER-071 §0 #5): este paquete NO pone `mcp/` en
sys.path y NO importa nada de `mcp/` (ni app/, features/, shared/, protocols/
— la fuente transitoria). Los casos de uso migrados VIVEN dentro de
`fast_mcp/` (fast_mcp.use_cases / fast_mcp.core, stdlib only). Al cierre,
`mcp/` se elimina por completo y `fast_mcp/` queda sirviendo el dominio.

Separación build/run: aquí SOLO se ensambla (tools/resources/prompts). El
transporte (stdio / Streamable HTTP) lo decide el runner (`main()`).
"""

from __future__ import annotations

import logging

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

_SERVER_NAME = "walker"


def build_server() -> FastMCP:
    """Construye el FastMCP server autónomo y ensambla las fases activas.

    Las tools expuestas son EXACTAMENTE las del ADR-WALKER-071 §3 (sin tools
    extra): Fase 1 registra el workflow core (prepare/list/status/submit/
    transition/help). Cada fase usa los casos de uso que VIVEN en fast_mcp/
    (sin importar mcp/).
    """
    from fast_mcp.phases import install as install_phases
    from fast_mcp.tool_profiles import apply_profile

    server = FastMCP(_SERVER_NAME)
    install_phases(server)
    # WALKER_MCP_PROFILE recorta la superficie ANTES de que el agente la vea
    # (`tools/list`). Cada tool extra se paga en tokens y en atención en TODOS
    # los turnos; ver fast_mcp/tool_profiles.py.
    apply_profile(server)
    return server
