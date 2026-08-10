"""mcp_adapter.py — Input adapter: walker as an MCP server.

CLEAN ARCHITECTURE: input adapter (driver) at the same level as the REST
router. Contains NO business logic: it translates MCP tools to REST paths
and delegates to the Router (SSOT of dispatch).

GIT-LIKE BEHAVIOR: like git, the walker operates PER-DIRECTORY. Each
project has its own .cognitive/ (like .git/). The MCP resolves the project
from the process CWD (os.getcwd()) via ProjectService.resolve_from_registry()
— exactly like git finds .git/ walking upward. project_id is OPTIONAL: if
omitted, the current project is detected.

PROTOCOL: MCP tools are the SSOT of capabilities. The CLI is a MCP client
with human formatting. REST endpoints exist only for external integration.

CLI vs MCP API separation:
  - CLI commands (human): prepare, list, help, mcp-tools, mcp-routes, status, submit, transition
  - MCP tools (agent): cpg_analyze, ckg_analyze, agent_context, trace_push, and all domain operations
  - REST endpoints (external integration): /status, /submit, /transition, /prepare, /list, /help
"""

from __future__ import annotations

import json
import logging
import sys

from returns.pipeline import is_successful
from returns.result import safe

from libs.mcp_server import McpServer
from .infrastructure.serializer import dumps
from .infrastructure.reloader import reload_features_if_changed

logger = logging.getLogger(__name__)


# ── Composite server: compone todos los modulos MCP ──


def _build_server() -> McpServer:
    """Construye el servidor MCP compositing todos los modulos.

    Composition root (DIP, ADR-WALKER-016): crea el ToolRegistry del dominio
    del Protocol y lo inyecta en el McpServer (adapter fino de libs). Así el
    schema SSOT de cada tool vive en el dominio; libs no importa features.

    Cada modulo register_module() delega en ModuleRegistry para exponer
    tools y rutas sin acoplar el contenedor.
    """
    from features.cli_interface.domain.services import ToolRegistry
    from features.engine.infrastructure.trace_log import TraceLogger

    registry = ToolRegistry()
    server = McpServer(registry=registry, tracer=TraceLogger())
    modules = [
        "features.cognitive_knowledge.mcp",
        "features.code_analysis.mcp",
        "features.delta_tracking.mcp",
        "features.event_tracing.mcp",
        "features.engine.mcp",
    ]
    for mod in modules:
        outcome = _register_module_core(server, mod)
        if not is_successful(outcome):
            _log_module_error(mod, outcome.failure())

    _register_rest_tools(server)
    _sync_module_registry_to_server(server)
    _register_help_resource(server)
    return server


@safe
def _register_module_core(server: McpServer, mod: str) -> None:
    """Core puro: registra un modulo MCP en el server (puede lanzar)."""
    _load_module(mod)(server)


def _sync_module_registry_to_server(server: McpServer) -> None:
    """Sincroniza el McpServer con el ModuleRegistry.

    Asegura que tools y rutas del registry estén reflejadas en el server.
    """
    outcome = _sync_catalog_core(server)
    if not is_successful(outcome):
        logger.warning(
            "module registry sync skipped (%s: %s)",
            type(outcome.failure()).__name__,
            outcome.failure(),
        )


@safe
def _sync_catalog_core(server: McpServer) -> None:
    """Core puro: sincroniza tools del ModuleRegistry (puede lanzar)."""
    from features.engine.application.tool_catalog import list_tools

    existing = {t.get("name", "") for t in server.list_tools()}
    for tool in list_tools():
        if tool["name"] not in existing:
            server.tool(
                name=tool["name"],
                description=tool.get("description", ""),
                input_schema=tool.get("inputSchema"),
                output_schema=tool.get("outputSchema"),
                handler=_module_tool_handler(tool["name"]),
            )


def _module_tool_handler(name: str):
    def handler(args: dict) -> dict:
        return _call_tool(name, args)

    return handler


