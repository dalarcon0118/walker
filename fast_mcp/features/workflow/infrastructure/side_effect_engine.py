"""infrastructure/side_effect_engine.py — Real SideEffectEngine (BPMN side effects).

Wraps a `SideEffectRegistry` (tasks feature) and implements the
`apply_side_effects(sub_state, state, chain_dir, ...)` contract used by the
workflow orchestrator. Unregistered effect types are skipped (never break the
walk); failures are fail-soft according to `optional`.

Idempotency is tracked per engine instance by
`(node, sub_state, revision, type, idempotency_key)`.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from fast_mcp.shared_kernel.ports import ISideEffectEngine

logger = logging.getLogger(__name__)

_NODE_RE = re.compile(r"^NODE\.(.+?)\.output(?:\.(.+))?$")

_UNRESOLVED = object()


def _state_attr(state: Any, attr: str, default: Any) -> Any:
    if state is None:
        return default
    try:
        val = getattr(state, attr, default)
    except Exception:
        return default
    return default if val is None else val


def _normalize_effect(se: Any) -> dict[str, Any]:
    """Normalize a SideEffectDef or dict into a plain dict."""
    if isinstance(se, dict):
        params = se.get("params", {})
        if not isinstance(params, dict):
            params = {}
        return {
            "type": str(se.get("type", "")),
            "params": dict(params),
            "optional": bool(se.get("optional", False)),
            "idempotency_key": str(se.get("idempotency_key", "") or ""),
        }
    # dataclass / object
    type_ = getattr(se, "type", "")
    params = getattr(se, "params", {})
    if not isinstance(params, dict):
        params = {}
    optional = getattr(se, "optional", False)
    idem = getattr(se, "idempotency_key", "")
    return {
        "type": str(type_ or ""),
        "params": dict(params),
        "optional": bool(optional),
        "idempotency_key": str(idem or ""),
    }


def _effects_of(sub_state: Any) -> list[Any]:
    if sub_state is None:
        return []
    if isinstance(sub_state, dict):
        return list(sub_state.get("side_effects", []) or [])
    return list(getattr(sub_state, "side_effects", []) or [])


def _sub_state_key(sub_state: Any, state: Any) -> str:
    if isinstance(sub_state, dict):
        key = sub_state.get("key")
        if key:
            return str(key)
    key = getattr(sub_state, "key", None)
    if key:
        return str(key)
    return str(_state_attr(state, "current_sub_state", "") or "")


def _resolve_node_output(state: Any, node_key: str, field: str | None) -> Any:
    """Resolve a NODE.<key>.output[.field] reference against state data."""
    if state is None:
        return _UNRESOLVED

    # 1. sub-estado actual
    cur_sub = str(_state_attr(state, "current_sub_state", "") or "")
    cur_node = str(_state_attr(state, "current_node", "") or "")
    if node_key in (cur_sub, cur_node):
        try:
            ssd = state.get_sub_state_data() if hasattr(state, "get_sub_state_data") else None
        except Exception:
            ssd = None
        fields = None
        if ssd is not None:
            fields = getattr(ssd, "fields", None)
            if fields is None and isinstance(ssd, dict):
                fields = ssd.get("fields", ssd)
        if isinstance(fields, dict):
            if field:
                if field in fields:
                    return fields[field]
                out = fields.get("output")
                if isinstance(out, dict) and field in out:
                    return out[field]
            else:
                return dict(fields)

    # 2. sub_state_data direct key
    ssd_map = _state_attr(state, "sub_state_data", {}) or {}
    if isinstance(ssd_map, dict) and node_key in ssd_map:
        ssd = ssd_map[node_key]
        fields = getattr(ssd, "fields", None)
        if fields is None and isinstance(ssd, dict):
            fields = ssd.get("fields", ssd)
        if isinstance(fields, dict):
            if field:
                if field in fields:
                    return fields[field]
                out = fields.get("output")
                if isinstance(out, dict) and field in out:
                    return out[field]
            else:
                return dict(fields)

    # 3. node_executions: last registered of that node/sub-state
    execs = _state_attr(state, "node_executions", []) or []
    try:
        for ne in reversed(list(execs)):
            node = getattr(ne, "node", None)
            sub = getattr(ne, "sub_state", None)
            if node is None and isinstance(ne, dict):
                node = ne.get("node")
            if sub is None and isinstance(ne, dict):
                sub = ne.get("sub_state")
            if str(node or "") != node_key and str(sub or "") != node_key:
                continue
            data = None
            tr = getattr(ne, "task_result", None) if not isinstance(ne, dict) else ne.get("task_result")
            if isinstance(tr, dict):
                data = tr.get("data", tr)
            if data is None:
                sf = getattr(ne, "submitted_fields", None) if not isinstance(ne, dict) else ne.get("submitted_fields")
                data = sf
            if isinstance(data, dict):
                if field:
                    if field in data:
                        return data[field]
                    out = data.get("output")
                    if isinstance(out, dict) and field in out:
                        return out[field]
                else:
                    return dict(data)
            if isinstance(tr, dict) and not field:
                return dict(tr)
    except Exception:
        pass

    return _UNRESOLVED


def _resolve_value(value: Any, state: Any, sub_state: Any, fields: Any) -> Any:
    if isinstance(value, str):
        m = _NODE_RE.match(value.strip())
        if m:
            node_key = m.group(1)
            field = m.group(2)
            resolved = _resolve_node_output(state, node_key, field)
            if resolved is _UNRESOLVED:
                return _UNRESOLVED
            return resolved
        return value
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            rv = _resolve_value(v, state, sub_state, fields)
            if rv is _UNRESOLVED:
                # omit unresolved NODE references
                if isinstance(v, str) and _NODE_RE.match(v.strip()):
                    continue
                rv = None
            out[k] = rv
        return out
    if isinstance(value, list):
        out_list = []
        for item in value:
            rv = _resolve_value(item, state, sub_state, fields)
            if rv is _UNRESOLVED:
                rv = None
            out_list.append(rv)
        return out_list
    return value


def _resolve_params(params: dict[str, Any], state: Any, sub_state: Any, fields: Any) -> dict[str, Any]:
    resolved: dict[str, Any] = {}
    for k, v in (params or {}).items():
        rv = _resolve_value(v, state, sub_state, fields)
        if rv is _UNRESOLVED:
            if isinstance(v, str) and _NODE_RE.match(v.strip()):
                continue
            rv = None
        resolved[k] = rv
    return resolved


class SideEffectEngine(ISideEffectEngine):
    """Real side-effect engine wrapping a `SideEffectRegistry`."""

    def __init__(self, registry: Any = None) -> None:
        self._registry = registry
        self._applied: set[tuple[Any, ...]] = set()

    def run_compensation_handler(self, handler_name: str, ctx: Any) -> Any:
        reg = self._registry
        if reg is not None and hasattr(reg, "execute"):
            try:
                return reg.execute(handler_name, getattr(ctx, "chain_dir", "") if ctx is not None else "", {})
            except Exception as exc:
                logger.exception("SideEffectEngine: compensation '%s' failed", handler_name)
                return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        return {"ok": True, "skipped": True, "handler": handler_name}

    def _is_registered(self, type_: str) -> bool:
        reg = self._registry
        if reg is None:
            return False
        if hasattr(reg, "has"):
            try:
                return bool(reg.has(type_))
            except Exception:
                pass
        handlers = getattr(reg, "_handlers", None)
        if isinstance(handlers, dict):
            return type_ in handlers
        effects = getattr(reg, "effects", None)
        if isinstance(effects, dict):
            return type_ in effects
        # fallback: unknown if registry exposes list_effects
        if hasattr(reg, "list_effects"):
            try:
                return type_ in (reg.list_effects() or [])
            except Exception:
                pass
        # default: assume registered (execute will tell)
        return True

    def apply_side_effects(
        self,
        sub_state: Any,
        state: Any = None,
        chain_dir: str = "",
        ctx: Any = None,
        fields: Any = None,
    ) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        node = str(_state_attr(state, "current_node", "") or "")
        sub_key = _sub_state_key(sub_state, state)
        revision = _state_attr(state, "revision", 0)

        for idx, se_raw in enumerate(_effects_of(sub_state)):
            se = _normalize_effect(se_raw)
            type_ = se["type"]
            if not type_:
                results.append({"ok": True, "skipped": True, "type": type_})
                continue

            idem_part = se["idempotency_key"] if se["idempotency_key"] else (idx, type_)
            ident = (node, sub_key, revision, idx, type_, idem_part)
            if ident in self._applied:
                results.append({"ok": True, "skipped": True, "idempotent": True, "type": type_})
                continue

            params = dict(se["params"] or {})
            resolved_params = _resolve_params(params, state, sub_state, fields)

            if not self._is_registered(type_):
                results.append({"ok": True, "skipped": True, "type": type_})
                self._applied.add(ident)
                continue

            reg = self._registry
            optional = se["optional"]
            try:
                raw = reg.execute(type_, chain_dir, resolved_params, state=state, ctx=ctx, fields=fields)
            except Exception as exc:  # never raises
                logger.exception("SideEffectEngine: '%s' raised", type_)
                raw = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

            if not isinstance(raw, dict):
                raw = {"ok": True, "data": raw}
            entry = dict(raw)
            entry.setdefault("type", type_)
            results.append(entry)
            self._applied.add(ident)

            ok = entry.get("ok", True)
            if ok is False and not optional:
                error_msg = entry.get("error", "side effect failed")
                return {"ok": False, "results": results, "error": str(error_msg)}

        return {"ok": True, "results": results, "error": ""}
