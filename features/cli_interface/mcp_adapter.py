"""mcp_adapter.py — Input adapter: walker as an MCP server.

CLEAN ARCHITECTURE: input adapter (driver) at the same level as the REST
router. Contains NO business logic: it translates MCP tools to REST paths
and delegates to the Router (SSOT of dispatch).

GIT-LIKE BEHAVIOR: like git, the walker operates PER-DIRECTORY. Each
project has its own .cognitive/ (like .git/). The MCP resolves the project
from the process CWD (os.getcwd()) via ProjectService.resolve_from_registry()
— exactly like git finds .git/ walking upward. project_id is OPTIONAL: if
omitted, the current project is detected.

```
  MCP client (agent)              <- tools in context (push)
       | JSON-RPC stdio
       v
  [mcp_adapter]  <- adapter (translates tool -> REST path; resolves CWD)
       |
       v
  [Router]  <- SSOT of dispatch (same pipeline as the CLI)
       |
       v
  [handlers] -> [use cases] -> [domain]
```
"""

from __future__ import annotations

import json
import os
import sys

from libs.mcp_server import McpServer
from .infrastructure.serializer import dumps
from .infrastructure.reloader import reload_features_if_changed


# ── Composite server: compone todos los modulos MCP ──

def _build_server() -> McpServer:
    """Construye el servidor MCP compositing todos los modulos.

    Cada modulo register() devuelve (count, errors) via return-pattern.
    Los errores se loguean — no se tragan silenciosamente.
    """
    server = McpServer()
    modules = [
        "features.cognitive_knowledge.mcp",
        "features.code_analysis.mcp",
        "features.delta_tracking.mcp",
        "features.event_tracing.mcp",
        "features.engine.mcp",
    ]
    for mod in modules:
        try:
            result = _load_module(mod)(server)
        except Exception as e:
            _log_module_error(mod, e)
            continue
        if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], list):
            count, errors = result
            if errors:
                _log_module_errors(mod, count, errors)

    # ── Tools REST del workflow (F7, ADR-016) ──
    # status/submit/transition/prepare/list/help son endpoints REST del
    # adapter (mapeados en _TOOL_ROUTES → Router → handlers), NO tasks del
    # registry. Se registran aquí con _call_tool para que el MCP los exponga
    # con el dispatch correcto (antes engine/mcp.py los mandaba al TaskRegistry
    # y fallaban con "task no registrada").
    _register_rest_tools(server)
    return server


def _register_rest_tools(server: McpServer) -> None:
    """Registra los tools REST del workflow con dispatch via _call_tool."""
    rest_tools = [
        (
            "status",
            "Project workflow state + memory stats: node, sub_state, instructions, gates, CPG/CKG/Delta counts.",
        ),
        (
            "submit",
            "Send data to current sub-state. ADVANCES the workflow. Body: node fields.",
        ),
        (
            "transition",
            "Transition through a gate (admin/emergency).",
        ),
        ("prepare", "Initialize project at path (creates .cognitive/)."),
        ("list", "List projects managed by walker."),
        ("help", "Walker protocol and available tasks."),
    ]
    for name, desc in rest_tools:
        server.tool(
            name=name,
            description=desc,
            handler=_call_tool_handler(name),
        )


def _call_tool_handler(name: str):
    def handler(args: dict) -> dict:
        return _call_tool(name, args)
    return handler


def _log_module_error(mod: str, err: Exception) -> None:
    import sys
    print(f"[MCP] module '{mod}' registration failed: {err}", file=sys.stderr)


def _log_module_errors(mod: str, count: int, errors: list[str]) -> None:
    import sys
    for e in errors:
        print(f"[MCP] module '{mod}' ({count} tools): {e}", file=sys.stderr)


def _load_module(module_name: str):
    """Lazy load a module's register function."""
    def register(server: McpServer) -> None:
        import importlib
        mod = importlib.import_module(module_name)
        mod.register(server)
    return register


