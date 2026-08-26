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
import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
_WALKER_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_SCRIPT_DIR)))
if _WALKER_ROOT not in sys.path:
    sys.path.insert(0, _WALKER_ROOT)

from returns.pipeline import is_successful
from returns.result import safe

from shared.core.mcp_server import McpServer
from .infrastructure.serializer import dumps
from .infrastructure.reloader import reload_features_if_changed

logger = logging.getLogger(__name__)


# ── Composite server: compone todos los modulos MCP ──


def _build_server() -> McpServer:
    """Construye el servidor MCP desde el composition root único.

    ADR-045 (capa_rout.md D1): NO registra módulos hardcodeados en paralelo
    al ModuleRegistry. consume AppContainer.build() — el composition root final de
    features/main registra MCP modules + libs services + tasks + side effects
    y puebla el service locator neutro de shared/core/engine. El server se sincroniza
    con el catálogo fusionado (ModuleRegistry + TaskRegistry, ADR-043).
    """
    from shared.core.engine.infrastructure.trace_log import TraceLogger
    from features.main.container import AppContainer
    from features.main.cli_interface.domain.services import ToolRegistry

    # Composition root obligatorio (D1): puebla el service locator de
    # shared/core/engine y registra todos los módulos MCP. Sin esto, list_tools()
    # y _call_tool() resuelven servicios inexistentes y el server crashea
    # (error MCP -32000 Connection closed).
    AppContainer.build()

    registry = ToolRegistry()
    server = McpServer(registry=registry, tracer=TraceLogger())

    # SSOT (ADR-043): las tools del server se pueblan DESDE el ModuleRegistry +
    # TaskRegistry (fusion via _sync_catalog_core) — no hay registro paralelo
    # con descripciones/schemas re-escritos. (Antiguo _register_rest_tools
    # duplicaba status/submit/transition/prepare/list/help con desc genérica
    # y sin output_schema: drift real entre lo que ve el agente y el dispatch).
    _sync_module_registry_to_server(server)
    _register_help_resource(server)
    return server


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
    from shared.core.engine.application.tool_catalog import list_tools
    from features.main.container import AppContainer

    app = AppContainer.build()
    registry = app.module_registry if app else None
    existing = {t.get("name", "") for t in server.list_tools()}
    for tool in list_tools(registry=registry):
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
    from shared.core.engine.application.tool_catalog import build_tool_help

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


def _log_module_error(mod: str, err: Exception) -> None:
    import sys

    print(f"[MCP] module '{mod}' registration failed: {err}", file=sys.stderr)


def _log_module_errors(mod: str, count: int, errors: list[str]) -> None:
    import sys

    for e in errors:
        print(f"[MCP] module '{mod}' ({count} tools): {e}", file=sys.stderr)


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
        return _ensure_project_id_discoverable(outcome.value_or([]))
    logger.warning(
        "tool catalog unavailable (%s: %s)",
        type(outcome.failure()).__name__,
        outcome.failure(),
    )
    return []


_PROJECT_ID_PARAM = {
    "type": "string",
    "description": (
        "ID del proyecto del registry de walker sobre el que operar "
        "(ej. 'react-native-frontend'). ENVIALO SIEMPRE que trabajas en un "
        "proyecto distinto al cwd del server — si lo omitís, la tool se "
        "resuelve contra el proyecto del directorio de arranque."
    ),
}


def _ensure_project_id_discoverable(tools: list[dict]) -> list[dict]:
    """Inyecta `project_id` opcional en el schema de TODAS las tools MCP.

    Lección descubribilidad (fix build_code_index): un parámetro que el
    agente no ve en el schema es un parámetro muerto. El dispatch YA leía
    args.project_id / args._project_id (_resolve_project_id) para ambos
    caminos A y B — lo que faltaba era DECLARARLO para que los clientes
    MCP puedan enviarlo. Idempotente: no toca schemas que ya lo exponen.
    """
    for tool in tools:
        schema = tool.setdefault("inputSchema", {}) or {}
        props = schema.setdefault("properties", {})
        if "project_id" not in props and "_project_id" not in props:
            props["project_id"] = dict(_PROJECT_ID_PARAM)
        required = schema.get("required")
        if isinstance(required, list) and "project_id" in required:
            required.remove("project_id")
    return tools


