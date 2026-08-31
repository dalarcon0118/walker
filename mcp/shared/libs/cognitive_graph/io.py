"""shared/libs/cognitive_graph/io.py — Lectura genérica del archivo de índice.

Infraestructura PURA (ADR-045): este componente NO conoce los dominios que
lo usan (cpg/ckg/delta/trace). Solo sabe leer un archivo de índice
CognitiveGraph serializado (.gz o .json) y devolver el dict.

La INTERPRETACIÓN de campos específicos de un dominio (ej. el
`source_fingerprint` del cpg) vive en el feature que posee ese dominio
(code_analysis → CognitiveCpgRepository.read_fingerprint, por el puerto).
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path


def read_index_file(path: str | Path) -> dict:
    """Lee un archivo de índice (.gz o .json) y devuelve el dict.

    Fallback defensivo: si el archivo no existe o está corrupto, devuelve {}.
    Genérico: no interpreta campos de ningún dominio.
    """
    p = Path(path)
    if not p.exists():
        return {}
    try:
        if str(p).endswith(".gz"):
            with gzip.open(p) as f:
                return json.load(f)
        return json.loads(p.read_text())
    except Exception:
        return {}