"""test_storage_caches_bdd.py — verificación y carga del CPG no se repiten.

Medido 2026-11-20 (warm `agent_context` = 787 ms):

- `PRAGMA integrity_check` corría en CADA `SqliteIndex.__init__` (320 ms).
  La BD es inmutable mientras no cambie su firma (mtime/size): se verifica una
  vez por versión del archivo.
- `CognitiveCpgRepository.load()` materializaba 9.5k nodos + edges (450 ms) en
  cada lectura. El agente repite el mismo archivo: se cachea por firma y se
  invalida en `save()`.

Ambos caches son por proceso y fail-safe: sin firma (archivo inexistente) no
se cachea nada.
"""

from __future__ import annotations

import os
from pathlib import Path

from fast_mcp.features.code_analysis.infrastructure.cognitive_cpg_repo import (
    CognitiveCpgRepository,
)
from fast_mcp.shared_kernel.cognitive_graph import sqlite_backend
from fast_mcp.shared_kernel.cognitive_graph.backends import resolve_storage_backend
from fast_mcp.shared_kernel.cognitive_graph.connection import MemoryConnection


def _backend(tmp_path: Path):
    conn = MemoryConnection(backend="sqlite", uri=f"sqlite://{tmp_path}")
    return resolve_storage_backend(conn)


def test_integrity_check_una_vez_por_version_del_archivo(tmp_path):
    first = _backend(tmp_path)
    before = sqlite_backend.integrity_checks()

    _backend(tmp_path)  # misma versión: no re-verifica
    assert sqlite_backend.integrity_checks() == before

    db = tmp_path / "cognitive.sqlite3"
    os.utime(db, ns=(db.stat().st_atime_ns, db.stat().st_mtime_ns + 1_000_000))
    _backend(tmp_path)  # nueva versión: re-verifica
    assert sqlite_backend.integrity_checks() == before + 1


class _CpgFalso:
    def __init__(self) -> None:
        self.docs = 0

    def get_docs(self, _index: str) -> dict:
        self.docs += 1
        return {"n:1": {"id": "n:1", "type": "function"}}

    def get_all_edges(self, _index: str) -> list:
        return []

    def get_metadata(self, _index: str) -> dict:
        return {"stats": {"files_scanned": 1}}


def _repo(tmp_path: Path, cg: _CpgFalso) -> CognitiveCpgRepository:
    (tmp_path / "cognitive.sqlite3").write_bytes(b"x")
    repo = object.__new__(CognitiveCpgRepository)
    repo._data_dir = str(tmp_path)
    repo._project_id = "p"
    repo._cg = cg
    return repo


def test_load_del_cpg_se_cachea_por_firma(tmp_path):
    cg = _CpgFalso()
    repo = _repo(tmp_path, cg)

    first = repo.load()
    second = repo.load()

    assert cg.docs == 1, "la segunda lectura no debe volver a materializar el CPG"
    assert second is first

    db = tmp_path / "cognitive.sqlite3"
    os.utime(db, ns=(db.stat().st_atime_ns, db.stat().st_mtime_ns + 1_000_000))
    repo.load()
    assert cg.docs == 2, "una firma distinta invalida el cache"
