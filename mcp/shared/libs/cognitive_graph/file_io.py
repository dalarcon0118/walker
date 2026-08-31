"""cognitive_graph/file_io.py — Serialización de blobs .json.gz (sin lógica de negocio).

ADR-WALKER-061 (F2): extrae la E/S de archivos de `Index` a este módulo
independiente. `Index` y `FileBackend` importan de aquí — sin ciclo de
imports (solo stdlib).

El formato en disco es IDÉNTICO al histórico de `Index._save`:
    {index, schema_hash, docs[], edges[], metadata, token_index,
     _type_index, _field_index}

Funciones:
    read_blob(path)  -> texto del archivo o None
    write_blob(path, payload) -> escritura atómica (tmp + rename + fsync)
"""

from __future__ import annotations

import gzip
import json
import os
import zlib


def read_blob(path: str) -> str | None:
    """Lee un .json.gz (o .json plano). None si no existe o ilegible.

    Replica el fallback de Index._read_gz:
      gzip.decompress → raw deflate (zlib) → JSON plano.
    """
    if not os.path.exists(path):
        return None
    raw_bytes = open(path, "rb").read()
    try:
        return gzip.decompress(raw_bytes).decode()
    except Exception:
        pass
    try:
        return zlib.decompress(raw_bytes[10:], -zlib.MAX_WBITS).decode()
    except Exception:
        pass
    try:
        return raw_bytes.decode("utf-8")
    except Exception:
        return None


def write_blob(path: str, payload: str) -> None:
    """Escribe payload JSON de forma atómica (tmp + rename + fsync).

    Replica la sección de escritura de Index._save (sin el lock, que queda
    en Index alrededor del ciclo read-modify-write).
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    if path.endswith(".gz"):
        with open(tmp_path, "wb") as f:
            f.write(gzip.compress(payload.encode()))
            f.flush()
            os.fsync(f.fileno())
    else:
        with open(tmp_path, "w") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
    os.replace(tmp_path, path)