_SERVER: McpServer | None = None


def _get_server() -> McpServer:
    global _SERVER
    if _SERVER is None:
        _SERVER = _build_server()
    return _SERVER


# ── Tools metadata ─────────────────────────────────────────────────────────
# El catalogo _TOOLS/_RESOURCES fue eliminado (ADR-WALKER-016): el schema SSOT
# de cada tool vive en McpServer/ToolRegistry (registrado por cada feature
# via server.tool()). tools/list lee _get_server().list_tools() — unica fuente.

_PROMPTS = [
    {
        "name": "workflow-protocol",
        "description": "Walker REST protocol: how to advance, what to query, when submit vs transition",
    },
]

_PROTOCOL_FALLBACK = (
    "WALKER PROTOCOL (MCP) — MANDATORY BEHAVIOR CONTRACT:\n"
    "\n"
    "1. GIT-LIKE: the walker operates on the current directory. Each project\n"
    "   has its own .cognitive/. prepare(path) initializes, list() shows managed.\n"
    "\n"
    "2. TO ADVANCE in the workflow, use submit with the node's data.\n"
    "   If the node is a decision (XOR), use submit with 'intent'.\n"
    "\n"
    "3. CODE ANALYSIS = USE THE WALKER CPG (MANDATORY):\n"
    "   The walker has a STRUCTURED CODE MEMORY (CPG) with a query layer.\n"
    "   Do NOT reason about code from scratch NOR read files directly.\n"
    "   BEFORE analyzing/implementing/verifying, query the project CPG:\n"
    "     - kg_search with _index:cpg + file:'<area>'  → symbols, signatures, imports\n"
    "     - cpg_business with file:'<area>'            → pure functions, flows, patterns\n"
    "     - analyze_code with area:'<area>'            → full code analysis\n"
    "   The CPG is the source of truth for the code (real names, real signatures).\n"
    "   Use what the CPG returns to build solutions; do NOT invent APIs.\n"
    "\n"
    "4. KNOWLEDGE MEMORY (CKG): query ckg before deciding patterns and\n"
    "   conventions. Use kg_store to REGISTER decisions, conventions and\n"
    "   patterns. ALWAYS persist in the project where you are actually\n"
    "   working: if you develop walker code, store in project 'walker';\n"
    "   if you validate another project, store there. Do not mix contexts.\n"
    "   (Workflow decisions are captured automatically by capture_decision.)\n"
    "\n"
    "5. REUSE what exists: cpg_business reveals already-implemented pure\n"
    "   functions and business flows. Reusing them avoids reinventing the wheel.\n"
    "\n"
    "6. SESSION WORK LOG (MANDATORY): when you COMPLETE a piece of work\n"
    "   (implemented a feature, fixed a bug, refactored, indexed, etc.),\n"
    "   PERSIST it with capture_task so it is not lost:\n"
    '     - walker <project>/capture_task \'{"objective":"...","domain":"...",\n'
    '         "files":["path/to/file.py"],"result_state":"..."}\'\n'
    "   This stores the task under an INFORMAL SPRING by date\n"
    "   (session-YYYYMMDD) that the DeltaGraph indexes as historical\n"
    "   context. The agent's work never gets lost, even without a formal\n"
    "   spring. The formal way remains plan_sprint + the workflow.\n"
    "   DECISIONS: if the work involved an architecture decision, INCLUDE it\n"
    "   in the same capture_task payload:\n"
    '     - walker <project>/capture_task \'{"objective":"...",\n'
    '         "decisions":[{"statement":"ADR-x: ...","rationale":"...",\n'
    '           "supersedes":["<prev statement>"]}]}\'\n'
    "   capture_task registers it in the CKG automatically (with lifecycle).\n"
    "   AUTO-DETECTION: if you do NOT report decisions but the 'files' touch\n"
    "   architecture layers (contracts, domain, workflow, config, engine,\n"
    "   repositories, providers), capture_task detects it and generates the\n"
    "   decision automatically using your objective as context.\n"
)