@safe
def _tool_catalog_core() -> list[dict]:
    """Core puro: lista las tools del ModuleRegistry (puede lanzar).

    Composition root obligatorio (ADR-045, capa_rout.md D1): tools/list es
    la PRIMERA request MCP del handshake — antes de listar, el composition
    root (features/main.compose_app) debe haber poblado el service locator de
    shared/core/engine. Sin esto, list_tools() resuelve servicios inexistentes y el
    server queda sin tools (error MCP -32000 Connection closed).
    """
    from shared.core.engine.application.tool_catalog import list_tools
    from features.main.container import AppContainer

    app = AppContainer.build()
    registry = app.module_registry if app else None
    return list_tools(registry=registry)


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
    "     - inspect_codebase with scope + depth/detect/review/metrics → unified analysis\n"
    "     - recall_knowledge with _index:cpg + file:'<area>'               → symbols, signatures\n"
    "   The CPG is the source of truth for the code (real names, real signatures).\n"
    "   Use what the CPG returns to build solutions; do NOT invent APIs.\n"
    "\n"
    "4. KNOWLEDGE MEMORY (CKG): query ckg before deciding patterns and\n"
    "   conventions. Use documenting_knowledge to REGISTER decisions, conventions and\n"
    "   patterns. ALWAYS persist in the project where you are actually\n"
    "   working: if you develop walker code, store in project 'walker';\n"
    "   if you validate another project, store there. Do not mix contexts.\n"
    "   (Workflow decisions are captured automatically by capture_decision.)\n"
    "\n"
    "5. REUSE what exists: inspect_codebase reveals already-implemented pure\n"
    "   functions and business flows. Reusing them avoids reinventing the wheel.\n"
    "\n"
    "6. PHASE INTELLIGENCE TOOLS (MANDATORY per workflow phase):\n"
    "   Use these tools to query structured knowledge at each phase:\n"
    "     - task_context   → file/task context before editing (IMPLEMENT phase)\n"
    "     - diagnose_failure → failure diagnosis with conventions (REFINE phase)\n"
    "     - verify_evidence → verification coverage (VERIFY phase)\n"
    "     - sprint_evidence → sprint planning evidence (SPRINT.PLANNING phase)\n"
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
    from features.main.prompts.loader import build_mcp_protocol

    return build_mcp_protocol()


_PROTOCOL_TEXT: str | None = None


def _get_protocol_text() -> str:
    """Resuelve el contrato MCP LAZY (al primer prompts/get).

    ADR-045 (capa_rout.md D1): el protocolo incluye las tools vivas del
    composition root (list_tools). Si se calculara en import-time, el
    service locator de shared/core/engine aún no está poblado y el catálogo queda
    vacío. Se resuelve per-request, tras tools/list (que compone la app).
    """
    global _PROTOCOL_TEXT
    if _PROTOCOL_TEXT is None:
        _PROTOCOL_TEXT = _load_protocol_text()
    return _PROTOCOL_TEXT


# ── Project resolution (git-like: from CWD) ───────────────────────────────────


def _resolve_project_id(args: dict) -> str:
    """Resolves the project_id: uses args if provided, otherwise detects it
    from the CWD (like git detects .git/).

    SOPORTA AMBOS ALIAS (SSOT de convención): los tools del agente reciben
    `_project_id` (underscore — el formato que leen las tasks kg/ckg/delta y
    el que usa el MCP adapter en el dispatch Camino A/B), mientras que los
    handlers REST/CLI leen `project_id`. ANTES solo se leía `project_id`: un
    agente que llamara documenting_knowledge/recall_knowledge desde un CWD que no es el
    proyecto (pasando `_project_id`) caía SILENCIOSAMENTE al fallback
    projects_list (status 200) — el dispatch resolvía la tool equivocada.
    """
    pid = (args.get("project_id") or args.get("_project_id") or "").strip()
    if pid:
        return pid
    # Override explícito del proyecto activo (ADR-WALKER-018): si el cliente
    # MCP definió WALKER_PROJECT_ID, respetalo aunque el CWD del server no
    # coincida (servers persistentes entre proyectos). Reusa el mecanismo ya
    # usado por walker-session.js. El CWD sigue siendo el fallback git-like.
    env_pid = (os.environ.get("WALKER_PROJECT_ID") or "").strip()
    if env_pid:
        return env_pid
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


