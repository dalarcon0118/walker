"""mcp.py — Driver del adaptador MCP (entry point limpio).

ARQUITECTURA LIMPIA: entry point del adaptador de entrada MCP.
Se invoca como `python -m features.cli_interface.mcp` — NO hay scripts/.

El driver solo arranca el loop stdio JSON-RPC; toda la logica vive
en mcp_adapter (adaptador) que delega en el Router (SSOT).

Uso:
    python -m features.cli_interface.mcp          # servidor stdio
    python -m features.cli_interface.mcp --self-test
"""
from __future__ import annotations

import sys

from .mcp_adapter import _handle_request, serve_stdio


def _self_test() -> int:
    """Verifica el protocolo MCP sin abrir stdio (modo test)."""
    init = _handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    tools = _handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    print("walker MCP (via Router) self-test OK")
    print("  server:", init["result"]["serverInfo"])
    print("  tools:", [t["name"] for t in tools["result"]["tools"]])
    return 0


def main() -> int:
    if "--self-test" in sys.argv:
        return _self_test()
    return serve_stdio()


if __name__ == "__main__":
    sys.exit(main())
