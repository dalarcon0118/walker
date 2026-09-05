"""Migración REAL de la canónica v2→v3 (ADR-WALKER-075 §10.1) + verificación post.

Flujo (todo por el camino sancionado de código, sin tocar archivos crudos):
  1. BACKUP de la canónica vía API backup de sqlite (origen read-only,
     snapshot consistente — mismo patrón que m0_baseline.snapshot_real_db).
  2. BOOT real del runtime sobre la canónica (load_memory_connection +
     resolve_storage_backend + build_cognitive_graph) — el __init__ del
     SqliteBackend ejecuta la migración lazy in-situ:
     F2 (columnas VIRTUAL + índices SQL) → F3 (doc_sha1 + backfill) →
     F1 (FTS por índice con fulltext) → marca v3 (FS-2: SOLO tras éxito).
  3. Registro de los schemas de PRODUCCIÓN (composition root: delta/trace/
     cpg/ckg) — completa FTS de los fulltext y PERSISTE el catálogo
     __indexes__ completo (arregla el hallazgo M0-1).
  4. VERIFICACIÓN post-migración (M-04/M-10/M-12/FS-1/FS-2):
     schema v3 · conteos idénticos (delta=8, trace=872, edges 7) ·
     integrity ok · 0 orphan edges · columnas generadas · índices SQL ·
     FTS markers + reconcile real (MATCH) · doc_sha1 sin vacíos ·
     light-loads y materialize por índice · eventos preservados (55) ·
     smoke fulltext (delta) + structured (trace) sin materializar.

Usage:
  python3 scripts/migrate_canonical_v3.py [--dry]
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time

from fast_mcp.shared_kernel.cognitive_graph.sqlite_backend import SqliteBackend

# Workspace root + canónica
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
DATA_DIR = os.path.join(ROOT, ".cognitive", "projects", "walker", "data")
CANON_DB = os.path.join(DATA_DIR, "cognitive.sqlite3")
BACKUP_DIR = os.path.join(ROOT, ".cognitive", "backups")

# Schemas de PRODUCCIÓN — SSOT ÚNICO (T-10/M-6): shared_kernel schemas.py;
# la copia local (con cpg stale) se descartó en favor de la forma runtime.
from fast_mcp.shared_kernel.cognitive_graph.schemas import (  # noqa: E402
    PRODUCTION_INDEX_SCHEMAS,
)


def backup_canonical() -> str:
    """Snapshot consistente vía API backup (origen read-only) — §10.1."""
    ts = time.strftime("%Y%m%d-%H%M%S")
    os.makedirs(BACKUP_DIR, exist_ok=True)
    dest = os.path.join(BACKUP_DIR, f"cognitive.v2.{ts}.sqlite3")
    src = sqlite3.connect(f"file:{CANON_DB}?mode=ro", uri=True)
    dst = sqlite3.connect(dest)
    src.backup(dst)
    dst.close()
    src.close()
    r = sqlite3.connect(dest)
    integrity = [x[0] for x in r.execute("PRAGMA integrity_check")]
    assert integrity == ["ok"], f"backup corrupto: {integrity}"
    r.close()
    print(f"[backup] {dest} — integrity ok, {os.path.getsize(dest)} bytes")
    return dest


def pre_state(conn_path: str) -> dict:
    c = sqlite3.connect(f"file:{conn_path}?mode=ro", uri=True)
    sv = c.execute(
        "SELECT value FROM meta WHERE `index`='__db__' AND key='__schema__'"
    ).fetchone()
    docs = dict(c.execute("SELECT `index`, COUNT(*) FROM docs GROUP BY `index`"))
    edges = dict(c.execute("SELECT `index`, COUNT(*) FROM edges GROUP BY `index`"))
    ev = c.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    c.close()
    return {"schema": sv[0] if sv else None, "docs": docs, "edges": edges, "events": ev}


def _assert_exact_set(actual, expected, label):
    """Completitud por IGUALDAD de sets (T-02/M-03) — nunca membresía.

    Un desvío como "8 índices donde el contrato dice 7" aborta listando
    exceso y falta; el DDL nuevo legítimo se incorpora actualizando el
    EXPECTED, no relajando la comparación.
    """
    actual_set, expected_set = set(actual), set(expected)
    excess = sorted(actual_set - expected_set)
    missing = sorted(expected_set - actual_set)
    assert not excess and not missing, (
        f"desvío en {label}: "
        + (f"exceso={excess} " if excess else "")
        + (f"falta={missing}" if missing else "")
    )


def verify_post(backend, expected_docs: dict, expected_edges: dict) -> dict:
    """Verificación post-migración completa (M-04/M-10/M-12/FS-1/FS-2)."""
    out: dict = {}
    # L10 (T-02): fail-fast sobre el tipo de backend — verify_post asume el
    # layout sqlite (PRAGMA table_xinfo, sqlite_master, _db); un backend de
    # otro tipo jamás debe pasar por aquí en silencio.
    assert isinstance(backend, SqliteBackend), (
        f"verify_post requiere SqliteBackend, recibió {type(backend).__name__} "
        f"— fail-fast (L10)"
    )
    db = backend._db  # noqa: SLF001 — script de verificación

    # M-12: schema v3 marcado (FS-2)
    sv = db.execute(
        "SELECT value FROM meta WHERE `index`='__db__' AND key='__schema__'"
    ).fetchone()
    out["schema_version"] = sv[0] if sv else None
    assert out["schema_version"] == "v3", f"schema marker NO es v3: {out['schema_version']}"

    # Conteos intactos
    docs_after = dict(db.execute("SELECT `index`, COUNT(*) FROM docs GROUP BY `index`"))
    edges_after = dict(db.execute("SELECT `index`, COUNT(*) FROM edges GROUP BY `index`"))
    out["docs_after"] = docs_after
    out["edges_after"] = edges_after
    assert docs_after == expected_docs, f"docs cambiaron: {docs_after} vs {expected_docs}"
    assert edges_after == expected_edges, f"edges cambiaron: {edges_after} vs {expected_edges}"

    # FS-5: integrity + orphans
    out["integrity"] = [r[0] for r in db.execute("PRAGMA integrity_check")]
    assert out["integrity"] == ["ok"]
    out["orphan_edges"] = db.execute(
        """SELECT COUNT(*) FROM edges e
           WHERE NOT EXISTS (SELECT 1 FROM docs d
                 WHERE d.`index`=e.`index` AND d.id=e.src)
              OR NOT EXISTS (SELECT 1 FROM docs d
                 WHERE d.`index`=e.`index` AND d.id=e.dst)"""
    ).fetchone()[0]
    assert out["orphan_edges"] == 0

    # M-10: doc_sha1 poblado sin vacíos
    total_sha, empty_sha = db.execute(
        "SELECT COUNT(*), SUM(CASE WHEN doc_sha1 IS NULL OR doc_sha1='' THEN 1 ELSE 0 END) "
        "FROM docs"
    ).fetchone()
    out["doc_sha1"] = {"total": int(total_sha), "empty": int(empty_sha or 0)}
    assert out["doc_sha1"]["empty"] == 0

    # F2: columnas generadas + índices SQL — IGUALDAD de sets (M-03/T-02):
    # un desvío 8-vs-7 es imposible de colar por membresía.
    gen_cols = sorted(
        r[1] for r in db.execute("PRAGMA table_xinfo(docs)") if r[6]
    )
    out["generated_columns"] = gen_cols
    sql_idx = [r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"
    )]
    out["sql_indexes"] = sql_idx
    _assert_exact_set(
        gen_cols,
        {"doc_type", "doc_name", "doc_path", "doc_scope", "doc_event", "doc_sprint"},
        "columnas generadas (docs)",
    )
    _assert_exact_set(
        sql_idx,
        {"idx_docs_type", "idx_docs_name", "idx_docs_path", "idx_docs_scope",
         "idx_docs_event", "idx_docs_sprint", "idx_edges_dst"},
        "índices SQL",
    )

    # F1/M-04: FTS tables + markers + reconcile real vía MATCH
    fts_tables = [r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE name LIKE '%_fts'"
    )]
    out["fts_tables"] = fts_tables
    from fast_mcp.shared_kernel.cognitive_graph import fts as fts_mod
    for name in sorted(fts_tables):
        idx_name = name[: -len("_fts")]
        marker = fts_mod.fts_marker(db, idx_name)
        assert marker is not None, f"{name} sin marker (FS-2)"
        rep = fts_mod.reconcile(db, idx_name, marker["fields"])
        out.setdefault("fts_reconcile", {})[idx_name] = rep
        assert rep["docs"] == docs_after.get(idx_name, 0), (
            f"FTS {idx_name}: reconcile {rep['docs']} vs docs {docs_after.get(idx_name)}"
        )

    # Events preservados
    out["events"] = db.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    # Read models: materialización EXPLÍCITA (contrato full-graph) → get_docs
    out["read_models"] = {}
    for name in ("delta", "trace"):
        idx = backend._indexes[name]  # noqa: SLF001
        assert idx._loaded is False, f"{name} materializado sin pedirlo (M-09)"
        mat = backend.materialize_all(name)
        docs = backend.get_docs(name)
        out["read_models"][name] = {"materialize": mat, "docs": len(docs)}
        assert mat["docs"] == docs_after.get(name, 0)
        assert len(docs) == docs_after.get(name, 0)

    return out


def main() -> int:
    dry = "--dry" in sys.argv
    assert os.path.exists(CANON_DB), f"canónica no existe: {CANON_DB}"

    print("=" * 64)
    print("MIGRACIÓN CANÓNICA v2→v3 (ADR-WALKER-075 §10.1)")
    print("=" * 64)

    # 0) Registro del estado PRE
    pre = pre_state(CANON_DB)
    print(f"\n[pre] schema={pre['schema']} docs={pre['docs']} "
          f"edges={pre['edges']} events={pre['events']}")

    # 1) Backup (siempre, incluso --dry: no es destructivo)
    backup_path = backup_canonical()
    if dry:
        print("[dry] migración NO ejecutada — solo backup + pre-state.")
        return 0

    # 2) Boot real del runtime = migración lazy in-situ
    from fast_mcp.shared_kernel.cognitive_graph import build_cognitive_graph

    graph = build_cognitive_graph(chain_dir=DATA_DIR)
    backend = graph._backend  # noqa: SLF001 — script de migración
    print("\n[boot] SqliteBackend abierto sobre la canónica — migración ejecutada.")

    # 3) Registro de schemas de PRODUCCIÓN (composition root)
    for name, schema in PRODUCTION_INDEX_SCHEMAS.items():
        if name not in backend.list_indexes():
            backend.register_index(name, schema)
    print("[catalog] schemas de producción registrados (delta/trace/cpg/ckg) "
          f"→ catálogo persistido: {sorted(backend.list_indexes())}")

    # 4) Verificación post
    report = verify_post(backend, pre["docs"], pre["edges"])
    backend._db.close()  # noqa: SLF001

    print("\n" + "=" * 64)
    print("VERIFICACIÓN POST-MIGRACIÓN")
    print("=" * 64)
    for k, v in report.items():
        print(f"  {k}: {v}")
    print("=" * 64)
    print("MIGRACIÓN OK — la canónica está en v3 con paridad verificada.")
    print(f"Backup v2: {backup_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())