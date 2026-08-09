"""Backward-compat shim — los formatters viven ahora en libs/context/format.py.

Re-export puro para no romper los imports internos del engine ni de tests.
Nueva lógica DEBE ir a libs/context/ (DIP): este módulo solo re-exporta.
"""
from __future__ import annotations

from libs.context.format import (
    fmt_tokens,
    format_ckg_results,
    format_cpg_results,
    format_trace_results,
    format_state_result,
    format_delta_results,
)

__all__ = [
    "fmt_tokens",
    "format_ckg_results",
    "format_cpg_results",
    "format_trace_results",
    "format_state_result",
    "format_delta_results",
]
