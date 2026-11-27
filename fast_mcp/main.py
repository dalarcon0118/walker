"""fast_mcp.main — Entry point del nuevo MCP server (FastMCP) AUTÓNOMO.

Construye el FastMCP server (ver `fast_mcp.server.build_server`) y lo pone a
correr sobre el transporte stdio (agentes locales) o Streamable HTTP.

- `build_server()` construye el server (assembly de fases + health tool).
- `run(transport="stdio")` corre el server (entry point standalone).
- `main()` = `run()` con parse simple de args (stdout del entry point).

Este módulo NO depende del transporte legacy `mcp/protocols/*` (ADR-071 §0
#5): es el entry-point autónomo del nuevo server.

Para correr:

    python -m fast_mcp.main
    bin/walker-mcp        # → fast_mcp.main.run
"""

from __future__ import annotations

import logging
import sys

logger = logging.getLogger(__name__)


def run(transport: str = "stdio") -> int:
    """Construye el server y corre el loop del transporte indicado.

    Returns:
        int: código de salida del proceso (0 = ok).
    """
    from fast_mcp.server import build_server

    server = build_server()
    server.run(transport=transport)
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point CLI.

    Dos modos:
      - `<project>/<tool> [json]` → ejecuta la tool y imprime JSON (plugins).
      - `--transport {stdio,http}` o sin args → arranca el server MCP.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and "/" in argv[0] and not argv[0].startswith("-"):
        from fast_mcp.cli import run_task

        return run_task(argv)
    transport = "stdio"
    if argv and argv[0] == "--transport":
        transport = argv[1] if len(argv) > 1 else "stdio"
    return run(transport=transport)


if __name__ == "__main__":
    sys.exit(main())
