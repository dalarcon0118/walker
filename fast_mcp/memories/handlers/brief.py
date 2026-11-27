"""fast_mcp/memories/handlers/brief.py — Contexto de fase en una llamada.

Re-exporta desde knowledge.py (fachada) para mantener compatibilidad.
La lógica vive en knowledge.py; este módulo es el punto de entrada canónico (SPEC-003).
"""
from __future__ import annotations

from fast_mcp.memories.handlers.knowledge import handle_brief

__all__ = ["handle_brief"]