def _load_protocol_text() -> str:
    """Carga el contrato MCP desde el template canónico (SSOT).

    SSOT: features/prompts/templates/protocol/mcp.md (mismo path único de
    templates que el resto de prompts). Fallback: constante del código.
    """
    try:
        from features.prompts.loader import load_static
        text = load_static("protocol/mcp")
        return text or _PROTOCOL_FALLBACK
    except Exception as e:
        import sys

        print(f"[MCP] protocol/mcp load failed (using fallback): {e}", file=sys.stderr)
        return _PROTOCOL_FALLBACK


_PROTOCOL_TEXT = _load_protocol_text()


# ── Project resolution (git-like: from CWD) ───────────────────────────────────


def _resolve_project_id(args: dict) -> str:
    """Resolves the project_id: uses args if provided, otherwise detects it
    from the CWD (like git detects .git/)."""
    pid = (args.get("project_id") or "").strip()
    if pid:
        return pid
    try:
        from features.project import ProjectService

        resolved = ProjectService.resolve_from_registry()
        if resolved:
            return resolved.get("project_id", "")
    except Exception as e:
        import sys

        print(f"[MCP] project resolution failed: {e}", file=sys.stderr)
    return ""


# ── Tool MCP → REST path translation (SSOT: the Router dispatches) ───────────

_TOOL_ROUTES = {
    "prepare": ("prepare", lambda a: {"path": a.get("path", os.getcwd())}),
    "list": ("list", lambda a: {}),
    "status": ("{project_id}/status", lambda a: {}),
    "submit": ("{project_id}/submit", lambda a: {"_payload": a.get("body", {})}),
    "transition": (
        "{project_id}/transition",
        lambda a: {"gate": a.get("gate", ""), "positional": [a.get("gate", "")]},
    ),
    "kg_search": ("{project_id}/kg_search", lambda a: {"_payload": a.get("query", {})}),
    "kg_multi": ("{project_id}/kg_multi", lambda a: {"_payload": a.get("layers", {})}),
    "kg_store": ("{project_id}/kg_store", lambda a: {"_payload": a.get("body", {})}),
    "cpg_business": (
        "{project_id}/cpg_business",
        lambda a: {"_payload": {"file": a.get("file", "")}},
    ),
    "cpg_unused_functions": (
        "{project_id}/cpg_unused_functions",
        lambda a: {},
    ),
    "project_metrics": (
        "{project_id}/project_metrics",
        lambda a: {},
    ),
    "analyze_code": (
        "{project_id}/analyze_code",
        lambda a: {"_payload": {"area": a.get("area", "")}},
    ),
    "help": ("help", lambda a: {}),
}


def _call_tool(name: str, args: dict) -> dict:
    """Translates an MCP tool to a REST path and delegates to the Router (SSOT).

    'analyze_code' is a COMPOSITE tool: it has no REST handler of its own;
    it combines CPG queries (kg_search + cpg_business) to deliver a full
    code analysis.
    """
    if name == "analyze_code":
        return _analyze_code(args)

    # Dispatch estandar: tool -> ruta REST mapeada en _TOOL_ROUTES.
    # FALLBACK (fix descubrimiento de tasks publicas): si la tool no esta
    # en el mapa (ej: cpg_unused_functions, project_metrics, kg_extract),
    # se despacha como task publica del proyecto via {project_id}/<nombre>.
    # Asi el MCP expone todas las tasks publicas del registro sin hardcodear.
    if name not in _TOOL_ROUTES:
        project_id = _resolve_project_id(args)
        path = f"{project_id}/{name}"
        flags = {"_payload": args.get("body", {})}
        from . import _ROUTER

        result = _ROUTER.dispatch(path, flags, method="POST")
        if hasattr(result, "to_dict"):
            return result.to_dict()
        if isinstance(result, dict):
            return result
        return {"type": "ok", "data": {"result": result}}

    route_tpl, flags_builder = _TOOL_ROUTES[name]
    project_id = _resolve_project_id(args)
    path = (
        route_tpl.format(project_id=project_id)
        if "{project_id}" in route_tpl
        else route_tpl
    )
    flags = flags_builder(args)

    from . import _ROUTER

    method = None
    if path.endswith(("submit", "transition")):
        method = "POST"
    elif path == "prepare":
        method = "POST"
    result = _ROUTER.dispatch(path, flags, method=method)
    if hasattr(result, "to_dict"):
        return result.to_dict()
    if isinstance(result, dict):
        return result
    return {"type": "ok", "data": {"result": result}}


