"""side_effects/spec.py — Side effect: transform spec status/fields."""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _get_sprint_service(chain_dir: str, params: dict[str, Any], kwargs: dict[str, Any]) -> Any:
    svc = kwargs.get("sprint_service")
    if svc is None:
        svc = kwargs.get("service") or kwargs.get("sprint")
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
        except Exception as exc:  # pragma: no cover - fail-open
            logger.debug("transform_spec: could not build sprint_service (%s)", exc)
            svc = None
    return svc


def transform_spec(chain_dir: str, params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Move a spec to a new status (and optional fields).

    params:
        status: target status
        fields: dict of extra fields to merge (already resolved by engine)
        spring_id/sprint_id/spring: optional spring id
        spec_id/spec: optional spec id
    """
    params = params or {}
    state = kwargs.get("state", kwargs.get("workflow_state"))
    status = params.get("status")
    campos = params.get("fields") or {}
    if not isinstance(campos, dict):
        campos = {}

    spring_id = params.get("spring_id") or params.get("sprint_id") or params.get("spring")
    spec_id = params.get("spec_id") or params.get("spec")

    ctx_data: dict[str, Any] = {}
    if state is not None:
        ctx_data = state.get("context", {}) if isinstance(state, dict) else (getattr(state, "context", {}) or {})
    if isinstance(ctx_data, dict):
        if not spring_id:
            spring_id = ctx_data.get("spring_id") or ctx_data.get("sprint_id")
        if not spec_id:
            spec_id = ctx_data.get("task_id") or ctx_data.get("spec_id")

    sprint_service = _get_sprint_service(chain_dir, params, kwargs)

    if sprint_service is None or not hasattr(sprint_service, "update_spec"):
        return {"ok": True, "skipped": True}

    # El sprint es el de la SESIÓN, no el basename del directorio.
    # `get_spring_id` devuelve "walker", que no es `SPR-INTEG-20261118`: con eso
    # el efecto se saltaba en silencio y la spec no se movía (medido 2026-11-18).
    ctx_obj = kwargs.get("ctx")
    session_id = str(getattr(ctx_obj, "session_id", "") or "") if ctx_obj is not None else ""
    if not session_id:
        session_id = str(params.get("session_id") or "")
    if not spring_id and session_id and hasattr(sprint_service, "get_session"):
        try:
            rec = sprint_service.get_session(session_id) or {}
            spring_id = str(rec.get("spring_id") or "")
        except Exception:
            spring_id = ""

    if not spring_id and hasattr(sprint_service, "get_spring_id"):
        try:
            spring_id = sprint_service.get_spring_id(chain_dir)
        except Exception:
            spring_id = ""

    # Si el sprint resuelto no contiene la spec, buscarla entre los sprints.
    get_spec = getattr(sprint_service, "get_spec", None)
    list_springs = getattr(sprint_service, "list_springs", None)
    if spec_id and spring_id and callable(get_spec):
        try:
            found = bool(get_spec(spring_id, spec_id))
        except Exception:
            found = True
        if not found and callable(list_springs):
            try:
                for s in list_springs() or []:
                    sid = s.get("spring_id") if isinstance(s, dict) else getattr(s, "spring_id", "")
                    if sid and sid != spring_id and get_spec(sid, spec_id):
                        spring_id = sid
                        break
            except Exception:
                pass

    if not spring_id or not spec_id:
        return {"ok": True, "skipped": True}

    updates = {"status": status, **campos}
    try:
        result = sprint_service.update_spec(spring_id, spec_id, updates)
    except Exception as exc:
        logger.exception("transform_spec: update_spec failed")
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    if result is None:
        return {"ok": True, "skipped": True}
    if isinstance(result, dict):
        if result.get("ok") is False:
            return {"ok": False, "error": result.get("error", "update_spec failed")}
        if result.get("error"):
            return {"ok": False, "error": result.get("error")}
    return {"ok": True, "result": result}


def register(registry) -> None:
    """Register transform_spec."""
    registry.register("transform_spec", transform_spec)
