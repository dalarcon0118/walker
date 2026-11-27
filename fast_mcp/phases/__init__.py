"""fast_mcp.phases — Ensamblado del MCP server por fases de migración.

Cada fase expone un subconjunto de tools de Walker y su propia capa de test.
Todas las fases siguen el mismo contrato de instalación:

    def install(server: FastMCP) -> None: ...

que registra sus tools/resources en el FastMCP server. Cada fase usa los
casos de uso que VIVEN dentro de fast_mcp (`fast_mcp.use_cases`, stdlib
only) — este paquete NO importa nada de `mcp/` (ADR-WALKER-071 §0 #4/#5).

Fases (ADR-WALKER-071):
  - Fase 1 (phase1_prepare): prepare / list / status (autónomos)
  - Fase 2 (phase2_memories): memorias que leen datos y escriben en prepare
                              (CKG/CPG: port a fast_mcp)
  - Fase 3 (phase3_tasks):   tasks de ejecución + submit/transition/help

`install(server)` ensambla las fases activas: `PHASES` permite activarlas de
a una conforme avanza la migración.
"""

from __future__ import annotations

import importlib
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # solo type hint: el modo CLI no debe requerir `mcp`
    from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

# Orden de instalación por fase. Cada entrada: (módulo, enabled).
# `enabled` se va pasando a True conforme cada fase queda migrada y testada.
PHASES = [
    ("fast_mcp.phases.phase1_prepare", True),  # Fase 1: prepare/list/status
    ("fast_mcp.phases.phase2_memories", True),  # Fase 2: memorias CKG/CPG
    ("fast_mcp.phases.phase3_tasks", True),  # Fase 3: tasks + workflow (11 tools — completa)
]


def install(server: FastMCP) -> None:
    """Instala las fases activas en el server (fail-open por fase).

    Si una fase no está migrada aún (module importable pero `enabled=False`)
    se omite sin romper el boot. Result pattern (convention-001): una fase que
    lanza no detiene a las demás — se loggea warning y se sigue.
    """
    for module_name, enabled in PHASES:
        if not enabled:
            logger.info("fast_mcp: fase '%s' desactivada (pendiente de migrar)", module_name)
            continue
        try:
            mod = importlib.import_module(module_name)
            install_fn = getattr(mod, "install", None)
            if callable(install_fn):
                install_fn(server)
                logger.info("fast_mcp: fase '%s' instalada", module_name)
            else:
                logger.warning("fast_mcp: fase '%s' no expone install()", module_name)
        except Exception as exc:  # noqa: BLE001 - fail-open por fase
            logger.warning("fast_mcp: fase '%s' fallo (%s: %s)", module_name, type(exc).__name__, exc)
