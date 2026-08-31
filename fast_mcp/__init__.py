"""fast_mcp — MCP server AUTÓNOMO de Walker construido sobre FastMCP.

Migración por fases desde `mcp/` (fuente transitoria) hacia aquí: los casos
de uso migrados VIVEN dentro de `fast_mcp/` (§0 #4 del ADR-WALKER-071), no
se llaman de forma remota. Este paquete NO importa nada de `mcp/` (ni app/,
features/, shared/, protocols/) y NO pone `mcp/` en sys.path (§0 #5) — solo
usa stdlib + la lib oficial `mcp.server.fastmcp` + `fast_mcp.*`.

Fases (cada una con su capa de test):

  - Fase 1 (phase1_prepare): prepare / list / status — implementados en
    `fast_mcp.use_cases` (workflow core: bootstrap y lectura de proyectos).
  - Fase 2 (phase2_memories): memorias que leen datos y escriben en prepare
                              (CKG/CPG — port a fast_mcp).
  - Fase 3 (phase3_tasks):   tasks de ejecución + submit/transition/help.

Al cierre, `mcp/` se elimina por completo y `fast_mcp/` queda como única
superficie sirviendo el dominio.
"""

from __future__ import annotations
