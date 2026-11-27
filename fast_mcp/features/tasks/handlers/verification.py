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

__all__ = ["changed_since_last_scan", "record_verification", "manifest_is_stale"]


def _git_head(chain_dir: str) -> str:
    """SHA corto de HEAD (o '' si no hay git). No rompe la verificación."""
    try:
        import subprocess

        # `check_output`: los tests de `run_tests` mockean `subprocess.run` para
        # capturar el argv del runner. Usar `run` acá lo sobrescribía y rompía
        # el assert del argv. `check_output` no está mockeado.
        proc = subprocess.Popen(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=chain_dir or ".",
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        out, _ = proc.communicate(timeout=5)
        if proc.returncode == 0:
            return (out or "").strip()
        return 
    except Exception:  # noqa: BLE001
        return ""

#: `mcp` es el canal, no una sesión: un evento con ese id no es atribuible.
_PLACEHOLDER_SESSIONS = {"", "mcp", "global"}


def manifest_is_stale(
    manifest: Any,
    current_commit: Any = None,
    current_revision: Any = None,
) -> bool:
    """Detecta una verificación stale (otro commit/revisión)."""
    if not isinstance(manifest, dict) or not manifest:
        return True
    m_commit = str(manifest.get("commit") or manifest.get("revision") or "").strip()
    m_revision = str(manifest.get("revision") or manifest.get("commit") or "").strip()
    if not m_commit:
        return True

    if isinstance(current_commit, dict):
        current_commit = (
            current_commit.get("commit")
            or current_commit.get("revision")
            or current_commit.get("current_commit")
        )
    if isinstance(current_revision, dict):
        current_revision = (
            current_revision.get("revision")
            or current_revision.get("commit")
            or current_revision.get("current_revision")
        )

    if current_commit is None and current_revision is None:
        return True
    if current_commit is not None:
        cc = str(current_commit).strip()
        if cc and cc != m_commit:
            return True
    if current_revision is not None:
        cr = str(current_revision).strip()
        if cr and cr != m_revision:
            return True
    return False


def _git_head(chain_dir: str) -> str:
    try:
        import subprocess

        # `check_output`: los tests de `run_tests` mockean `subprocess.run` para
        # capturar el argv del runner. Usar `run` acá lo sobrescribía y rompía
        # el assert del argv. `check_output` no está mockeado.
        proc = subprocess.Popen(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=chain_dir or ".",
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        out, _ = proc.communicate(timeout=5)
        if proc.returncode == 0:
            return (out or "").strip()
        return 
    except Exception:  # noqa: BLE001
        return ""


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
    manifest: dict[str, Any] | None = None,
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
            "manifest": manifest or {},
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


def _reseal_stamp(
    chain_dir: str,
    session_id: str,
    *,
    passed: bool,
    level: str,
    weak: bool,
    when: str,
) -> bool:
    """Actualiza el sello de una sesión ACTIVA con la verificación recién hecha.

    El sello se calcula al cerrar; si la sesión se reabre y verifica después, el
    sello viejo dice `none` y el artefacto miente. Se actualiza por el puerto de
    `_di` (tasks no importa delta_tracking, R1) y es fail-soft: el trace ya
    testifica; si el sello no se puede reescribir, no se rompe la verificación.
    """
    try:
        service = _di.sprint_service(chain_dir)
    except Exception:  # noqa: BLE001
        return False
    if service is None or not hasattr(service, "stamp_session"):
        return False
    try:
        record = service.get_session(session_id) or {}
        plan = service.get_session_plan(session_id) or {}
        previous = record.get("stamp") or {}
        kind = "predicates" if level == "predicates" else "tests"
        stamp = {
            "kind": kind if passed else "none",
            "verified": bool(passed),
            "level": level,
            "weak": bool(weak),
            "criteria": len(plan.get("acceptance") or []),
            "rounds": int(previous.get("rounds") or 0),
            "source": "task_tool",
            "at": when,
        }
        return bool(service.stamp_session(session_id, stamp))
    except Exception:  # noqa: BLE001 — el sello es un extra del cierre
        return False


def build_manifest(
    *,
    passed: bool,
    level: str,
    runner: str = "",
    task_id: str = "",
    touched: list[str] | None = None,
    weak: bool = False,
    counts: dict[str, Any] | None = None,
    results: list[Any] | None = None,
    commit: str = "",
    revision: str = "",
    when: str = "",
) -> dict[str, Any]:
    """Manifest de verificación (F1c): commit/revisión/resultados.

    Puro: no I/O. `commit`/`revision` vacíos se completan por el caller
    (record_verification los resuelve con git HEAD).
    """
    if not commit:
        commit = _git_head("")  # fallback vacío; el caller resuelve con chain_dir
    if not revision:
        revision = commit
    if not when:
        from datetime import datetime, timezone

        when = datetime.now(timezone.utc).isoformat()
    files = [str(f) for f in (touched or []) if f][:20]
    return {
        "commit": commit,
        "revision": revision,
        "all_passed": bool(passed),
        "level": level,
        "runner": runner,
        "task_id": str(task_id or ""),
        "results": list(results or []),
        "counts": counts or {},
        "weak": bool(weak),
        "files": files,
        "at": when,
    }


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
    commit: str = "",
    revision: str = "",
    results: list[Any] | None = None,
    manifest: dict[str, Any] | None = None,
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

    # F1c: manifest de verificación (commit/revision/resultados). Se persiste
    # en el nodo de evidencia y en el trace, para detectar verificaciones stale
    # de otro commit/revisión.
    if not commit:
        commit = _git_head(chain_dir)
    if not revision:
        revision = commit
    manifest = build_manifest(
        passed=bool(passed),
        level=level,
        runner=runner,
        task_id=str(task_id or ""),
        touched=touched or [],
        weak=bool(weak),
        counts=counts or {},
        results=list(results or []),
        commit=commit,
        revision=revision,
        when=now,
    )
    data["manifest"] = manifest

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
        manifest=manifest,
    )
    data["evidence_node"] = node_id
    # Re-sellado: la evidencia recién registrada es la que vale para el cierre.
    _reseal_stamp(
        chain_dir,
        sid,
        passed=bool(passed),
        level=level,
        weak=bool(weak),
        when=now,
    )

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