def _analyze_code(args: dict) -> dict:
    """Analyzes code by querying the project CPG (query layer).

    Combines:
      1. kg_search cpg by area  -> symbols, signatures, imports
      2. cpg_business by area   -> pure functions, flows, patterns
    """
    project_id = _resolve_project_id(args)
    area = (args.get("area") or "").strip()
    if not area:
        return {
            "type": "error",
            "status_code": 400,
            "message": "analyze_code requiere 'area' (substring del archivo)",
        }

    # 1. CPG symbols
    sym_resp = _call_tool(
        "kg_search",
        {
            "project_id": project_id,
            "query": {"_index": "cpg", "file": area},
        },
    )
    symbols = sym_resp.get("data", {}).get("result", {}) if sym_resp.get("data") else {}
    hits = symbols.get("hits", {}).get("hits", []) if isinstance(symbols, dict) else []

    # 2. Business layer
    biz_resp = _call_tool(
        "cpg_business",
        {
            "project_id": project_id,
            "file": area,
        },
    )
    biz = biz_resp.get("data", {}).get("result", {}) if biz_resp.get("data") else {}

    # Summarize symbols (avoid huge responses)
    files = {}
    functions = []
    imports = []
    for h in hits[:80]:
        src = h.get("_source", {})
        name = src.get("name", "")
        ftype = src.get("type", "")
        sig = src.get("signature", "")
        fpath = src.get("file", "")
        if ftype == "file":
            files[fpath] = fpath
        elif ftype == "module":
            imports.append({"name": name, "file": fpath, "signature": sig})
        elif ftype in (
            "function",
            "constant",
            "method",
            "interface",
            "struct",
            "class",
        ):
            functions.append(
                {
                    "name": name,
                    "type": ftype,
                    "file": fpath,
                    "line": src.get("line", 0),
                    "signature": sig,
                }
            )

    return {
        "type": "analyze_code",
        "status_code": 200,
        "message": f"Code analysis for '{area}' — {len(functions)} symbols, {len(imports)} imports",
        "data": {
            "area": area,
            "project_id": project_id,
            "files": sorted(files.keys()),
            "symbols": functions[:60],
            "imports": imports[:20],
            "business_layer": {
                "pure_functions": (biz.get("pure_functions") or [])[:15],
                "business_flows": (biz.get("business_flows") or [])[:8],
                "architecture_patterns": (biz.get("architecture_patterns") or [])[:8],
            },
        },
        "next_actions": [
            {
                "command": f"walker {project_id}/kg_search",
                "reason": "more specific CPG query",
            },
            {
                "command": f"walker {project_id}/cpg_business",
                "reason": "reuse functions/patterns of the area",
            },
        ],
    }


def _read_resource(uri: str) -> dict:
    """Reads a walker resource (context injectable to the agent)."""
    if uri.startswith("walker://status/"):
        project_id = uri.split("/")[-1]
        result = _call_tool("status", {"project_id": project_id})
        return {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": "application/json",
                    "text": dumps(result),
                }
            ]
        }
    return {
        "contents": [
            {
                "uri": uri,
                "mimeType": "application/json",
                "text": dumps({"error": "resource no encontrado"}),
            }
        ]
    }


