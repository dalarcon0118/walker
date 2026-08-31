"""domain/models.py — Shim de compatibilidad para TraceEvent.

Mover a domain/entities.py (ADR-048). Este archivo mantiene compatibilidad
de imports existentes durante la transición.
"""
from features.event_tracing.domain.entities import TraceEvent  # noqa: F401

__all__ = ["TraceEvent"]
