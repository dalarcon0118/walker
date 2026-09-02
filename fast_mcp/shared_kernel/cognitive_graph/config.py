"""cognitive_graph/config.py — La conexión de memorias es CONFIGURACIÓN.

ADR-WALKER-061 (F6): modelo empresarial — backend/uri/db se leen desde el
SSOT de configuración del walker (shared.core.engine.infrastructure.config),
NUNCA hardcodeados. Cambiar de storage es cambiar config, no código.

Fuentes (precedencia del loader central):
    1. env var   → WALKER_MEMORY_BACKEND / WALKER_MEMORY_URI /
                   WALKER_MEMORY_DB / WALKER_MEMORY_PREFIX
                   (mapeo automático: key 'memory.backend' → WALKER_MEMORY_BACKEND)
    2. archivo   → <walker_root>/config/config.json → {"memory": {...}}
    3. defaults  → memory.backend="sqlite", memory.db="walker", memory.prefix=""

Nota: memory.uri NO tiene default — el backend 'sqlite' lo construye desde la
raíz de memorias resuelta por el caller; un backend remoto sin uri falla
fast (ValueError).

ADR-WALKER-063 (CA-9, corte directo): `memory.backend='file'` ya NO es un
valor válido — sqlite (v2) es la única fuente de verdad del runtime. Un
config legacy con 'file' = config INVÁLIDA (fail-fast con mensaje claro);
la migración de blobs v1 se hace con el script one-shot
`shared/libs/cognitive_graph/migrate_cli.py`, no con config.

Creds: JAMÁS por aquí. El uri remoto debe referenciar credenciales por
env del proceso / vault (p.ej. postgresql://$USER@host/db).
"""

from __future__ import annotations

import os

from fast_mcp.shared_kernel import config as _walker_config
from .connection import MemoryConnection

__all__ = ["load_memory_connection", "ENV_MEMORY_BACKEND", "ENV_MEMORY_URI",
           "ENV_MEMORY_DB", "ENV_MEMORY_PREFIX"]

# Los nombres de env derivan del mapeo del loader central:
# key 'memory.X' → WALKER_MEMORY_X. Se exponen para tests y docs.
ENV_MEMORY_BACKEND = "WALKER_MEMORY_BACKEND"
ENV_MEMORY_URI = "WALKER_MEMORY_URI"
ENV_MEMORY_DB = "WALKER_MEMORY_DB"
ENV_MEMORY_PREFIX = "WALKER_MEMORY_PREFIX"

# 'file' se mantiene como CONSTANTE de "valor legacy prohibido": el backend
# v1 (.json.gz) ya no es runtime (ADR-063 corte directo) — solo export/
# migración/tests (FileBackend no registrado). 'sqlite' es el default.
_FILE_BACKEND = "file"
_SQLITE_BACKEND = "sqlite"
# Backends LOCALES: el caller resuelve el dir base y esta función construye
# el uri (sqlite://). Un backend no local exige uri explícito.
_LOCAL_BACKENDS = {_FILE_BACKEND, _SQLITE_BACKEND}


def _local_uri(backend: str, override_uri: str, base_dir: str) -> str:
    """Construye el uri para backends locales (sqlite).

    Un uri configurado solo se honra si su esquema coincide con el backend
    activo (evita filtrar un uri remoto stale cuando el env cambia el
    backend a sqlite). Para sqlite se aceptan también file:// (el
    resolver de ruta del SqliteBackend los trata igual).
    """
    if override_uri:
        if backend == _SQLITE_BACKEND and (
            override_uri.startswith("sqlite://") or override_uri.startswith("file://")
        ):
            return override_uri
    scheme = "sqlite://"
    if base_dir:
        return scheme + os.path.abspath(base_dir)
    return scheme + "./data"


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
        ValueError: si memory.backend='file' (legacy prohibido, ADR-063) o
            si memory.backend no es sqlite y no hay memory.uri (backend
            remoto sin uri = error de config, fail-fast).
    """
    backend = str(_walker_config("memory.backend", _SQLITE_BACKEND)).strip() or _SQLITE_BACKEND
    if backend == _FILE_BACKEND:
        raise ValueError(
            f"memory.backend='file' ya no es válido (ADR-WALKER-063 corte "
            f"directo): sqlite es la única fuente de verdad del runtime. "
            f"Migrá los blobs v1 con shared/libs/cognitive_graph/migrate_cli.py "
            f"y removeé 'memory.backend' del config para usar el default sqlite."
        )
    database = str(_walker_config("memory.db", "walker")).strip()
    prefix = str(_walker_config("memory.prefix", "")).strip()
    override_uri = str(_walker_config("memory.uri", "")).strip()

    if backend in _LOCAL_BACKENDS:
        uri = _local_uri(backend, override_uri, base_dir)
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