# ── JSON-RPC 2.0 handler ────────────────────────────────────────────────────


def _handle_request(msg: dict) -> dict:
    method = msg.get("method", "")
    params = msg.get("params", {}) or {}
    msg_id = msg.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {"listChanged": False},
                    "resources": {"subscribe": False},
                    "prompts": {},
                },
                "serverInfo": {"name": "walker", "version": "3.1"},
            },
        }
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": _get_server().list_tools()}}
    if method == "tools/call":
        name = params.get("name", "")
        args = params.get("arguments", {}) or {}
        try:
            result = _get_server().call_tool(name, args)
            return {
                "jsonrpc": "2.0", "id": msg_id,
                "result": {"content": [{"type": "text", "text": dumps(result)}],
                           "isError": result.get("status_code", 200) >= 400},
            }
        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"content": [{"type": "text", "text": str(e)}], "isError": True},
            }
    if method == "resources/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"resources": _get_server().list_resources()}}
    if method == "resources/read":
        uri = params.get("uri", "")
        try:
            result = _get_server().read_resource(uri)
            return {"jsonrpc": "2.0", "id": msg_id, "result": result}
        except Exception as e:
            return {
                "jsonrpc": "2.0", "id": msg_id,
                "result": {"contents": [{"uri": uri, "mimeType": "text/plain", "text": str(e)}]}}
    if method == "prompts/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"prompts": _PROMPTS}}
    if method == "prompts/get":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "description": "Workflow protocol",
                "messages": [
                    {
                        "role": "user",
                        "content": {"type": "text", "text": _PROTOCOL_TEXT},
                    }
                ],
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}

    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32601, "message": f"Metodo '{method}' no soportado"},
    }


def _is_notification(msg: dict) -> bool:
    """JSON-RPC 2.0: un mensaje SIN 'id' es una notificación y NO debe
    recibir respuesta (p. ej. 'notifications/initialized'). Responderla
    rompe el handshake MCP (opencode desincroniza y tools/list queda vacío)."""
    return "id" not in msg or msg.get("id") is None


# ── Recarga de codigo (fix estructural 'server stale') ──────────────────────
# El server MCP cargaba los modulos de features/ una vez al arrancar. Cualquier
# fix en disco (engine, handlers) quedaba inactivo hasta reiniciar el proceso,
# perdiendo la conexion MCP de la sesion. Este mecanismo recarga los modulos
# cuando su source cambia, sin reiniciar. Implementado en
# infrastructure/reloader.py (F4 — thin adapters).


def serve_stdio() -> int:
    """stdio JSON-RPC loop: reads requests line by line, responds.

    RECARGA DE CODIGO (fix estructural 'server stale'): entre request y
    request se verifica si los modulos de features/ cambiaron en disco.
    Si cambian, se recargan via importlib.reload para que el server tome
    los fixes SIN reiniciar el proceso ni perder la conexion MCP.

    Las NOTIFICACIONES (sin id) se procesan pero NO se responden — el
    protocolo MCP las usa como señales one-way (initialized, cancelled).
    """
    def _reset_server_cache() -> None:
        global _SERVER
        _SERVER = None

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            reload_features_if_changed(on_changed=_reset_server_cache)
        except Exception as _reload_err:
            # Recarga best-effort: si falla, se loggea y se continua con
            # los modulos actuales (no romper el loop por un reload).
            import logging

            logging.getLogger(__name__).warning(
                "Recarga de codigo fallo (best-effort): %s", _reload_err
            )
        try:
            msg = json.loads(line)
            resp = _handle_request(msg)
        except json.JSONDecodeError:
            resp = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error"},
            }
        if _is_notification(msg):
            continue
        sys.stdout.write(dumps(resp) + "\n")
        sys.stdout.flush()
    return 0


def main() -> int:
    """MCP adapter entry point (driver). No business logic."""
    return serve_stdio()


if __name__ == "__main__":
    sys.exit(main())
