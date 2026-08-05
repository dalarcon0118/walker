"""
infrastructure/serializer.py — Serialización JSON robusta para el MCP adapter.

Extraído de mcp_adapter.py (F4 — thin adapters, ADR-WALKER-016).

Convierte cualquier objeto (to_dict/__dict__/dataclasses/datetime) a una
representación JSON-safe para que el protocolo MCP NUNCA rompa por
serialización.
"""

from __future__ import annotations

import json
from datetime import datetime


def json_safe(value):
    """Convierte value en una representacion JSON-serializable."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    # Objetos con to_dict() (StepResult, WorkflowState, etc.)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            return json_safe(to_dict())
        except Exception:
            # fallback del chain: si to_dict falla, se intenta __dict__ abajo.
            dict_attr = getattr(value, "__dict__", None)
            if isinstance(dict_attr, dict):
                return json_safe(dict_attr)
            return str(value)
    # Dataclasses y objetos con __dict__
    dict_attr = getattr(value, "__dict__", None)
    if isinstance(dict_attr, dict):
        return json_safe(dict_attr)
    return str(value)


def dumps(obj) -> str:
    """json.dumps con fallback seguro (nunca lanza por serializacion)."""
    try:
        return json.dumps(obj, ensure_ascii=False, default=json_safe)
    except (TypeError, ValueError):
        return json.dumps(json_safe(obj), ensure_ascii=False)
