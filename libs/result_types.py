"""libs/result_types.py — Result pattern para Walker.

Wrapper sobre dry-python/returns que expone los tipos fundamentales
del patrón Result para manejo de errores explícito.

Uso:
    from libs.result_types import Result, Success, Failure, safe, is_successful

    @safe
    def validate_plan(chain_dir: str) -> dict:
        # Puede lanzar excepciones → se convierten en Failure automáticamente
        ...

    result = validate_plan("/path/to/sprint")
    if is_successful(result):
        plan = result.value_or({})
    else:
        error = result.failure()
"""
from __future__ import annotations

from typing import Any, TypeVar

from returns.result import (
    Result,
    Success,
    Failure,
    safe,
    attempt,
)
from returns.pipeline import is_successful, flow
from returns.pointfree import bind, map_

# Type aliases para Walker
T = TypeVar("T")
E = TypeVar("E")

# Walker usa dict como tipo de retorno estándar para responses
ResultDict = Result[dict[str, Any], Exception]
ResultAny = Result[Any, Exception]

__all__ = [
    "Result",
    "Success",
    "Failure",
    "safe",
    "attempt",
    "is_successful",
    "flow",
    "bind",
    "map_",
    "ResultDict",
    "ResultAny",
]
