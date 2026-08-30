"""
features/cli_interface/ — Walker CLI con Router tipo REST (Express/NestJS style).

Flujo: router → handler → output
  shared/common/router/router.py       → matchea path + verbo HTTP + extrae parametros
  infra/contracts.py          → Request (input SSOT) y Response (output SSOT)
  app/handlers.py             → recibe Request → Response
  infra/output.py             → Response → print() / exit code

REST routing:
  walker GET status                     → GET  /status
  walker POST submit '{"field":"x"}'    → POST /submit  (body JSON)
  walker react-native-frontend/status   → GET  /:projectId/status
  walker task memory_schema           → GET  /task/:taskName
  walker recall_knowledge?index=ckg   → GET  /task/recall_knowledge (query params)
  walker POST react-native-frontend/submit

Si no se especifica verbo, se infiere por la ruta (matcheo por path).

Request explícito:
  main() construye un Request (method, path, params, query, body, flags),
  lo pasa como context al handler a través del Router. Los handlers reciben
  el Request SSOT y devuelven Response (nunca dicts ni ParsedArgs).

El Router (y los controllers/bridges) viven en features/main/cli_interface/router.py
para romper el ciclo de composición cli_interface <-> mcp_adapter.
"""
from __future__ import annotations

import logging
import sys
from typing import Any

from protocols.cli.cli_interface.application.handlers import handle_help
from protocols.cli.cli_interface.infrastructure.output import format_result
from protocols.cli.cli_interface.infrastructure.contracts import Request
from protocols.cli.cli_interface.router import _ROUTER

logger = logging.getLogger(__name__)

# Verbos HTTP soportados (convención REST clásica)
HTTP_VERBS = {"GET", "POST", "PUT", "PATCH", "DELETE"}


def _parse_flags(args: list[str]) -> dict[str, Any]:
    """Convierte args tipo --key value a dict."""
    flags = {}
    i = 0
    while i < len(args):
        if args[i].startswith("--"):
            key = args[i][2:]
            if i + 1 < len(args) and not args[i + 1].startswith("--"):
                flags[key] = args[i + 1]
                i += 2
            else:
                flags[key] = True
                i += 1
        else:
            i += 1
    return flags


def _parse_query_string(path: str) -> tuple[str, dict[str, str]]:
    """Extrae query params estilo URL: path?key=value&k2=v2."""
    if "?" not in path:
        return path, {}
    path_part, qs = path.split("?", 1)
    query = {}
    for kv in qs.split("&"):
        if "=" in kv:
            k, v = kv.split("=", 1)
            query[k] = v
    return path_part, query


def main(args: list[str] | None = None) -> int:
    """Entry point. Router → handler → output. Todo pasa por el Router.

    Población del holder MCP (rompe el ciclo handlers -> mcp_client ->
    mcp_adapter -> pkg): en CLI el holder contiene un MCPClient; en
    server-mode lo popula protocols.mcp.mcp_adapter._get_server con el
    McpServer (misma API list_tools/call_tool que usan los handlers).
    """
    try:
        from protocols.cli.cli_interface.application.mcp_client import get_mcp_client

        get_mcp_client()
    except Exception:
        pass

    if args is None:
        args = sys.argv[1:]

    if not args:
        return format_result(handle_help(Request()))

    # Verbo HTTP explícito opcional: walker GET status / POST submit {...}
    method = None
    if args[0].upper() in HTTP_VERBS:
        method = args[0].upper()
        args = args[1:]
        if not args:
            return format_result(handle_help(Request()))

    raw_path = args[0]
    flag_args = args[1:]

    # Query params estilo URL: walker recall_knowledge?index=ckg
    raw_path, query_flags = _parse_query_string(raw_path)

    path = raw_path.strip("/")

    # Normalización REST: "walker task <name>" → path "task/<name>"
    if path == "task" and flag_args and not flag_args[0].startswith("--"):
        path = f"task/{flag_args[0]}"
        flag_args = flag_args[1:]

    flags = _parse_flags(flag_args)
    flags["positional"] = [a for a in flag_args if not a.startswith("--")]
    flags.update(query_flags)  # query params tienen prioridad

    # Request explícito (SSOT del input). Se pasa como context al handler.
    request = Request(method=method or "GET", path=path, query=query_flags, flags=flags)

    handler, params, resolved_flags, matched = _ROUTER.resolve(path, flags, method)
    if handler is None:
        print(f"  No route matched: {method + ' ' if method else ''}/{path}")
        print("  Run 'walker routes' to list all REST endpoints")
        return 2

    request.params = params
    result = handler(request.params, resolved_flags, request)
    return format_result(result)
