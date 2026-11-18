"""side_effects/_context.py — resolución común de sprint/sesión/spec.

Los efectos reciben `kwargs` del motor (`ctx`, `state`, `fields`) y `params`
ya resueltos. Este módulo centraliza la resolución para que los 12 efectos no
repitan (ni diverjan en) la misma lógica.

No se registra como efecto: el descubrimiento ignora los módulos `_*`.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def state_context(kwargs: dict[str, Any]) -> dict[str, Any]:
    state = kwargs.get("state") or kwargs.get("workflow_state")
    if state is None:
        return {}
    ctx = state.get("context", {}) if isinstance(state, dict) else getattr(state, "context", {})
    return ctx if isinstance(ctx, dict) else {}


def session_id_of(params: dict[str, Any], kwargs: dict[str, Any]) -> str:
    ctx = kwargs.get("ctx")
    sid = str(getattr(ctx, "session_id", "") or "") if ctx is not None else ""
    return sid or str(params.get("session_id") or "")


def task_id_of(params: dict[str, Any], kwargs: dict[str, Any]) -> str:
    ctx = kwargs.get("ctx")
    tid = str(getattr(ctx, "task_id", "") or "") if ctx is not None else ""
    if tid:
        return tid
    ctx_data = state_context(kwargs)
    return str(
        params.get("task_id")
        or params.get("spec_id")
        or kwargs.get("task_id")
        or kwargs.get("spec_id")
        or ctx_data.get("task_id")
        or ctx_data.get("spec_id")
        or ""
    )


def sprint_service(chain_dir: str, params: dict[str, Any], kwargs: dict[str, Any]) -> Any:
    """Servicio de sprint: inyectado, en params o construido por el composition."""
    svc = kwargs.get("sprint_service") or kwargs.get("service") or kwargs.get("sprint")
    if svc is None and isinstance(params, dict):
        svc = params.get("sprint_service") or params.get("service") or params.get("sprint")
    if svc is None:
        ctx = kwargs.get("ctx")
        if ctx is not None:
            svc = getattr(ctx, "sprint_service", None) or getattr(ctx, "service", None)
    if svc is None:
        try:
            from fast_mcp.composition import build_sprint_service

            svc = build_sprint_service(chain_dir)
        except Exception as exc:  # noqa: BLE001 — fail-open: el efecto reporta
            logger.debug("side_effects: sin sprint_service (%s)", exc)
            return None
    return svc


def spring_id_of(
    service: Any,
    chain_dir: str,
    params: dict[str, Any],
    kwargs: dict[str, Any],
) -> str:
    """ID del sprint EXPLÍCITO por params, sesión o estado.

    Nunca cae al basename del directorio como si fuera un sprint: `get_spring_id`
    devuelve "walker", que no es `SPR-INTEG-20261118` (ya causó un skip silencioso
    en `transform_spec`). El fallback solo se acepta si el sprint EXISTE.
    """
    candidates = [
        params.get("spring_id"),
        params.get("sprint_id"),
        params.get("spring"),
        kwargs.get("spring_id"),
        kwargs.get("sprint_id"),
    ]
    ctx_data = state_context(kwargs)
    candidates += [ctx_data.get("spring_id"), ctx_data.get("sprint_id")]

    for candidate in candidates:
        sid = str(candidate or "").strip()
        if not sid:
            continue
        if service is not None and hasattr(service, "get_spring"):
            try:
                if service.get_spring(sid):
                    return sid
            except Exception:  # noqa: BLE001
                continue
        else:
            return sid

    sid = session_id_of(params, kwargs)
    if sid and service is not None and hasattr(service, "get_session"):
        try:
            record = service.get_session(sid) or {}
            spring = str(record.get("spring_id") or "")
            if spring:
                return spring
        except Exception:  # noqa: BLE001
            pass

    if service is not None and hasattr(service, "get_spring_id"):
        try:
            derived = str(service.get_spring_id(chain_dir) or "")
        except Exception:  # noqa: BLE001
            derived = ""
        if derived:
            try:
                if service.get_spring(derived):
                    return derived
            except Exception:  # noqa: BLE001
                pass
    return ""


def specifications(service: Any, spring_id: str) -> list[dict]:
    if service is None or not spring_id:
        return []
    try:
        return [s for s in (service.get_specs(spring_id) or []) if isinstance(s, dict)]
    except Exception:  # noqa: BLE001
        return []


def ckg(chain_dir: str) -> Any:
    """CKG del proyecto resuelto por path (no por cwd)."""
    try:
        from fast_mcp.composition import ckg_for

        return ckg_for(chain_dir)
    except Exception as exc:  # noqa: BLE001
        logger.debug("side_effects: sin CKG (%s)", exc)
        return None
