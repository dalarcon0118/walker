"""fast_mcp/cli.py — protocolo CLI que usan los plugins del agente.

El plugin de OpenCode invoca `bin/walker3 <project>/<tool> <json>` (ADR-WALKER-016:
"el hook usa el CLI, no python inline"). Hasta ahora `bin/walker3` ignoraba los
argumentos y arrancaba el server MCP por stdio: con stdin vacío salía rc=0 sin
imprimir nada, así que TODAS las llamadas del plugin (trace, gates, CKG,
contexto) eran no-ops silenciosos.

Este módulo implementa ese contrato: misma resolución de proyecto y los mismos
runners que registra el server, en proceso, imprimiendo un JSON por stdout.

    walker3 walker/help '{}'
    walker3 backend-microservices/recall_knowledge '{"index":"ckg","query":"ADR"}'

Salida: JSON del resultado. Exit code 1 si el resultado trae `error`, 2 si el
invocante está mal (tool desconocida, JSON inválido, proyecto no registrado).
"""

from __future__ import annotations

import json
import sys
from typing import Any, Callable


def _error(message: str, code: int = 2) -> int:
    print(json.dumps({"error": message}, ensure_ascii=False))
    return code


def _runner_for(tool: str) -> Callable[..., Any] | None:
    """Runner cableado por composition para la tool pedida."""
    from fast_mcp import composition
    from fast_mcp.phases.phase2_memories import _FASE2_TOOLS
    from fast_mcp.phases.phase3_tasks import _FASE3_TOOLS, _build_runner

    if tool in _FASE2_TOOLS:
        return composition.memory_tool_runner(tool)
    if tool in _FASE3_TOOLS:
        return _build_runner(tool)
    return None


def run_task(argv: list[str]) -> int:
    """`<project>/<tool> [json]` → resultado JSON por stdout."""
    if not argv or "/" not in argv[0]:
        return _error(
            "uso: walker3 <project>/<tool> '<json>' (ej: walker/help '{}')"
        )
    project_id, _, tool = argv[0].partition("/")
    project_id, tool = project_id.strip(), tool.strip()
    if not project_id or not tool:
        return _error(f"spec inválida: {argv[0]!r} (esperado <project>/<tool>)")

    raw = argv[1] if len(argv) > 1 else ""
    if not raw.strip() and not sys.stdin.isatty():
        try:
            raw = sys.stdin.read()
        except (OSError, ValueError):
            raw = ""
    payload: dict[str, Any] = {}
    if raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            return _error(f"JSON inválido: {exc}")
        if not isinstance(parsed, dict):
            payload = {"value": parsed}
        else:
            payload = parsed

    runner = _runner_for(tool)
    if runner is None:
        return _error(f"tool desconocida: {tool!r}")

    # El project_id de la spec es el SSOT; cualquier project_id en el payload se
    # ignora para que el plugin no pueda apuntar a otro proyecto por accidente.
    payload.pop("project_id", None)
    payload.pop("_project_id", None)

    try:
        result = runner(project_id=project_id, **payload)
    except Exception as exc:  # noqa: BLE001 — el CLI nunca deja un traceback crudo
        return _error(f"{type(exc).__name__}: {exc}", code=1)

    print(json.dumps(result, ensure_ascii=False, default=str))
    if isinstance(result, dict) and result.get("error"):
        return 1
    return 0