# ── Tool MCP → REST path translation (SSOT: features/main) ───────────

def _get_module_registry() -> Any:
    """Devuelve el ModuleRegistry canónico de la app.

    ADR-045 (capa_rout.md D1): features/main compone el ModuleRegistry
    completo (discovery de MCP modules, libs services, tasks, etc.).
    El adapter consume el composition root via AppContainer (DI directa,
    ADR-WALKER-058).
    """
    from features.main.container import AppContainer

    app = AppContainer.build()
    if app is not None and app.module_registry is not None:
        return app.module_registry
    raise RuntimeError(
        "ModuleRegistry no disponible: AppContainer.build() falló. "
        "Asegúrate de que features.main.compose_app() se ejecutó."
    )


def _dispatch_result_to_dict(result) -> dict:
    """Normaliza el resultado del dispatcher a un dict JSON-safe.

    El Router devuelve Response (con to_dict) o dict; cualquier otro tipo se
    envuelve en {type: ok}. DRY: ambos caminos de _call_tool comparten esta
    conversión (antes duplicada 2×).
    """
    if hasattr(result, "to_dict"):
        return result.to_dict()
    if isinstance(result, dict):
        return result
    return {"type": "ok", "data": {"result": result}}


def _call_tool(name: str, args: dict) -> dict:
    """Translates an MCP tool to a REST path and delegates to the Router (SSOT).

    DISPATCH EN DOS CAMINOS (capa_rout.md D2 — ARQ objetivo: "tool → route →
    flags_builder → handler; fallback system_registry.execute"). Ambos caminos
    convergen en _ROUTER.dispatch (el dispatcher único de la app); la
    bifurcación es SOLO en la construcción de path/flags, que es inherente a
    las dos categorías de tools:

    Camino A — tool con route_def en el ModuleRegistry (status, submit,
    transition, prepare, list, help): usa la metadata declarada del route
    (path template + flags_builder + method). El handler es el del controller
    REST (handle_status/handle_submit/...).

    Camino B — fallback de task pública dinámica (ej. tasks que ningún
    módulo expone con ruta): construye el path por convención
    "{project_id}/{name}" y el Router lo resuelve vía ":projectId/:taskName"
    (o el catch-all ":taskName" cuando no hay proyecto) → _exec_task →
    handle_task → execute_public_task (el core compartido con el CLI,
    SPEC-005 #5). Es el "fallback system_registry.execute" del ARQ: cualquier
    task pública del registro es invocable por nombre aunque ningún mcp.py la
    declare.

    EDGE conocido (preexistente): el fallback despacha contra las rutas del
    Router, así que una task cuyo nombre colisionara con una ruta de
    controller (status, info, list, ...) matchearía ESA ruta REST en vez de
    handle_task. En la práctica no ocurre: esas tasks tienen route_def
    (Camino A) y los nombres públicos reales son kg_*/scan_*/build_*. Si el
    edge molestara, la alternativa es despachar Camino B directo a
    dispatch_public_task (como hacen los mcp.py de los módulos), que elimina
    la colisión por completo.

    NO ELIMINAR la bifurcación: es el diseño del ARQ (ruta declarada primero,
    fallback dinámico después). Forzar Camino B por route_def exigiría
    duplicar el catálogo de tasks en RestRoutes; y pasar el fallback fuera del
    Router rompería el principio "todo pasa por el Router" (cli_interface).
    """
    registry = _get_module_registry()
    tool_def = registry.tool(name) if registry else None
    route_def = registry.route(name) if registry else None

    if tool_def and route_def:
        # ── Camino A: tool con ruta declarada (metadata SSOT del route) ──
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
        # Canal de invocación (SPEC-002 G-W1): la tool vino por MCP, no por CLI.
        # ANTES el dispatch pasaba por handle_task con channel="cli" hardcodeado
        # y las tools con route_def (recall_knowledge, documenting_knowledge, ...) trazaban el
        # canal equivocado en el TraceGraph. El handler lee `_channel`.
        flags["_channel"] = "mcp"
        from . import _ROUTER

        result = _ROUTER.dispatch(path, flags, method=route_def.method or "POST")
        return _dispatch_result_to_dict(result)

    # ── Camino B: fallback task pública dinámica (sin ruta declarada) ──
    # El Router resuelve ":projectId/:taskName" → handle_task →
    # execute_public_task (mismo core que el CLI, SPEC-005 #5).
    project_id = _resolve_project_id(args)
    path = f"{project_id}/{name}"
    flags = {"_payload": args.get("body", args)}
    flags["_channel"] = "mcp"
    from . import _ROUTER

    result = _ROUTER.dispatch(path, flags, method="POST")
    return _dispatch_result_to_dict(result)


