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


def _persist_evidence_node(
    chain_dir: str,
    *,
    session_id: str,
    task_id: str,
    passed: bool,
    level: str,
    runner: str,
    returncode: int | None,
    weak: bool,
    counts: dict[str, Any] | None,
    files: list[str],
) -> str:
    """Nodo de evidencia de verificación en el CKG (SPEC-021).

    Un verde sin artefacto es una afirmación. El nodo queda ligado a la sesión
    por `metadata.session_id`, que es el join que usa `session_context`.

    Returns:
        El id del nodo si se guardó; "" si el CKG no está disponible o el nodo
        no entró. El trace ya testifica la verificación, así que el nodo es un
        extra consultable: no se rompe la tool por no poder escribirlo.
    """
    extra = counts or {}
    verdict = "verde" if passed else "rojo"
    node_id = f"verification:{session_id}:{level}"
    statement = f"{runner or level} {verdict}"
    if returncode is not None:
        statement += f" (rc={returncode})"
    if extra.get("total"):
        statement += f" · {extra.get('passed', 0)}/{extra['total']}"
    node = {
        "id": node_id,
        "type": "verification",
        "label": f"verificación {level}: {verdict}",
        "statement": statement,
        "metadata": {
            "kind": "verification",
            "runner": runner,
            "level": level,
            "verified": bool(passed),
            "returncode": returncode,
            "weak": bool(weak),
            **{k: extra[k] for k in ("passed", "failed", "total") if k in extra},
            "files": files[:20],
        },
    }
    try:
        # R1: `tasks` no importa `cognitive_knowledge`; el writer lo inyecta el
        # composition root (`configure_knowledge_writer`). Si no está, el nodo
        # no se escribe y se declara vacío.
        saved = _di.save_knowledge(
            chain_dir, [node], str(task_id or ""), session_id
        )
    except Exception:  # noqa: BLE001 — el trace es la evidencia primaria
        return ""
    if not isinstance(saved, dict):
        return ""
    if saved.get("ok") is False or saved.get("error"):
        return ""
    return node_id if saved.get("nodes_saved") else ""


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
    counts: dict[str, Any] | None = None,
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

    # SPEC-021: la verificación es un ARTEFACTO, no sólo un evento del trace.
    # Queda como nodo del CKG ligado por `metadata.session_id` (el mismo join
    # que usa `session_context`), con el runner y los contadores. Si el CKG no
    # está disponible, el nodo no se escribe y se declara: nunca se finge.
    node_id = _persist_evidence_node(
        chain_dir,
        session_id=sid,
        task_id=task_id,
        passed=bool(passed),
        level=level,
        runner=runner,
        returncode=returncode,
        weak=weak,
        counts=counts,
        files=files,
    )
    data["evidence_node"] = node_id

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
