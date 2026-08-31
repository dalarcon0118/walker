"""cognitive_graph/backends.py — Registro de StorageBackends por nombre.

ADR-WALKER-061 (F6): el backend se resuelve desde MemoryConnection.backend
(config), no hardcodeado. El adapter concreto se registra aquí y se activa
cambiando WALKER_MEMORY_BACKEND — sin tocar CognitiveGraph, los repos ni el
composition root.

ADR-WALKER-063 (corte directo, CA-9): 'sqlite' (SqliteBackend) es el ÚNICO
backend runtime. 'file' (FileBackend) NO se registra — el formato v1
(.json.gz) no se lee ni escribe en el runtime; queda como clase de bajo
nivel solo para export/migración/tests (§6.6). Un conn con backend='file'
falla fail-fast con mensaje claro (config legacy = config inválida).
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
            fallback silencioso a file). 'file' no está registrado desde
            ADR-063 corte directo.
    """
    name = (conn.backend or "sqlite").strip()
    factory = _REGISTRY.get(name)
    if factory is None:
        raise ValueError(
            f"Storage backend desconocido: '{name}'. "
            f"Disponibles: {', '.join(available_backends())}. "
            f"Revisa {conn.backend!r} en la configuración "
            f"(db) y nota: 'file' legacy ya no es válido (ADR-063)."
        )
    return factory(conn)


# Registro del adaptador runtime (ADR-063): sqlite es el único backend.
from .sqlite_backend import SqliteBackend  # noqa: E402  (registro al final)

register_storage_backend("sqlite", SqliteBackend)
