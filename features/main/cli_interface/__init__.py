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
  walker task kg_schema                 → GET  /task/:taskName
  walker kg_search?index=ckg            → GET  /kg_search (query params)
  walker POST react-native-frontend/submit

Si no se especifica verbo, se infiere por la ruta (matcheo por path).

Request explícito:
  main() construye un Request (method, path, params, query, body, flags),
  lo pasa como context al handler a través del Router. Los handlers reciben
  el Request SSOT y devuelven Response (nunca dicts ni ParsedArgs).
"""
from __future__ import annotations

import logging
import sys
from typing import Any

from returns.pipeline import is_successful
from returns.result import safe

from shared.common.router import Router
from features.main.cli_interface.application.handlers import (
    handle_list_projects, handle_status, handle_submit,
    handle_transition, handle_task, handle_help, handle_prepare,
    handle_project_info, handle_mcp_config, handle_mcp_tools, handle_mcp_routes,
)
from features.main.cli_interface.infrastructure.output import format_result
from features.main.cli_interface.infrastructure.contracts import Request, Response, action

logger = logging.getLogger(__name__)

# Verbos HTTP soportados (convención REST clásica)
HTTP_VERBS = {"GET", "POST", "PUT", "PATCH", "DELETE"}


def _as_request(params: dict, flags: dict, context: Any) -> Request:
    """SSOT: convierte (params, flags, context) del Router a un Request.

    Si el Router ya recibió el Request como context (main()), se reutiliza
    y solo se actualizan los params resueltos del path. Si no (tests o
    dispatch directo), se construye uno nuevo.
    """
    if isinstance(context, Request):
        context.params = params
        return context
    return Request(params=params, flags=flags)


# ── Controllers (estilo NestJS: cada dominio registra sus endpoints) ──

def _register_project_controller(router: Router) -> None:
    """GET /status — estado de salud del proyecto (resuelve desde CWD)."""
    router.get(
        ":projectId/:sprintId/status",
        lambda p, f, c: handle_status(_as_request(p, f, c)),
        metadata={"description": "GET /:projectId/:sprintId/status — health + status de un sprint especifico"},
    )
    router.get(
        ":projectId/status",
        lambda p, f, c: handle_status(_as_request(p, f, c)),
        metadata={"description": "GET /:projectId/status — health + workflow status"},
    )
    router.get(
        "status",
        lambda p, f, c: handle_status(_as_request(p, f, c)),
        metadata={"description": "GET /status — project health (resolves from CWD)"},
    )
    router.get(
        ":projectId/info",
        lambda p, f, c: handle_project_info(_as_request(p, f, c)),
        metadata={"description": "GET /:projectId/info — all project info"},
    )
    router.get(
        "info",
        lambda p, f, c: handle_project_info(_as_request(p, f, c)),
        metadata={"description": "GET /info — project info (resolves from CWD)"},
    )


def _register_workflow_controller(router: Router) -> None:
    """POST — mutaciones del workflow (submit / transition / advance)."""
    router.post(
        ":projectId/submit",
        lambda p, f, c: _submit_with_payload(p, f, c),
        metadata={"description": "POST /:projectId/submit {...} — submit data to sub-state"},
    )
    router.post(
        "submit",
        lambda p, f, c: _submit_with_payload(p, f, c),
        metadata={"description": "POST /submit {...} — submit data (resolves from CWD)"},
    )
    router.post(
        ":projectId/transition",
        lambda p, f, c: handle_transition(_as_request(p, f, c)),
        metadata={"description": "POST /:projectId/transition — transition via gate"},
    )
    router.post(
        "transition",
        lambda p, f, c: handle_transition(_as_request(p, f, c)),
        metadata={"description": "POST /transition — transition via gate (resolves from CWD)"},
    )
    router.post(
        ":projectId/advance",
        lambda p, f, c: handle_submit(_as_request(p, f, c)),
        metadata={"description": "POST /:projectId/advance — advance to next sub-state"},
    )
    router.post(
        "advance",
        lambda p, f, c: handle_submit(_as_request(p, f, c)),
        metadata={"description": "POST /advance — advance sub-state (resolves from CWD)"},
    )


def _register_task_controller(router: Router) -> None:
    """GET/POST — ejecución de tasks registradas (body JSON opcional)."""
    # REST: project/task/<name>
    router.any(
        ":projectId/task/:taskName",
        lambda p, f, c: _exec_task(p, f, c),
        metadata={"description": "GET|POST /:projectId/task/:taskName — execute task"},
    )
    # REST directo: project/<taskName>
    router.any(
        ":projectId/:taskName",
        lambda p, f, c: _exec_task(p, f, c),
        metadata={"description": "GET|POST /:projectId/:taskName — execute task directly"},
    )
    # Legacy: project/task --task <name>
    router.any(
        ":projectId/task",
        lambda p, f, c: handle_task(_as_request(p, f, c)),
        metadata={"description": "GET|POST /:projectId/task — task via flags (legacy)"},
    )
    # Sin proyecto: task/<name>
    router.any(
        "task/:taskName",
        lambda p, f, c: _exec_task(p, f, c),
        metadata={"description": "GET|POST /task/:taskName — execute task (resolves from CWD)"},
    )
    # Task directa sin /task/ (catch-all REST: walker <taskName>)
    router.any(
        ":taskName",
        lambda p, f, c: _exec_task(p, f, c),
        metadata={"description": "GET|POST /:taskName — execute task directly"},
    )


def _register_system_controller(router: Router) -> None:
    """GET/POST — comandos del sistema."""
    router.get(
        "list",
        lambda p, f, c: handle_list_projects(_as_request(p, f, c)),
        metadata={"description": "GET /list — list available projects"},
    )
    router.post(
        "prepare",
        lambda p, f, c: handle_prepare(_as_request(p, f, c)),
        metadata={"description": "POST /prepare — init/register project"},
    )
    router.get(
        "help",
        lambda p, f, c: handle_help(_as_request(p, f, c)),
        metadata={"description": "GET /help — show help"},
    )
    router.get(
        "mcp-config",
        lambda p, f, c: handle_mcp_config(_as_request(p, f, c)),
        metadata={"description": "GET /mcp-config — MCP config portable para cualquier agente IA"},
    )
    router.get(
        "mcp-tools",
        lambda p, f, c: handle_mcp_tools(_as_request(p, f, c)),
        metadata={"description": "GET /mcp-tools — list all MCP tools from ModuleRegistry"},
    )
    router.get(
        "mcp-routes",
        lambda p, f, c: handle_mcp_routes(_as_request(p, f, c)),
        metadata={"description": "GET /mcp-routes — list all REST routes from ModuleRegistry"},
    )
    # Autodocumentación de endpoints (estilo /docs de FastAPI)
    router.get(
        "routes",
        lambda p, f, c: _handle_routes(p, f, c),
        metadata={"description": "GET /routes — list all REST endpoints"},
    )


def _build_router() -> Router:
    """Construye el router registrando los controllers (estilo NestJS)."""
    router = Router()
    _register_project_controller(router)
    _register_workflow_controller(router)
    _register_task_controller(router)
    _register_system_controller(router)
    return router


# ── Handlers de soporte (bridges) ──────────────────────────────────

def _handle_routes(params: dict, flags: dict, context: Any = None) -> Response:
    """GET /routes — lista todos los endpoints registrados (autodoc)."""
    routes = [
        {
            "method": "/".join(r["methods"]),
            "pattern": "/" + r["pattern"],
            "description": r["metadata"].get("description", ""),
        }
        for r in _ROUTER.list_routes()
    ]
    return Response.ok(
        "routes_list",
        message=f"{len(routes)} REST endpoint(s) registered",
        data={"routes": routes},
        next_actions=[action("walker help", "see the protocol and available tasks")],
    )


def _exec_task(params: dict, flags: dict, context: Any = None) -> Response:
    """Ejecuta una task por nombre (ruta REST: project/task/<name>).

    El ultimo argumento posicional es el body JSON de la peticion.
    Se pasa como _payload (dict) a la task. Sin flags --index ni --query.

    Si ya hay un --task en flags (legacy), respetarlo.
    Si no, usar :taskName del path REST.
    """
    req = _as_request(params, flags, context)
    # Extraer body JSON ANTES de setear task (el positional puede ser JSON body)
    body = _json_body(req.flags)
    if body:
        req.flags["_payload"] = body
    # Task name: respetar --task legacy o usar :taskName del path
    if not req.flags.get("task"):
        req.flags["task"] = req.params.get("taskName", "")
    return handle_task(req)


def _json_body(flags: dict) -> dict | None:
    """Extrae JSON body del primer positional si tiene formato {..}.

    Return-pattern: devuelve None si no hay body o el JSON es inválido
    (el llamador decide si es error). Nunca traga el fallo silenciosamente.
    """
    positional = flags.get("positional", [])
    if positional and not any(f.startswith("--") for f in positional):
        import json
        non_flag = [a for a in positional if not a.startswith("--") and "--" not in a]
        if non_flag and len(non_flag) == 1 and non_flag[0].startswith("{"):
            parse_outcome = _parse_json_body_core(non_flag[0])
            if is_successful(parse_outcome):
                return parse_outcome.value_or(None)
            error = parse_outcome.failure()
            logger.warning(
                "invalid JSON body: %s (%s: %s)",
                non_flag[0][:80], type(error).__name__, error,
            )
            return None
    return None


@safe
def _parse_json_body_core(raw: str) -> dict:
    """Core puro de _json_body (puede lanzar JSONDecodeError/TypeError)."""
    import json

    return json.loads(raw)


def _submit_with_payload(params: dict, flags: dict, context: Any = None) -> Response:
    """Handle submit con REST body o flags legacy."""
    req = _as_request(params, flags, context)
    body = _json_body(req.flags)
    if body:
        req.flags["_payload"] = body
    return handle_submit(req)


# ── Router singleton ──────────────────────────────────────────────
_ROUTER = _build_router()


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
    """Entry point. Router → handler → output. Todo pasa por el Router."""
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

    # Query params estilo URL: walker kg_search?index=ckg
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
