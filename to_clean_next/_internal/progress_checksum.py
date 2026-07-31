#!/usr/bin/env python3
"""progress_checksum.py — SHA-256 integrity checksum for progress.json.

V1/V2 Integrity check: cada vez que un script escribe progress.json,
agrega un campo _checksum que es el SHA-256 del contenido (sin _checksum).
Al leer, se verifica que _checksum coincida.

Si falla la verificación, significa que el archivo fue manipulado
externamente (ej: LLM editando progress.json directamente) y se debe
rechazar la operación.

Uso:
    from _internal.progress_checksum import add_checksum, verify_checksum

    # Al escribir:
    data["_checksum"] = compute_checksum(data)
    write_json(path, data)

    # Al leer:
    data = load_json(path)
    if not verify_checksum(data):
        raise ValueError("progress.json manipulado externamente")
"""

import hashlib
import json


def _serialize_deterministic(data: dict) -> str:
    """Serialize dict to JSON string with sorted keys for deterministic hash.

    Uses ``sort_keys=True`` to ensure the same data always produces
    the same hash, regardless of Python dict insertion order.
    """
    return json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)


def compute_checksum(data: dict) -> str:
    """Compute SHA-256 checksum of data excluding the ``_checksum`` field.

    Args:
        data: The progress.json data dict.

    Returns:
        Hex-encoded SHA-256 hash of the data without the ``_checksum`` key.
    """
    # Exclude _checksum from hash computation
    data_copy = {k: v for k, v in data.items() if k != "_checksum"}
    raw = _serialize_deterministic(data_copy)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def add_checksum(data: dict) -> dict:
    """Add ``_checksum`` field to data dict (in-place and return).

    The checksum includes all fields EXCEPT ``_checksum`` itself.
    This means verifying later will compute the hash without ``_checksum``
    and compare to the stored value.

    Args:
        data: Dict to add checksum to (modified in-place).

    Returns:
        The same dict with ``_checksum`` added.
    """
    data["_checksum"] = compute_checksum(data)
    return data


def verify_checksum(data: dict) -> bool:
    """Verify that ``_checksum`` in data matches recomputed value.

    Args:
        data: Dict loaded from progress.json.

    Returns:
        True if checksum matches or no ``_checksum`` field present
        (backward compat: files without checksum are accepted but warned).
        False if checksum does NOT match (file was tampered).
    """
    # Check if _checksum key EXISTS (not just if value is truthy)
    # This prevents bypass via _checksum: null
    if "_checksum" not in data:
        # Legacy file without _checksum field — accept with warning.
        return True  # backward compat — don't block old progress files
    stored = data["_checksum"]
    computed = compute_checksum(data)
    return stored == computed
