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

Salida: JSON del resultado. Las tools de verificación (`run_tests`,
`verify_predicates`) agregan una línea final `WALKER_RESULT ...` como contrato
de certificación (SPEC-WALKER-044), para no depender de un JSON truncable.
Exit code 1 si el resultado trae `error`, 2 si el invocante está mal (tool
desconocida, JSON inválido, proyecto no registrado).
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any, Callable


def _error(message: str, code: int = 2) -> int:
    print(json.dumps({"error": message}, ensure_ascii=False))
    return code


#: Tools cuya salida certifica un cierre. Su resultado viaja con un marcador
#: final estable para que el plugin no dependa de parsear un JSON truncable.
_VERIFICATION_TOOLS = ("run_tests", "verify_predicates")


def _verification_marker(tool: str, result: Any) -> str:
    """Línea final `WALKER_RESULT ...` para las tools de verificación.

    El JSON sigue siendo la primera línea (detalle); el marcador es el contrato
    de certificación. Va al final para sobrevivir a previews/truncados que
    conservan la cola, y su ausencia significa "no certificable".
    """
    if tool not in _VERIFICATION_TOOLS or not isinstance(result, dict):
        return ""
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    ok = bool(result.get("ok")) and not result.get("error")

    if tool == "run_tests":
        ok = ok and data.get("passed") is not False
        passed = int(data.get("total") or 0) if data.get("passed") else 0
        failed = int(data.get("failures") or 0)
        errors = int(data.get("errors") or 0)
        rc = data.get("returncode")
        exit_code = int(rc) if rc is not None else (0 if ok else 1)
        return (
            f"WALKER_RESULT tool=run_tests ok={int(ok)} passed={passed} "
            f"failed={failed} errors={errors} exit={exit_code}"
        )

    total = int(data.get("total") or 0)
    passed = int(data.get("passed_count") or 0)
    failed = int(data.get("failed_count") or 0)
    ok = ok and bool(data.get("all_satisfied")) and failed == 0
    return (
        f"WALKER_RESULT tool=verify_predicates ok={int(ok)} passed={passed} "
        f"failed={failed} errors=0 total={total} exit={0 if ok else 1}"
    )


def _runner_for(tool: str) -> Callable[..., Any] | None:
    """Runner cableado por composition para la tool pedida."""
    from fast_mcp import composition
    from fast_mcp.phases.phase1_prepare import _FASE1_TOOLS
    from fast_mcp.phases.phase2_memories import _FASE2_TOOLS
    from fast_mcp.phases.phase3_tasks import _FASE3_TOOLS, _build_runner

    # Fase 1 (prepare/list/status) existe como superficie MCP y estaba fuera
    # del CLI: el propio plugin le dice al usuario `walker3 _/prepare …` y el
    # comando respondía "tool desconocida". El uso documentado tiene que existir.
    if tool in _FASE1_TOOLS:
        return composition.project_tool_runner(tool)
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

    # Fail-fast: un project_id que no está en el registro hacía que
    # `resolve_paths` cayera al repo de walker, así que un typo leía/escribía
    # estado y baselines DEL PROYECTO EQUIVOCADO (medido 2026-11-17: un
    # snapshot de 'cyclo-probe' se guardó en walker). `_` es el comodín que usa
    # el plugin para las tools que no necesitan proyecto.
    if project_id != "_":
        # Un id desconocido hacía que `resolve_paths` cayera al repo de walker,
        # así que un typo leía/escribía estado y baselines DEL PROYECTO
        # EQUIVOCADO (medido: un snapshot de 'cyclo-probe' se guardó en walker).
        # Se detecta comparando el id resuelto con el pedido.
        try:
            from fast_mcp.shared_kernel.project_support import ProjectService

            resolved = getattr(ProjectService.resolve_paths(project_id), "project_id", "")
            if resolved and resolved != project_id:
                return _error(
                    f"proyecto no registrado: {project_id!r} (se resolvía a {resolved!r})"
                )
        except Exception:
            pass  # sin contexto de proyecto no se bloquea la tool

    # El project_id de la spec es el SSOT; cualquier project_id en el payload se
    # ignora para que el plugin no pueda apuntar a otro proyecto por accidente.
    payload.pop("project_id", None)
    payload.pop("_project_id", None)

    # El CLI es el canal humano: marcar _human=True para que el walker permita
    # enviar `decision` en pasos user_input (el agente vía MCP no puede).
    payload["_human"] = True

    # La telemetría por tool también cubre el CLI: el plugin llama `brief`,
    # `run_tests`, `submit`, etc. por acá, no por el MCP. Sin esto el score no
    # ve la sesión y "ToolInvoked" queda sin session_id (medido 2026-11-26).
    telemetry_session = str(payload.get("session_id") or payload.get("_session_id") or "")
    telemetry_task = str(payload.get("task_id") or payload.get("_task_id") or "")
    started = time.perf_counter()
    try:
        result = runner(project_id=project_id, **payload)
    except Exception as exc:  # noqa: BLE001 — el CLI nunca deja un traceback crudo
        try:
            from fast_mcp.shared_kernel.telemetry.tool_metrics import record_exception

            record_exception(
                tool=tool,
                latency_ms=int((time.perf_counter() - started) * 1000),
                error=exc,
                project_id=project_id,
                session_id=telemetry_session,
                task_id=telemetry_task,
            )
        except Exception:  # noqa: BLE001 — observabilidad, no el resultado
            pass
        return _error(f"{type(exc).__name__}: {exc}", code=1)
    try:
        from fast_mcp.shared_kernel.telemetry.tool_metrics import record_call

        record_call(
            tool=tool,
            latency_ms=int((time.perf_counter() - started) * 1000),
            result=result,
            project_id=project_id,
            session_id=telemetry_session,
            task_id=telemetry_task,
        )
    except Exception:  # noqa: BLE001 — observabilidad, no el resultado
        pass

    print(json.dumps(result, ensure_ascii=False, default=str))
    marker = _verification_marker(tool, result)
    if marker:
        print(marker)
    if isinstance(result, dict) and result.get("error"):
        return 1
    return 0
