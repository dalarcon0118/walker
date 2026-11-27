"""handlers/_trace.py — emisión de eventos de dominio desde `tasks` (fail-open).

Los handlers de tasks no importan `event_tracing` en tiempo de módulo (R1): el
servicio se resuelve por import dinámico, igual que en `capture`. Un fallo del
trace nunca rompe la tool que lo emite.
"""
from __future__ import annotations

from typing import Any


def push(chain_dir: str, event: dict[str, Any]) -> bool:
    """Emite `event` al Trace del proyecto. Devuelve False si no hay servicio."""
    if not chain_dir or not isinstance(event, dict) or not event.get("event_type"):
        return False
    try:
        import importlib

        module = importlib.import_module(
            "fast_mcp.features.event_tracing.application.services.trace_factory"
        )
        service = module.build_trace_service(chain_dir)
        if service is None:
            return False
        service.push_event(chain_dir, event)
        return True
    except Exception:  # noqa: BLE001 — el trace no rompe la tool
        return False
