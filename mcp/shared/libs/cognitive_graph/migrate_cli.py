"""cognitive_graph/migrate_cli.py — Migración one-shot v1 (.json.gz) → v2 (sqlite).

ADR-WALKER-063 (corte directo): sqlite es la única fuente de verdad del
runtime. Este script es la ÚNICA vía de migración de los blobs históricos
del `.cognitive` (o cualquier data_dir de memorias) a la BD v2. NO se
auto-ejecuta en el runtime, NO es fallback y NO reescribe ni elimina los
blobs v1 (quedan como backup hasta que el operador los retire).

Idempotencia (sin --force): por cada índice, si la BD ya tiene docs para
ese índice se SKIP con warning — re-importar podría pisar datos más nuevos
que el blob (no hay forma segura de detectarlo: la intención del corte
directo es migrar UNA vez, antes de seguir operando).

Uso:
    python -m shared.libs.cognitive_graph.migrate_cli \
        --data-dir <memories_dir> [--index cpg,ckg,delta,trace]

Exit codes:
    0  todos los blobs importados (o skipped/ausentes, sin error)
    2  algún blob de un índice existente falló (transacción por índice,
       rollback de ESE índice; se reporta el resto igual — FS-6 por índice)
    3  error de uso/argumentos (antes de tocar nada)

Fail-ruidoso (IV-6): cualquier excepción inesperada propaga la traza.
"""

from __future__ import annotations

import argparse
import os
import sys

from .connection import MemoryConnection
from .file_io import read_blob  # noqa: F401  (re-export para scripts de export)
from .migrations import import_json_gz_to_sqlite
from .sqlite_backend import SqliteBackend

DEFAULT_INDEXES = ("cpg", "ckg", "delta", "trace")


def _db_has_index(backend: SqliteBackend, index_name: str) -> bool:
    """Idempotencia: True si la BD v2 ya tiene docs para el índice."""
    row = backend._db.execute(  # noqa: SLF001 — script de migración, puente directo
        "SELECT COUNT(*) FROM docs WHERE `index`=?", (index_name,)
    ).fetchone()
    return bool(row and int(row[0]) > 0)


def _find_blob(data_dir: str, index_name: str) -> str:
    """Blob canónico <data_dir>/<index>.json.gz; peer legacy cog/data como fallback."""
    canonical = os.path.join(data_dir, f"{index_name}.json.gz")
    if os.path.exists(canonical):
        return canonical
    legacy_peer = os.path.join(data_dir, "cog", "data", f"{index_name}.json.gz")
    if os.path.exists(legacy_peer):
        return legacy_peer
    return canonical


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="migrate_cli",
        description="Migra blobs .json.gz v1 a sqlite v2 (ADR-WALKER-063, corte directo).",
    )
    parser.add_argument(
        "--data-dir", required=True,
        help="Raíz de memorias (p.ej. el .cognitive base) que contiene los blobs "
             "<index>.json.gz y donde se crea/usa cognitive.sqlite3.",
    )
    parser.add_argument(
        "--index", default=",".join(DEFAULT_INDEXES),
        help="Índices a migrar, separados por coma (default: cpg,ckg,delta,trace).",
    )
    args = parser.parse_args(argv)

    data_dir = os.path.abspath(args.data_dir)
    if not os.path.isdir(data_dir):
        print(f"[migrate] ERROR: --data-dir no existe: {data_dir}", file=sys.stderr)
        return 3
    indexes = tuple(i.strip() for i in args.index.split(",") if i.strip())
    if not indexes:
        print("[migrate] ERROR: --index vacío.", file=sys.stderr)
        return 3

    backend = SqliteBackend(
        MemoryConnection(uri=f"sqlite://{data_dir}", database="walker")
    )
    print(f"[migrate] destino: {backend._db_path}")  # noqa: SLF001 — script one-shot
    failures = 0
    for name in indexes:
        if _db_has_index(backend, name):
            print(f"[migrate] SKIP  {name}: la BD v2 ya tiene docs (idempotente, sin --force).")
            continue
        blob_path = _find_blob(data_dir, name)
        if not os.path.exists(blob_path):
            print(f"[migrate] INFO  {name}: sin blob v1 ({blob_path}) — nada que migrar.")
            continue
        try:
            summary = import_json_gz_to_sqlite(backend, blob_path, index_name=name)
            print(
                f"[migrate] OK    {name}: {summary['docs']} docs, "
                f"{summary['edges']} edges, metadata={summary['metadata']} "
                f"(fuente: {blob_path})"
            )
        except Exception as e:  # noqa: BLE001 — fail ruidoso por índice, FS-6 rollback
            failures += 1
            print(
                f"[migrate] FAIL  {name}: {type(e).__name__}: {e} "
                f"— rollback de '{name}', el resto de índices es independiente.",
                file=sys.stderr,
            )

    if failures:
        print(f"[migrate] {failures} índice(s) fallaron — corregí y re-ejecutá "
              f"(idempotente; los índices ya migrados se SKIP).", file=sys.stderr)
        return 2
    print("[migrate] migración v1→v2 completada. Los blobs .json.gz quedan como "
          "backup; retiralos manualmente cuando confirmes la BD.")
    return 0


if __name__ == "__main__":  # pragma: no cover — CLI
    sys.exit(main())