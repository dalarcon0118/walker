"""
infrastructure/serializer.py — Serialización JSON robusta para el MCP adapter.

Extraído de mcp_adapter.py (F4 — thin adapters, ADR-WALKER-016).

Convierte cualquier objeto (to_dict/__dict__/dataclasses/datetime) a una
representación JSON-safe para que el protocolo MCP NUNCA rompa por
serialización.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from returns.pipeline import is_successful
from returns.result import safe

logger = logging.getLogger(__name__)


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
        to_dict_outcome = _to_dict_core(value)
        if is_successful(to_dict_outcome):
            return json_safe(to_dict_outcome.value_or(None))
        # fallback del chain: si to_dict falla, se intenta __dict__ abajo.
        logger.debug(
            "to_dict fallo (%s: %s); probando __dict__",
            type(to_dict_outcome.failure()).__name__, to_dict_outcome.failure(),
        )
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
    outcome = _dumps_core(obj)
    if is_successful(outcome):
        return outcome.value_or("")
    error = outcome.failure()
    logger.debug(
        "dumps directo fallo (%s: %s); usando json_safe", type(error).__name__, error,
    )
    return json.dumps(json_safe(obj), ensure_ascii=False)


@safe
def _dumps_core(obj) -> str:
    """Core puro de dumps (puede lanzar TypeError/ValueError)."""
    return json.dumps(obj, ensure_ascii=False, default=json_safe)


@safe
def _to_dict_core(value):
    """Core puro: llama a to_dict() del objeto (puede lanzar)."""
    return value.to_dict()
