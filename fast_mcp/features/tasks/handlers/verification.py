"""handlers/verification.py — registrar la verificación en el TRACE (P1.11).

El plugin certifica el cierre con lo que *observa* de los comandos. En
entornos donde la tool se invoca por fuera de su wrapper (Code Mode, shell del
host, subagente), el `run_tests` verde nunca llega al hook y el gate vuelve a
pedir verificación aunque ya exista (medido: nudge repetido sin cambios de
código).

El TRACE es el SSOT del flight state: `session_context` devuelve el último
`session_state` de la sesión y el plugin lo hidrata. Por eso la tool registra
su resultado acá: el verde existe aunque el plugin no lo haya visto.

Fail-closed: sin `session_id` real no se escribe. Una verificación anónima
marcaría como verde a cualquier sesión del proyecto.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fast_mcp.features.tasks.handlers import _di
from fast_mcp.shared_kernel.project_support.watcher import changed_since_last_scan

__all__ = ["changed_since_last_scan", "record_verification"]

#: `mcp` es el canal, no una sesión: un evento con ese id no es atribuible.
_PLACEHOLDER_SESSIONS = {"", "mcp", "global"}


def record_verification(
    ctx: Any,
    *,
    session_id: str = "",
    passed: bool,
    level: str,
    runner: str = "",
    returncode: int | None = None,
    task_id: str = "",
    touched: list[str] | None = None,
    weak: bool = False,
) -> bool:
    """Escribe un `session_state` con el resultado de la verificación.

    Args:
        ctx: ExecutionContext (solo se usa `chain_dir`).
        session_id: sesión real; vacío/`mcp` ⇒ no se escribe.
        passed: True solo si la verificación fue verde de verdad.
        level: "tool" (run_tests) o "predicates" (verify_predicates).
        runner: comando ejecutado, para que la evidencia sea legible.
        returncode: exit code del runner (si aplica).
        task_id: tarea sobre la que se verificó.
        touched: archivos que cambiaron desde el último scan; evidencia de
            escritura para que el plugin no dependa de ver `edit`/`write`.

    Returns:
        True si el evento quedó escrito.
    """
    sid = str(session_id or "").strip()
    chain_dir = str(getattr(ctx, "chain_dir", "") or "")
    if sid in _PLACEHOLDER_SESSIONS or not chain_dir:
        return False

    now = datetime.now(timezone.utc).isoformat()
    data: dict[str, Any] = {
        "verified": bool(passed),
        "verified_at": now if passed else "",
        "verify_level": level,
        "dirty_since": "",
        "verify_failed": not passed,
        "runner": runner,
        "recorded_by": "task_tool",
    }
    if returncode is not None:
        data["returncode"] = int(returncode)

    if weak:
        # P3c: el verde existe pero se certificó con reglas que el cambio tocó.
        # Es un dato, no un silencio: el sello lo marca `weak`.
        data["weak"] = True
        data["weak_reason"] = "rules_modified_by_change"

    files = [str(f) for f in (touched or []) if f][:20]
    if files:
        # Solo si hay evidencia: sin cambios no se marca `wrote` (el plugin
        # adoptaría una escritura que no ocurrió).
        data["wrote"] = True
        data["writes"] = len(files)
        data["touched"] = files
        data["touched_source"] = "watcher"

    return _di.push_trace(
        chain_dir,
        {
            "event_type": "session_state",
            "source": "walker_task",
            "session_id": sid,
            "task_id": str(task_id or ""),
            "data": data,
        },
    )
