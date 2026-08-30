"""shared/common/context/format.py — Helpers de formateo de contexto (mecanismo puro).

Solo helpers GENÉRICOS de formateo (capa común). Los formatters de las
memorias del walker (CKG/CPG/Delta/Trace) son NEGOCIO del walker y viven en
`app/context_format.py` (ADR-WALKER-067 §7.5/L6).
"""
from __future__ import annotations


def fmt_tokens(content: str) -> int:
    """Estimación de tokens (~4 chars/token)."""
    return len(content) // 4