"""fast_mcp/memories/handlers/plan_diff.py — Diff del plan contra la spec.

Re-exporta desde knowledge.py (fachada) para mantener compatibilidad.
La lógica vive en knowledge.py; este módulo es el punto de entrada canónico (SPEC-003).
"""
from __future__ import annotations

from fast_mcp.memories.handlers.knowledge import handle_plan_diff

__all__ = ["handle_plan_diff"]
