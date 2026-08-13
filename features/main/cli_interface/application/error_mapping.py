"""
application/error_mapping.py — Mapeo error del dominio → status HTTP (presentación).

Extraído de handlers.py (F4 — thin adapters, ADR-WALKER-016).

Convierte excepciones del engine en payloads REST estructurados.
Es un mapper de presentación: no orquesta, solo traduce.
"""

from __future__ import annotations


def error_type_to_status(error_type: str, error: Exception) -> int:
    """Mapea la taxonomía de errores del dominio a status HTTP.

    SSOT: cada ErrorType del workflow engine tiene un status_code REST.
      validation/transition → 400 (el agente puede corregir y reintentar)
      state/notfound       → 404 (recurso inexistente)
      execution/infra      → 500 (fallo interno, no recuperable por el agente)
    """
    from core.engine.domain.errors import (
        ErrorType,
        NodeNotFoundError,
        SubStateNotFoundError,
        GateNotFoundError,
        TaskNotFoundError,
    )
    if error_type in (ErrorType.VALIDATION.value, ErrorType.TRANSITION.value):
        return 400
    if error_type == ErrorType.STATE.value:
        return 404
    if isinstance(
        error,
        (NodeNotFoundError, SubStateNotFoundError, GateNotFoundError, TaskNotFoundError),
    ):
        return 404
    if error_type in (ErrorType.EXECUTION.value, ErrorType.INFRASTRUCTURE.value):
        return 500
    return 500


def map_engine_error(error: Exception) -> dict:
    """Mapea un error del engine a un payload estructurado (patrón Result).

    Usa la taxonomía del dominio (classify_error → ErrorType) + el contexto
    del WorkflowEngineError (details/errors/stderr) para producir:
      { error_type, strategy, status_code, message, errors }

    El clasificador se envuelve en @safe (dry-python/returns): si la propia
    clasificación falla, devolvemos un payload UNKNOWN en vez de propagar.
    """
    from returns.result import safe
    from returns.pipeline import is_successful
    from core.engine.domain.errors import WorkflowEngineError

    @safe
    def _build() -> dict:
        from core.engine.domain.errors import classify_error

        error_type = classify_error(error)
        details: list[str] = []
        message = str(error)
        if isinstance(error, WorkflowEngineError) and error.context:
            ctx = error.context
            if ctx.get("details"):
                details.extend(str(d) for d in ctx["details"])
            if ctx.get("errors"):
                details.extend(str(e) for e in ctx["errors"])
            if ctx.get("stderr"):
                details.append(f"stderr: {str(ctx['stderr'])[:300]}")
        return {
            "error_type": error_type.value,
            "status_code": error_type_to_status(error_type.value, error),
            "message": message,
            "errors": details,
        }

    result = _build()
    if is_successful(result):
        return result.value_or({})
    return {
        "error_type": "unknown",
        "status_code": 500,
        "message": str(error),
        "errors": [],
    }
