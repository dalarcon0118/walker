"""cognitive_graph/config.py — La conexión de memorias es CONFIGURACIÓN.

ADR-WALKER-061 (F6): modelo empresarial — backend/uri/db se leen desde el
SSOT de configuración del walker (shared.core.engine.infrastructure.config),
NUNCA hardcodeados. Cambiar de storage es cambiar config, no código.

Fuentes (precedencia del loader central):
    1. env var   → WALKER_MEMORY_BACKEND / WALKER_MEMORY_URI /
                   WALKER_MEMORY_DB / WALKER_MEMORY_PREFIX
                   (mapeo automático: key 'memory.backend' → WALKER_MEMORY_BACKEND)
    2. archivo   → <walker_root>/config/config.json → {"memory": {...}}
    3. defaults  → memory.backend="file", memory.db="walker", memory.prefix=""

Nota: memory.uri NO tiene default — el backend 'file' lo construye desde la
raíz de memorias resuelta por el caller; un backend remoto sin uri falla
fast (ValueError).

Creds: JAMÁS por aquí. El uri remoto debe referenciar credenciales por
env del proceso / vault (p.ej. postgresql://$USER@host/db).
"""

from __future__ import annotations

import os

from shared.core.engine.infrastructure.config import get as _walker_config
from .connection import MemoryConnection

__all__ = ["load_memory_connection", "ENV_MEMORY_BACKEND", "ENV_MEMORY_URI",
           "ENV_MEMORY_DB", "ENV_MEMORY_PREFIX"]

# Los nombres de env derivan del mapeo del loader central:
# key 'memory.X' → WALKER_MEMORY_X. Se exponen para tests y docs.
ENV_MEMORY_BACKEND = "WALKER_MEMORY_BACKEND"
ENV_MEMORY_URI = "WALKER_MEMORY_URI"
ENV_MEMORY_DB = "WALKER_MEMORY_DB"
ENV_MEMORY_PREFIX = "WALKER_MEMORY_PREFIX"

_FILE_BACKEND = "file"


def load_memory_connection(base_dir: str = "") -> MemoryConnection:
    """Construye la MemoryConnection desde la configuración del walker.

    Args:
        base_dir: raíz de memorias YA RESUELTA para backends locales
            (p.ej. memory_data_dir(chain_dir), o data_dir del proyecto).
            Para backend 'file' el caller SIEMPRE resuelve el directorio —
            esta función NO hace resolución de paths (evita doble
            resolución y acoplamiento con features/project).

    Returns:
        MemoryConnection lista para resolve_storage_backend().

    Raises:
        ValueError: si memory.backend != 'file' y no hay memory.uri
            (backend remoto sin uri = error de config, fail-fast).
    """
    backend = str(_walker_config("memory.backend", _FILE_BACKEND)).strip() or _FILE_BACKEND
    database = str(_walker_config("memory.db", "walker")).strip()
    prefix = str(_walker_config("memory.prefix", "")).strip()
    override_uri = str(_walker_config("memory.uri", "")).strip()

    if backend == _FILE_BACKEND:
        # Un uri configurado solo se honra si su esquema coincide con el
        # backend activo (evita filtrar un uri remoto stale cuando el env
        # cambia el backend a file).
        if override_uri.startswith("file://"):
            uri = override_uri
        elif base_dir:
            uri = "file://" + os.path.abspath(base_dir)
        else:
            uri = "file://./data"
    else:
        # Backend remoto: el uri es obligatorio (no hay default seguro).
        if not override_uri:
            raise ValueError(
                f"{ENV_MEMORY_URI} es obligatorio cuando "
                f"memory.backend='{backend}' (fail-fast, sin default)."
            )
        uri = override_uri

    return MemoryConnection(
        backend=backend, uri=uri, database=database, collection_prefix=prefix,
    )
