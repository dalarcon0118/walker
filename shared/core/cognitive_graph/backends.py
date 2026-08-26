"""cognitive_graph/backends.py — Registro de StorageBackends por nombre.

ADR-WALKER-061 (F6): el backend se resuelve desde MemoryConnection.backend
(config), no hardcodeado. Hoy solo existe FileBackend; un DbBackend futuro
(F7) se registra aquí y se activa cambiando WALKER_MEMORY_BACKEND — sin
tocar CognitiveGraph, los repos ni el composition root.
"""

from __future__ import annotations

from typing import Any

from .connection import MemoryConnection
from .ports import StorageBackend

__all__ = ["register_storage_backend", "resolve_storage_backend", "available_backends"]

_REGISTRY: dict[str, Any] = {}


def register_storage_backend(name: str, factory: Any) -> None:
    """Registra una factory de backend bajo un nombre de configuración."""
    _REGISTRY[name] = factory


def available_backends() -> list[str]:
    return sorted(_REGISTRY.keys())


def resolve_storage_backend(conn: MemoryConnection) -> StorageBackend:
    """Resuelve el adapter concreto según conn.backend (config-driven).

    Raises:
        ValueError: si el backend no está registrado (fail-fast; nunca
            fallback silencioso a file).
    """
    name = (conn.backend or "file").strip()
    factory = _REGISTRY.get(name)
    if factory is None:
        raise ValueError(
            f"Storage backend desconocido: '{name}'. "
            f"Disponibles: {', '.join(available_backends())}. "
            f"Revisa {conn.backend!r} en la configuración."
        )
    return factory(conn)


# Registro del único backend implementado hoy (F7 agregará DbBackend aquí).
from .file_backend import FileBackend  # noqa: E402  (registro al final)

register_storage_backend("file", FileBackend)
