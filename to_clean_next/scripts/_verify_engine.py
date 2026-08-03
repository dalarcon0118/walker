"""Shim — _verify_engine.py delegado en features.

El motor real vive en features/task_execution/infrastructure/verify_engine.py.
Este módulo conserva la firma histórica para los consumidores legacy
(scripts/prompt_engine.py) sin duplicar lógica.
"""
from __future__ import annotations

from features.task_execution.infrastructure.verify_engine import (  # noqa: F401
    _AST_DEF_PATTERNS,
    _AST_LANG_MAP,
    _AST_GREP_AVAILABLE,
    _is_string_a_shell_command,
    _parse_predicate,
    invalidate_verify_cache,
    run_verification,
    verify_predicate,
    verify_predicates_batch,
)
