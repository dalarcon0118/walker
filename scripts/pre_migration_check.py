"""H0 — GATE §10.0 ADR-WALKER-075: pre_migration_check sobre la canónica.
Fail-fast con las remediaciones que el propio ADR prescribe (no abortos
donde hay remedio): checkpoint TRUNCATE (H-2), purge de edges huérfanos
con listado (H-3), cuarentena de blobs legacy (H-5), drop de derivables
cpg/ckg con SSOT delta/trace intocado (H-6). Backup pre-destructivo.

Correcciones (plan de remediación T-01, hallazgo H-3):
  - El backup se captura INMEDIATAMENTE tras abrir la conexión y ANTES de
    toda mutación (purge H-3 / cuarentena H-5 / drop H-6), y se VERIFICA
    contra el pre-estado (integrity + conteos) antes del primer DELETE.
  - Extensión .sqlite3 y directorio .cognitive/backups (mismo restore-point
    que scripts/migrate_canonical_v3.py).
  - Re-run sobre una BD ya migrada (meta.__schema__ == v3) → StorageStateError:
    nunca se re-ejecuta el gate de limpieza sobre una BD v3.
  - Rutas overrideables por env (WALKER_DATA_DIR / WALKER_DB_PATH /
    WALKER_BACKUP_DIR) para tests BDD; default = canónica del repo.
"""
import os
import sqlite3
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fast_mcp.shared_kernel.cognitive_graph.storage_errors import (  # noqa: E402
    StorageStateError,
)

REPO = os.environ.get("WALKER_REPO", ROOT)
DATA = os.environ.get(
    "WALKER_DATA_DIR",
    os.path.realpath(os.path.join(REPO, ".cognitive", "projects", "walker", "data")),
)
DB = os.environ.get(
    "WALKER_DB_PATH",
    os.path.join(DATA, "cognitive.sqlite3"),
)
BACKUP_DIR = os.environ.get(
    "WALKER_BACKUP_DIR",
    os.path.realpath(os.path.join(REPO, ".cognitive", "backups")),
)
DERIVABLES = ("cpg", "ckg")  # H-6: se vacían; delta/trace SSOT NUNCA
results = []


def verdict(check, ok, detail=""):
    results.append((check, "OK" if ok else "FAIL", detail))
    print(f"{check}: {'✅ OK' if ok else '❌ FAIL'}  {detail}")
    if not ok:
        print(f"  -> GATE ABORTADO en {check} (fail-fast §10.0)")
        sys.exit(1)


def pre_state_counts(c):
    docs = dict(c.execute("SELECT `index`, COUNT(*) FROM docs GROUP BY `index`"))
    edges = dict(c.execute("SELECT `index`, COUNT(*) FROM edges GROUP BY `index`"))
    return docs, edges


def make_backup(c, bdir, pre_docs, pre_edges):
    """Snapshot pre-destructivo VERIFICADO antes de toda mutación (H-3 fix).

    El backup debe contener exactamente el pre-estado (docs/edges) o el gate
    aborta ANTES de ejecutar el primer DELETE — un restore-point que no
    contiene el pre-estado no es un restore-point (M-02).
    """
    os.makedirs(bdir, exist_ok=True)
    bpath = os.path.realpath(
        os.path.join(bdir, f"cognitive-pre-H0-{int(time.time())}.sqlite3")
    )
    dst = sqlite3.connect(bpath)
    c.backup(dst)
    dst.close()
    vb = sqlite3.connect(f"file:{bpath}?mode=ro", uri=True)
    integrity = [r[0] for r in vb.execute("PRAGMA integrity_check")]
    b_docs, b_edges = pre_state_counts(vb)
    vb.close()
    ok = integrity == ["ok"] and b_docs == pre_docs and b_edges == pre_edges
    verdict(
        "BACKUP",
        ok,
        f"{os.path.basename(bpath)} — integrity={integrity}, docs={b_docs}, "
        f"edges={b_edges} (pre-estado: docs={pre_docs}, edges={pre_edges})",
    )
    return bpath


