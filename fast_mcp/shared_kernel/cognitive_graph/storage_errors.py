"""cognitive_graph/storage_errors.py — Errores custom del dominio de datos.

ADR-WALKER-063 §6.1 (Fase 1): errores custom por fase. Invariantes:
  - IV-6: ningún fallo silencioso — toda operación que falla propaga `StorageError`
    (o lo reporta en el dict del puerto con `ok: False` + `error`), jamás
    `{}`/`None` que parezcan éxito, jamás `except: pass`.
  - IV-9: sin fallbacks — el backend sqlite es la única vía tras el cutover.

Jerarquía (dominio de datos / infraestructura de memorias). Fase 2 añadirá
`StorageSearchUnsupportedError` y `StorageServerError` (roadmap, no viven aquí).
"""

from __future__ import annotations


class StorageError(Exception):
    """Base del dominio de datos (Fase 1). Nunca se traga en silencio."""


class StorageContractError(StorageError):
    """Método del puerto invocado sin contrato válido.

    P.ej.: índice no registrado, query no soportado por el search handler.
    """


class StorageWriteError(StorageError):
    """INSERT/COMMIT falló. El evento NO se pierde en silencio (IV-6)."""


class StorageReadError(StorageError):
    """SELECT/reconstrucción del read model falló."""


class StorageIntegrityError(StorageError):
    """`PRAGMA integrity_check` con violaciones (FS-5).

    Jamás se siguen leyendo datos posiblemente corruptos.
    """


class StorageStateError(StorageError):
    """Estado de la BD incompatible con la operación pedida.

    Caso canónico (ADR-WALKER-075 §10.0, directiva PO): el gate de higiene
    pre-migración (H-1..H-7) detecta suciedad heredada → `el sqlite entra a
    v3 LIMPIO o no migra` (FS-9). El mensaje incluye la lista ESPECÍFICA y
    accionable de suciedad; el operador limpia y re-intenta — nunca migrar
    sucio, nunca limpiar en silencio (IV-6).
    """


class StorageSchemaVersionError(StorageError):
    """`schema_version` > soportado o BD v2 leída por lector v1 (§6.4).

    Guarda de lector ruidosa: nunca datos stale en silencio.
    """


class StorageMigrationError(StorageError):
    """Fallo durante la migración import (json.gz → sqlite) (§7.7)."""


class StorageMigrationCountMismatchError(StorageMigrationError):
    """Conteo sqlite != conteo `.json.gz` por índice (FS-6/CA-6).

    Aborta el índice (todo-o-nada), sin degradación.
    """


class StorageEmptyResultError(StorageError):
    """Resultado vacío cuando el contrato EXIGE datos (IV-7).

    Por defecto el puerto devuelve shape explícito de sin-hits
    (`{"hits": {"total": 0, "hits": []}}`); este error solo se usa en
    operaciones cuyo contrato declara que la ausencia es fallo.
    """