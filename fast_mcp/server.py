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


def _warm_imports() -> None:
    """Importa en el boot los módulos que los handlers importan en caliente.

    Medido 2026-11-20: la primera ráfaga concurrente (`status` +
    `agent_context` + `workflow_state`) tiró
    `deadlock detected by _ModuleLock('…check_cpg_health')`. El lock es del
    importador, no del dominio: dos hilos entrando a la vez en un import
    perezoso dentro de un handler. Importarlos antes de aceptar requests
    elimina la carrera por construcción (no hay un lock de dominio que tomar).
    """
    import fast_mcp.features.code_analysis.application.use_cases.check_cpg_health  # noqa: F401
    import fast_mcp.features.cognitive_knowledge.application.services.ckg_read  # noqa: F401
    import fast_mcp.features.tasks.infrastructure.verify_engine  # noqa: F401


def build_server() -> FastMCP:
    """Construye el FastMCP server autónomo y ensambla las fases activas.

    Las tools expuestas son EXACTAMENTE las del ADR-WALKER-071 §3 (sin tools
    extra): Fase 1 registra el workflow core (prepare/list/status/submit/
    transition/help). Cada fase usa los casos de uso que VIVEN en fast_mcp/
    (sin importar mcp/).
    """
    from fast_mcp.phases import install as install_phases
    from fast_mcp.shared_kernel.runtime_safety import install_cancellation_guard
    from fast_mcp.shared_kernel.telemetry import install_tool_metrics
    from fast_mcp.tool_profiles import apply_profile

    server = FastMCP(_SERVER_NAME)
    _warm_imports()
    install_phases(server)
    # WALKER_MCP_PROFILE recorta la superficie ANTES de que el agente la vea
    # (`tools/list`). Cada tool extra se paga en tokens y en atención en TODOS
    # los turnos; ver fast_mcp/tool_profiles.py.
    apply_profile(server)
    # AE-INV-09: una tool cancelada por el cliente no puede tumbar el proceso.
    # Sin esto, `run_tests` (suite > timeout del cliente) mata el server y deja
    # la sesión sin walker en el momento del cierre.
    install_cancellation_guard()
    # F0 (AGENT-EFFICIENCY §2.7): latencia, tokens y outcome por llamada.
    install_tool_metrics(server)
    # AE-INV-07: spawnear los workers de lectura en background; la primera
    # ráfaga no debería pagar el arranque de cada intérprete.
    from fast_mcp.memories import read_pool

    if read_pool.enabled():
        import threading

        threading.Thread(target=read_pool.warm, name="walker-read-pool-warm",
                         daemon=True).start()
    return server