def _register_help_resource(server: McpServer) -> None:
    """Registra el recurso dinámico `walker://help/{tool}` (F4 SPEC-043 RF-C1).

    La doc del contrato se SIRVE desde la metadata del registro (tool_catalog
    build_tool_help: description + example + input/output schema) — no desde
    un texto fijo en el adapter. Si la tool no existe, fallback error 404.
    """

    def inner(args: dict) -> dict:
        tool = (args.get("params") or {}).get("tool", "")
        outcome = _build_help_content(tool)
        if is_successful(outcome):
            return outcome.value_or({})
        return {"error": str(outcome.failure())}

    server.resource(
        "walker://help/{tool}",
        "Tool Help",
        "Contract of a walker tool: description, example and input/output schemas (SSOT metadata).",
        handler=inner,
    )


@safe
def _build_help_content(tool: str) -> dict:
    """Core puro: construye el contenido del recurso help (puede lanzar)."""
    from features.engine.application.tool_catalog import build_tool_help

    uri = f"walker://help/{tool}"
    help_doc = build_tool_help(tool)
    if help_doc is None:
        return {"error": f"Resource 'walker://help/{tool}' not found"}
    text = _render_help_text(help_doc)
    return {"contents": [{"uri": uri, "mimeType": "application/json", "text": text}]}


def _render_help_text(help_doc: dict) -> str:
    """Renderiza la doc del contrato como texto plano (description + example + schemas)."""
    lines = [f"# Tool: {help_doc['name']}", ""]
    if help_doc.get("description"):
        lines.append(help_doc["description"])
        lines.append("")
    if help_doc.get("usage"):
        lines.append(f"Usage: {help_doc['usage']}")
        lines.append("")
    if help_doc.get("example"):
        lines.append("Example:")
        lines.append(help_doc["example"])
        lines.append("")
    if help_doc.get("input_schema"):
        lines.append("Input schema:")
        lines.append(json.dumps(help_doc["input_schema"], indent=2))
        lines.append("")
    if help_doc.get("output_schema"):
        lines.append("Output schema:")
        lines.append(json.dumps(help_doc["output_schema"], indent=2))
    return "\n".join(lines)


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
    """Lazy load a module's register and register_module functions."""

    def register(server: McpServer) -> None:
        import importlib

        mod = importlib.import_module(module_name)
        if hasattr(mod, "register"):
            mod.register(server)
        if hasattr(mod, "register_module"):
            mod.register_module(_get_module_registry())

    return register


_SERVER: McpServer | None = None


def _get_server() -> McpServer:
    global _SERVER
    if _SERVER is None:
        _SERVER = _build_server()
    return _SERVER


def _get_tool_catalog() -> list[dict]:
    """Obtiene el catálogo de tools desde el ModuleRegistry.

    Usa el catálogo público para evitar instanciar McpServer en el CLI
    (evita circular imports y acoplamiento innecesario).
    """
    outcome = _tool_catalog_core()
    if is_successful(outcome):
        return outcome.value_or([])
    logger.warning(
        "tool catalog unavailable (%s: %s)",
        type(outcome.failure()).__name__,
        outcome.failure(),
    )
    return []


@safe
def _tool_catalog_core() -> list[dict]:
    """Core puro: lista las tools del ModuleRegistry (puede lanzar)."""
    from features.engine.application.tool_catalog import list_tools

    return list_tools()


# ── Tools metadata ─────────────────────────────────────────────────────────
# El catalogo de tools vive en ModuleRegistry (features/engine/application/tool_catalog.py).
# tools/list consulta el registry sin instanciar McpServer.

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
    "     - cpg_analyze with scope + depth/detect/review/metrics → unified analysis\n"
    "     - kg_search with _index:cpg + file:'<area>'               → symbols, signatures\n"
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
    "5. REUSE what exists: cpg_analyze reveals already-implemented pure\n"
    "   functions and business flows. Reusing them avoids reinventing the wheel.\n"
)


def _load_protocol_text() -> str:
    """Carga el contrato MCP combinando template estático + secciones dinámicas.

    SSOT narrativo: features/prompts/templates/protocol/mcp.md
    Secciones dinámicas: tools disponibles, workflow map, capture_task usage.
    """
    outcome = _protocol_text_core()
    if is_successful(outcome):
        text = outcome.value_or("")
        if text:
            return text
    error = outcome.failure() if not is_successful(outcome) else None
    logger.warning(
        "protocol/mcp dynamic build failed (%s: %s); usando fallback",
        type(error).__name__ if error else "empty",
        error or "empty",
    )
    return _PROTOCOL_FALLBACK


