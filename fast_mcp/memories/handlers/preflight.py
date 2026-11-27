"""fast_mcp/memories/handlers/preflight.py — Preflight real.

Re-exporta desde knowledge.py (fachada) para mantener compatibilidad.
La lógica vive en knowledge.py; este módulo es el punto de entrada canónico (SPEC-003).
"""
from __future__ import annotations

from fast_mcp.memories.handlers.knowledge import handle_preflight

__all__ = ["handle_preflight"]
