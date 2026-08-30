"""features/main/cli_interface/router.py — Router REST tipo NestJS (submódulo).

Extraído de features/main/cli_interface/__init__.py para romper el ciclo de
composición: mcp_adapter importaba `from . import _ROUTER` (nodo del paquete
protocols.cli.cli_interface), cerrando el ciclo
pkg -> handlers -> mcp_client -> mcp_adapter -> pkg.

Ahora mcp_adapter importa `_ROUTER` desde ESTE submódulo
(protocols.cli.cli_interface.router), no desde el __init__ del paquete. El
router importa handlers (submódulo) y los controllers; NO importa el __init__
del paquete, así que no hay arista router -> pkg.
"""
from __future__ import annotations

import logging
from typing import Any

from returns.pipeline import is_successful
from returns.result import safe

from shared.common.router import Router
from protocols.cli.cli_interface.application.handlers import (
    handle_list_projects, handle_status, handle_submit,
    handle_transition, handle_task, handle_help, handle_prepare,
    handle_project_info, handle_mcp_config, handle_mcp_tools, handle_mcp_routes,
)
from protocols.cli.cli_interface.infrastructure.contracts import Request, Response, action

logger = logging.getLogger(__name__)


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