@safe
def _protocol_text_core() -> str:
    """Core puro: construye el protocolo MCP dinámicamente (puede lanzar)."""
    from features.prompts.loader import build_mcp_protocol

    return build_mcp_protocol()


_PROTOCOL_TEXT = _load_protocol_text()


# ── Project resolution (git-like: from CWD) ───────────────────────────────────


def _resolve_project_id(args: dict) -> str:
    """Resolves the project_id: uses args if provided, otherwise detects it
    from the CWD (like git detects .git/)."""
    pid = (args.get("project_id") or "").strip()
    if pid:
        return pid
    outcome = _resolve_project_core()
    if is_successful(outcome):
        return outcome.value_or("")
    error = outcome.failure()
    logger.warning(
        "project resolution failed (%s: %s)",
        type(error).__name__,
        error,
    )
    return ""


@safe
def _resolve_project_core() -> str:
    """Core puro: resuelve project_id desde el CWD (puede lanzar)."""
    from features.project import ProjectService

    resolved = ProjectService.resolve_from_registry()
    if resolved:
        return resolved.get("project_id", "")
    return ""


# ── Tool MCP → REST path translation (SSOT: ModuleRegistry) ───────────

_MODULE_REGISTRY: Any = None


def _get_module_registry() -> Any:
    global _MODULE_REGISTRY
    if _MODULE_REGISTRY is None:
        outcome = _bootstrap_modules_core()
        if is_successful(outcome):
            _MODULE_REGISTRY = outcome.value_or({})
        else:
            logger.warning(
                "module registry bootstrap failed (%s: %s); usando vacio",
                type(outcome.failure()).__name__,
                outcome.failure(),
            )
            _MODULE_REGISTRY = {}
    return _MODULE_REGISTRY


@safe
def _bootstrap_modules_core():
    """Core puro: bootstrapea los modulos (puede lanzar)."""
    from features.engine.bootstrap import bootstrap_modules

    return bootstrap_modules()


def _call_tool(name: str, args: dict) -> dict:
    """Translates an MCP tool to a REST path and delegates to the Router (SSOT).

    Consulta ModuleRegistry para resolver tools y rutas. Si no está registrado,
    fallback a task pública dinámica (descubrimiento automático).
    """
    registry = _get_module_registry()
    tool_def = registry.tool(name) if registry else None
    route_def = registry.route(name) if registry else None

    if tool_def and route_def:
        project_id = _resolve_project_id(args)
        path = (
            route_def.path.format(project_id=project_id)
            if "{project_id}" in route_def.path
            else route_def.path
        )
        flags = (
            route_def.flags_builder(args)
            if route_def.flags_builder
            else {"_payload": args.get("body", args)}
        )
        from . import _ROUTER

        result = _ROUTER.dispatch(path, flags, method=route_def.method or "POST")
        if hasattr(result, "to_dict"):
            return result.to_dict()
        if isinstance(result, dict):
            return result
        return {"type": "ok", "data": {"result": result}}

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


# ── JSON-RPC 2.0 handler ────────────────────────────────────────────────────

# Sesiones MCP del dominio del Protocol (ADR-016): repo in-memory compartido.
from features.cli_interface.infrastructure.session_store import (
    InMemorySessionRepository,
)

_SESSIONS = InMemorySessionRepository()