def main() -> int:
    # ── H-7: 0 ops pendientes ⇔ ningún proceso tiene la BD abierta ────────────
    # (_saved_docs/_saved_edges son estado en-memoria del backend; sin proceso
    #  vivo no puede existir diff sin commit)
    out = subprocess.run(["lsof", "-t", DB], capture_output=True, text=True)
    pids = [p for p in out.stdout.split() if p]
    verdict("H-7", not pids, f"procesos con la BD abierta (ops pendientes): {pids or 0}")

    c = sqlite3.connect(DB)
    c.execute("PRAGMA busy_timeout=10000")

    # ── Guard v3: re-run ilegal — nunca re-ejecutar el gate sobre una BD
    #    ya migrada (no "limpiar" una BD v3: H-6 vaciaría el catálogo) ─────────
    try:
        sv = c.execute(
            "SELECT value FROM meta WHERE `index`='__db__' AND key='__schema__'"
        ).fetchone()
    except sqlite3.OperationalError as exc:
        raise StorageStateError(
            f"{DB} no es una BD cognitiva walker (tabla meta ausente: {exc}). "
            "Remediación: restaurar la canónica desde .cognitive/backups/ o "
            "ejecutar la importación inicial."
        ) from exc
    if sv and sv[0] == "v3":
        raise StorageStateError(
            f"{DB} ya está en v3 ({sv[0]}) — el gate H0 es para estados pre-v3; "
            "un re-run sobre BD migrada destruiría el catálogo (H-6 vacía "
            "cpg/ckg). Remediación: usar el flujo de rebuild dedicado de "
            "derivables (tarea del ADR-075), o restaurar el backup v2 desde "
            ".cognitive/backups/ si se quiere re-migrar."
        )

    # ── Backup PRE-destructivo: antes de TODA mutación (purge H-3, cuarentena
    #    H-5, drop H-6). El pre-estado se captura aquí, antes del primer DELETE. ─
    pre_docs, pre_edges = pre_state_counts(c)
    bpath = make_backup(c, BACKUP_DIR, pre_docs, pre_edges)
    print(f"backup verificado: {bpath} ({os.path.getsize(bpath)} bytes)")

    # ── H-1: integridad ────────────────────────────────────────────────────────
    ic = c.execute("PRAGMA integrity_check").fetchall()
    verdict("H-1", ic == [("ok",)], str(ic[:3]))

    # ── H-2: WAL en cero (checkpoint TRUNCATE, prescribe el ADR) ──────────────
    c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    wal = os.path.getsize(DB + "-wal") if os.path.exists(DB + "-wal") else 0
    verdict("H-2", wal < 4096, f"-wal = {wal} bytes")

    # ── H-3: edges huérfanos → REMEDIACIÓN del ADR: purge con listado ─────────
    ecols = [r[1] for r in c.execute("PRAGMA table_info(edges)")]
    src_c = "src" if "src" in ecols else ecols[2]
    dst_c = "dst" if "dst" in ecols else ecols[3]
    idx_c = "`index`"
    orphans = c.execute(
        f"""SELECT e.{idx_c}, e.{src_c}, e.{dst_c} FROM edges e
            WHERE NOT EXISTS (
              SELECT 1 FROM docs d WHERE d.{idx_c}=e.{idx_c} AND d.id=e.{src_c})
               OR NOT EXISTS (
              SELECT 1 FROM docs d WHERE d.{idx_c}=e.{idx_c} AND d.id=e.{dst_c})"""
    ).fetchall()
    if orphans:
        print(f"  remediación H-3: purge de {len(orphans)} edges huérfanos (listado):")
        for row in orphans[:20]:
            print(f"    {row}")
        if len(orphans) > 20:
            print(f"    ... y {len(orphans) - 20} más")
        c.execute(
            f"""DELETE FROM edges WHERE rowid IN (
                  SELECT e.rowid FROM edges e
                  WHERE NOT EXISTS (
                    SELECT 1 FROM docs d WHERE d.{idx_c}=e.{idx_c} AND d.id=e.{src_c})
                     OR NOT EXISTS (
                    SELECT 1 FROM docs d WHERE d.{idx_c}=e.{idx_c} AND d.id=e.{dst_c}))"""
        )
        c.commit()

    # ── H-4: catálogo consistente (schema_hash por índice con docs) ───────────
    meta_idx = {
        r[0] for r in c.execute(
            "SELECT DISTINCT `index` FROM meta WHERE key='schema_hash'")
    }
    docs_idx = {r[0] for r in c.execute("SELECT DISTINCT `index` FROM docs")}
    ghost_no_hash = docs_idx - meta_idx   # docs sin registrar → fallo (o derivable)
    hash_no_docs = meta_idx - docs_idx    # permitido: criterio del ADR es "docs ≥ 0"
    verdict(
        "H-4",
        not (ghost_no_hash - set(DERIVABLES)),
        f"docs-sin-hash={sorted(ghost_no_hash)} (derivable: se purga en H-6); "
        f"hash-sin-docs (OK, docs≥0)={sorted(hash_no_docs) or 0}",
    )

    # ── H-5: cuarentena de blobs legacy (nunca borrado silencioso) ────────────
    blobs = [
        f for f in os.listdir(DATA)
        if f.endswith(".json.gz") or f.endswith(".json.gz.lock")
    ]
    if blobs:
        qb = os.path.join(DATA, "legacy-blobs")
        os.makedirs(qb, exist_ok=True)
        for f in blobs:
            os.rename(os.path.join(DATA, f), os.path.join(qb, f))
    verdict("H-5", True, f"cuarentenados {len(blobs)}: {sorted(blobs)} -> legacy-blobs/")

    # ── H-6: derivables cpg/ckg se VACÍAN (directiva PO — opción recomendada);
    #    delta/trace son SSOT: NUNCA se tocan ──────────────────────────────────
    before = dict(c.execute(f"SELECT {idx_c}, COUNT(*) FROM docs GROUP BY {idx_c}"))
    c.execute(f"DELETE FROM edges WHERE {idx_c} IN {DERIVABLES}")
    c.execute(f"DELETE FROM docs WHERE {idx_c} IN {DERIVABLES}")
    c.execute(
        f"DELETE FROM meta WHERE key='schema_hash' AND {idx_c} IN {DERIVABLES}"
    )  # catálogo limpio: el índice se re-registra al reconstruir
    c.commit()
    after = dict(c.execute(f"SELECT {idx_c}, COUNT(*) FROM docs GROUP BY {idx_c}"))
    dropped = sum(before.get(k, 0) for k in DERIVABLES)
    verdict(
        "H-6",
        all(after.get(k, 0) == 0 for k in DERIVABLES)
        and after.get("delta", 0) == before.get("delta", 0)
        and after.get("trace", 0) == before.get("trace", 0),
        f"dropped {dropped} docs derivables; SSOT intacto: {after}",
    )

    c.execute("VACUUM")  # archivo físico compacto para la migración v3

    # ── Re-verificación final ─────────────────────────────────────────────────
    ic2 = c.execute("PRAGMA integrity_check").fetchall()
    orph3 = c.execute(
        f"""SELECT COUNT(*) FROM edges e WHERE NOT EXISTS (
              SELECT 1 FROM docs d WHERE d.{idx_c}=e.{idx_c} AND d.id=e.{src_c})"""
    ).fetchone()[0]
    final_idx = dict(c.execute(f"SELECT {idx_c}, COUNT(*) FROM docs GROUP BY {idx_c}"))
    size = os.path.getsize(DB)
    c.close()
    print("\n=== VEREDICTO FINAL H0 ===")
    print(f"integrity: {ic2[0][0]}  orphan_edges: {orph3}  size: {size} bytes")
    print(f"docs por índice (post-H0): {final_idx}")
    print("H0: GATE VERDE — BD higienizada con backup pre-mutación verificado"
          if ic2 == [("ok",)] and orph3 == 0 else "H0: REVISAR")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except StorageStateError as exc:
        print(f"GATE ABORTADO (StorageStateError): {exc}", file=sys.stderr)
        sys.exit(1)