# ── JSON-RPC 2.0 handler ────────────────────────────────────────────────────

# Sesiones MCP del dominio del Protocol (ADR-016): repo in-memory compartido.
from features.main.cli_interface.infrastructure.session_store import (
    InMemorySessionRepository,
)

_SESSIONS = InMemorySessionRepository()


def _handle_request(msg: dict) -> dict:
    method = msg.get("method", "")
    params = msg.get("params", {}) or {}
    msg_id = msg.get("id")

    if method == "initialize":
        from features.main.cli_interface.contracts.rpc import JsonRpcError

        client_info = params.get("clientInfo", {}) or {}
        client_name = client_info.get("name", "unknown")
        client_version = client_info.get("version", "")

        # ── Sesión del dominio del Protocol (ADR-016) ──
        from features.main.cli_interface.domain.entities import McpSession

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
                        "content": {"type": "text", "text": _get_protocol_text()},
                    }
                ],
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}

    # ── Método no soportado → taxonomía del dominio (ADR-016) ──
    from features.main.cli_interface.domain.errors import UnsupportedMethodError
    from features.main.cli_interface.contracts.rpc import JsonRpcError

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


def _reset_server_cache() -> None:
    """Invalida caches del server tras una cascada de hot-reload.

    Extraído de serve_stdio (closure → módulo) para poder testear el
    contrato de recomposición eager sin levantar el loop stdio.
    """
    global _SERVER
    _SERVER = None
    # El ModuleRegistry (composition root D1) también se invalida: si un
    # fix agrega/cambia tools o servicios DI, tools/list las refleja sin
    # reiniciar el proceso (mismo patrón que el server stale).
    from features.main.bootstrap import reset_composition

    reset_composition()
    # Recomposicion EAGER post-reload (fix -32001): sin esto, la primera
    # tarea tras una cascada pagaba AppContainer.build() completo DENTRO
    # de su presupuesto de respuesta (handlers.handle_task reconstruye
    # cuando el locator esta vacio) → timeout del cliente. Recomponer
    # aqui, fuera del request, deja el locator listo para el proximo
    # dispatch. Best-effort: si falla, handle_task reintenta por su cuenta.
    try:
        from features.main.container import AppContainer

        AppContainer.build()
    except Exception as exc:
        logger.warning(
            "Recomposicion post-reload fallo (best-effort, "
            "handle_task reintentara): %s",
            exc,
        )


def serve_stdio() -> int:
    """stdio JSON-RPC loop: reads requests line by line, responds.

    RECARGA DE CODIGO (fix estructural 'server stale'): entre request y
    request se verifica si los modulos de features/ cambiaron en disco.
    Si cambian, se recargan via importlib.reload para que el server tome
    los fixes SIN reiniciar el proceso ni perder la conexion MCP.
    Desde el fix de cooldown/budget (reloader.py), las cascadas se coalescen:
    ráfagas de ediciones producen UNA cascada por ventana, nunca por request.

    Las NOTIFICACIONES (sin id) se procesan pero NO se responden — el
    protocolo MCP las usa como señales one-way (initialized, cancelled).
    """

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