def _handle_request(msg: dict) -> dict:
    method = msg.get("method", "")
    params = msg.get("params", {}) or {}
    msg_id = msg.get("id")

    if method == "initialize":
        from features.cli_interface.contracts.rpc import JsonRpcError

        client_info = params.get("clientInfo", {}) or {}
        client_name = client_info.get("name", "unknown")
        client_version = client_info.get("version", "")

        # ── Sesión del dominio del Protocol (ADR-016) ──
        from features.cli_interface.domain.entities import McpSession

        session, sess_errors = McpSession.create(
            params.get("protocolVersion", "2024-11-05")
        )
        if sess_errors:
            return JsonRpcError(
                id=msg_id, code=-32602, message="; ".join(sess_errors)
            ).to_dict()
        init_errors = session.initialize(
            client_name=client_name, client_version=client_version
        )
        if init_errors:
            return JsonRpcError(
                id=msg_id, code=-32602, message="; ".join(init_errors)
            ).to_dict()
        _SESSIONS.save(session)

        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": session.protocol_version.value,
                "capabilities": {
                    "tools": {"listChanged": False},
                    "resources": {"subscribe": False},
                    "prompts": {},
                },
                "serverInfo": {"name": "walker", "version": "3.1"},
                "sessionId": session.session_id.value,
            },
        }
    if method == "tools/list":
        tools = _get_tool_catalog()
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": tools}}
    if method == "tools/call":
        name = params.get("name", "")
        args = params.get("arguments", {}) or {}
        outcome = _call_tool_core(name, args)

        if is_successful(outcome):
            result = outcome.value_or({})
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": dumps(result)}],
                    "isError": result.get("status_code", 200) >= 400,
                },
            }
        error = outcome.failure()
        logger.error(
            "tools/call '%s' fallo (%s: %s)", name, type(error).__name__, error
        )
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "content": [{"type": "text", "text": str(error)}],
                "isError": True,
            },
        }
    if method == "resources/list":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"resources": _get_server().list_resources()},
        }
    if method == "resources/read":
        uri = params.get("uri", "")
        outcome = _read_resource_core(uri)

        if is_successful(outcome):
            return {"jsonrpc": "2.0", "id": msg_id, "result": outcome.value_or({})}
        error = outcome.failure()
        logger.error(
            "resources/read '%s' fallo (%s: %s)",
            uri,
            type(error).__name__,
            error,
        )
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "contents": [{"uri": uri, "mimeType": "text/plain", "text": str(error)}]
            },
        }
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

    # ── Método no soportado → taxonomía del dominio (ADR-016) ──
    from features.cli_interface.domain.errors import UnsupportedMethodError
    from features.cli_interface.contracts.rpc import JsonRpcError

    err = UnsupportedMethodError(method)
    return JsonRpcError(id=msg_id, code=-32601, message=str(err)).to_dict()


def _is_notification(msg: dict) -> bool:
    """JSON-RPC 2.0: un mensaje SIN 'id' es una notificación y NO debe
    recibir respuesta (p. ej. 'notifications/initialized'). Responderla
    rompe el handshake MCP (opencode desincroniza y tools/list queda vacío)."""
    return "id" not in msg or msg.get("id") is None


@safe
def _call_tool_core(name: str, args: dict) -> dict:
    """Core puro: invoca un tool MCP (puede lanzar)."""
    return _get_server().call_tool(name, args)


@safe
def _read_resource_core(uri: str) -> dict:
    """Core puro: lee un recurso MCP (puede lanzar)."""
    return _get_server().read_resource(uri)


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
        reload_outcome = _reload_features_core(on_changed=_reset_server_cache)
        if not is_successful(reload_outcome):
            # Recarga best-effort: si falla, se loggea y se continua con
            # los modulos actuales (no romper el loop por un reload).
            logger.warning(
                "Recarga de codigo fallo (best-effort): %s", reload_outcome.failure()
            )
        parse_outcome = _parse_request_core(line)
        if is_successful(parse_outcome):
            msg, resp = parse_outcome.value_or(({}, {}))
        else:
            error = parse_outcome.failure()
            if isinstance(error, json.JSONDecodeError):
                resp = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": "Parse error"},
                }
            else:
                logger.warning(
                    "request handling fallo (%s: %s)",
                    type(error).__name__,
                    error,
                )
                resp = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32603, "message": f"Internal error: {error}"},
                }
            msg = {}
        if _is_notification(msg):
            continue
        sys.stdout.write(dumps(resp) + "\n")
        sys.stdout.flush()
    return 0


@safe
def _reload_features_core(on_changed=None) -> bool:
    """Core puro: recarga modulos de features/ si cambiaron (puede lanzar)."""
    return reload_features_if_changed(on_changed=on_changed)


@safe
def _parse_request_core(line: str) -> tuple[dict, dict]:
    """Core puro: parsea y despacha un mensaje JSON-RPC (puede lanzar)."""
    msg = json.loads(line)
    return msg, _handle_request(msg)


def main() -> int:
    """MCP adapter entry point (driver). No business logic."""
    return serve_stdio()


if __name__ == "__main__":
    sys.exit(main())
