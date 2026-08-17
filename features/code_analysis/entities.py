"""entities.py — Shim de compatibilidad para CodeNode.

Mover a domain/entities.py (ADR-048). Este archivo mantiene compatibilidad
de imports existentes durante la transición.
"""
from features.code_analysis.domain.entities import CodeNode  # noqa: F401

__all__ = ["CodeNode"]
