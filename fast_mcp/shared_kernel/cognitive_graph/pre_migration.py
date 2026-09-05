"""cognitive_graph/pre_migration.py — Gate H0 (ADR-WALKER-075 §10.0) como librería.

Deuda del Anexo A #1 (T-04 del plan de remediación): `pre_migration_check`
deja de ser un one-shot y pasa a ser un módulo importable del shared kernel,
invocable desde el boot del runtime o desde la CLI.

Contrato:
  - Cada check (H-0 guard v3, H-1..H-7) es una función que retorna
    `Result[CheckReport, StorageStateError]` — jamás excepción cruda
    cruzando la frontera de la librería (M-04).
  - Las remediaciones con mutación (purge H-3, cuarentena H-5, drop H-6)
    son `remediation_action`s SEPARADAS que exigen un backup verificado
    previo (T-01/M-02): sin backup, devuelven Failure. Los checks puros
    (H-1, H-2, H-4, H-7) nunca mutan datos.
  - `run_gate()` orquesta el flujo completo del gate: H-7 → guard v3 →
    backup verificado → checks → remediaciones → re-verificación final.
    Fail-fast: primer Failure propaga y ninguna mutación posterior ocurre.
  - Mensajes de error con remediación accionable (M-06).

Fail-fast / sin fallbacks (AGENTS.md): si un check no puede resolverse,
devuelve Failure(StorageStateError) con la lista específica de suciedad —
nunca `None` que parezca éxito.
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import time
from dataclasses import dataclass, field

from returns.result import Failure, Result, Success

from .storage_errors import StorageStateError

# H-6: se vacían; delta/trace son SSOT — NUNCA se tocan.
DERIVABLES = ("cpg", "ckg")

_SCHEMA_MARKER_SQL = (
    "SELECT value FROM meta WHERE `index`='__db__' AND key='__schema__'"
)
_BACKUP_PREFIX = "cognitive-pre-H0-"
_BACKUP_SUFFIX = ".sqlite3"


@dataclass(frozen=True)
class CheckReport:
    """Resultado de un check del gate H0 (verificación o remediación)."""

    check: str
    ok: bool
    detail: str
    remediation: str | None = None  # acción ejecutada (o recomendada en el error)


@dataclass(frozen=True)
class GateReport:
    """Resultado agregado del gate H0 tras ejecución completa exitosa."""

    checks: tuple[CheckReport, ...] = field(default_factory=tuple)
    backup_path: str | None = None
    final_ok: bool = False
    final_detail: str = ""


def _pre_state_counts(conn: sqlite3.Connection) -> tuple[dict[str, int], dict[str, int]]:
    docs = dict(conn.execute("SELECT `index`, COUNT(*) FROM docs GROUP BY `index`"))
    edges = dict(conn.execute("SELECT `index`, COUNT(*) FROM edges GROUP BY `index`"))
    return docs, edges


# ── H-0 (guard T-01): BD ya migrada ─────────────────────────────────────────

def check_not_migrated(
    conn: sqlite3.Connection, db_path: str
) -> Result[CheckReport, StorageStateError]:
    """Re-run guard: el gate es para estados pre-v3; nunca se re-ejecuta sobre una BD v3."""
    try:
        row = conn.execute(_SCHEMA_MARKER_SQL).fetchone()
    except sqlite3.OperationalError as exc:
        return Failure(
            StorageStateError(
                f"{db_path} no es una BD cognitiva walker (tabla meta ausente: {exc}). "
                "Remediación: restaurar la canónica desde .cognitive/backups/ o "
                "ejecutar la importación inicial."
            )
        )
    existing = row[0] if row else None
    if existing == "v3":
        return Failure(
            StorageStateError(
                f"{db_path} ya está en v3 — el gate H0 es para estados pre-v3; "
                "un re-run sobre BD migrada destruiría el catálogo (H-6 vacía "
                "cpg/ckg). Remediación: usar el flujo de rebuild dedicado de "
                "derivables (tarea del ADR-075), o restaurar el backup v2 desde "
                ".cognitive/backups/ si se quiere re-migrar."
            )
        )
    if existing not in (None, "v2"):
        return Failure(
            StorageStateError(
                f"schema_version '{existing}' desconocida (§10.1 fail-loud) — "
                "no se ejecuta el gate sobre un estado que no se entiende. "
                "Remediación: decidir backup/restore o upgrade antes de continuar."
            )
        )
    return Success(
        CheckReport(
            "H-0",
            True,
            f"schema={existing or 'sin marker'} — gate aplicable (pre-v3)",
        )
    )


# ── H-7: 0 ops pendientes ⇔ ningún proceso tiene la BD abierta ───────────────

def check_no_live_process(
    db_path: str,
) -> Result[CheckReport, StorageStateError]:
    """(_saved_docs/_saved_edges son estado en-memoria del backend; sin proceso
    vivo no puede existir diff sin commit)."""
    try:
        out = subprocess.run(
            ["lsof", "-t", db_path], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return Failure(
            StorageStateError(
                f"no se pudo verificar procesos con {db_path} abierta (lsof: {exc}). "
                "Remediación: verificar manualmente que ningún proceso walker "
                "tenga la BD abierta antes de ejecutar el gate."
            )
        )
    pids = [p for p in out.stdout.split() if p]
    if pids:
        return Failure(
            StorageStateError(
                f"procesos con la BD abierta (ops pendientes H-7): {pids}. "
                "Remediación: detenerlos antes de ejecutar el gate."
            )
        )
    return Success(CheckReport("H-7", True, "0 procesos con la BD abierta"))


# ── H-1: integridad ─────────────────────────────────────────────────────────

def check_integrity(
    conn: sqlite3.Connection,
) -> Result[CheckReport, StorageStateError]:
    try:
        ic = [r[0] for r in conn.execute("PRAGMA integrity_check")]
    except sqlite3.Error as exc:
        return Failure(
            StorageStateError(
                f"integrity_check no ejecutable: {exc} — BD ilegible/corrupta (FS-5). "
                "Remediación: restaurar el backup desde .cognitive/backups/."
            )
        )
    if ic != ["ok"]:
        return Failure(
            StorageStateError(
                f"integrity_check falló: {ic[:3]} — FS-5. Remediación: restaurar "
                "el backup desde .cognitive/backups/."
            )
        )
    return Success(CheckReport("H-1", True, "integrity ok"))


# ── H-2: WAL en cero (checkpoint TRUNCATE — remediación prescrita por el ADR,
#    no toca datos) ──────────────────────────────────────────────────────────

def check_wal_zero(
    conn: sqlite3.Connection, db_path: str
) -> Result[CheckReport, StorageStateError]:
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        wal = os.path.getsize(db_path + "-wal") if os.path.exists(db_path + "-wal") else 0
    except sqlite3.Error as exc:
        return Failure(
            StorageStateError(
                f"checkpoint TRUNCATE falló: {exc}. Remediación: detener "
                "lectores/escritores de la BD y re-ejecutar el gate."
            )
        )
    if wal >= 4096:
        return Failure(
            StorageStateError(
                f"-wal = {wal} bytes tras checkpoint TRUNCATE — readers activos "
                "lo impiden. Remediación: detener lectores y re-ejecutar el gate."
            )
        )
    return Success(CheckReport("H-2", True, f"-wal = {wal} bytes"))


# ── H-4: catálogo consistente (schema_hash por índice con docs) ─────────────

def check_catalog(
    conn: sqlite3.Connection,
) -> Result[CheckReport, StorageStateError]:
    meta_idx = {
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT `index` FROM meta WHERE key='schema_hash'"
        )
    }
    docs_idx = {r[0] for r in conn.execute("SELECT DISTINCT `index` FROM docs")}
    ghost_no_hash = docs_idx - meta_idx  # docs sin registrar → fallo (o derivable)
    hash_no_docs = meta_idx - docs_idx  # permitido: criterio del ADR es "docs ≥ 0"
    ghosts_non_derivable = ghost_no_hash - set(DERIVABLES)
    if ghosts_non_derivable:
        return Failure(
            StorageStateError(
                f"docs sin schema_hash: {sorted(ghosts_non_derivable)} — catálogo "
                "inconsistente (H-4). Remediación: registrar los schema_hash o "
                "purgar el catálogo fantasma."
            )
        )
    return Success(
        CheckReport(
            "H-4",
            True,
            f"docs-sin-hash={sorted(ghost_no_hash) or 0} (derivable: se purga en H-6); "
            f"hash-sin-docs (OK, docs≥0)={sorted(hash_no_docs) or 0}",
        )
    )


# ── Backup pre-destructivo VERIFICADO (T-01/M-02) ────────────────────────────

def make_verified_backup(
    conn: sqlite3.Connection,
    backup_dir: str,
    pre_docs: dict[str, int],
    pre_edges: dict[str, int],
) -> Result[str, StorageStateError]:
    """Snapshot pre-destructivo verificado contra el pre-estado.

    El backup DEBE contener exactamente el pre-estado (docs/edges) o se
    devuelve Failure ANTES de que el caller ejecute mutación alguna — un
    restore-point que no contiene el pre-estado no es un restore-point.
    """
    try:
        os.makedirs(backup_dir, exist_ok=True)
        bpath = os.path.realpath(
            os.path.join(
                backup_dir, f"{_BACKUP_PREFIX}{int(time.time())}{_BACKUP_SUFFIX}"
            )
        )
        dst = sqlite3.connect(bpath)
        try:
            conn.backup(dst)
        finally:
            dst.close()
        vb = sqlite3.connect(f"file:{bpath}?mode=ro", uri=True)
        try:
            integrity = [r[0] for r in vb.execute("PRAGMA integrity_check")]
            b_docs, b_edges = _pre_state_counts(vb)
        finally:
            vb.close()
    except (sqlite3.Error, OSError) as exc:
        return Failure(
            StorageStateError(
                f"backup falló: {exc}. Remediación: verificar permisos de "
                f"{backup_dir} y espacio en disco."
            )
        )
    if integrity != ["ok"] or b_docs != pre_docs or b_edges != pre_edges:
        return Failure(
            StorageStateError(
                f"backup verificado NO coincide con el pre-estado "
                f"(integrity={integrity}, docs={b_docs} vs {pre_docs}, "
                f"edges={b_edges} vs {pre_edges}) — no se ejecuta ninguna "
                "mutación. Remediación: revisar el estado de la BD antes de "
                "volver a intentar."
            )
        )
    return Success(bpath)


# ── Remediation actions (MUTAN datos — exigen backup verificado previo) ─────

def remediate_orphan_edges(
    conn: sqlite3.Connection, backup_path: str | None
) -> Result[CheckReport, StorageStateError]:
    """H-3: purge de edges huérfanos con listado (remediación del ADR)."""
    if backup_path is None:
        return Failure(
            StorageStateError(
                "H-3 (purge) requiere backup verificado previo (T-01/M-02). "
                "Remediación: ejecutar make_verified_backup antes de mutar."
            )
        )
    ecols = [r[1] for r in conn.execute("PRAGMA table_info(edges)")]
    src_c = "src" if "src" in ecols else ecols[2]
    dst_c = "dst" if "dst" in ecols else ecols[3]
    idx_c = "`index`"
    orphans = conn.execute(
        f"""SELECT e.{idx_c}, e.{src_c}, e.{dst_c} FROM edges e
            WHERE NOT EXISTS (
              SELECT 1 FROM docs d WHERE d.{idx_c}=e.{idx_c} AND d.id=e.{src_c})
               OR NOT EXISTS (
              SELECT 1 FROM docs d WHERE d.{idx_c}=e.{idx_c} AND d.id=e.{dst_c})"""
    ).fetchall()
    if not orphans:
        return Success(CheckReport("H-3", True, "0 edges huérfanos"))
    conn.execute(
        f"""DELETE FROM edges WHERE rowid IN (
              SELECT e.rowid FROM edges e
              WHERE NOT EXISTS (
                SELECT 1 FROM docs d WHERE d.{idx_c}=e.{idx_c} AND d.id=e.{src_c})
                 OR NOT EXISTS (
                SELECT 1 FROM docs d WHERE d.{idx_c}=e.{idx_c} AND d.id=e.{dst_c}))"""
    )
    conn.commit()
    return Success(
        CheckReport(
            "H-3",
            True,
            f"purge de {len(orphans)} edges huérfanos "
            f"(listado): {[tuple(r) for r in orphans[:20]]}"
            f"{' …' if len(orphans) > 20 else ''}",
            remediation="purge H-3",
        )
    )


def remediate_legacy_blobs(
    data_dir: str, backup_path: str | None
) -> Result[CheckReport, StorageStateError]:
    """H-5: cuarentena de blobs legacy (nunca borrado silencioso)."""
    if backup_path is None:
        return Failure(
            StorageStateError(
                "H-5 (cuarentena) requiere backup verificado previo (T-01/M-02). "
                "Remediación: ejecutar make_verified_backup antes de mutar."
            )
        )
    blobs = [
        f for f in os.listdir(data_dir)
        if f.endswith(".json.gz") or f.endswith(".json.gz.lock")
    ]
    if not blobs:
        return Success(CheckReport("H-5", True, "0 blobs legacy"))
    qb = os.path.join(data_dir, "legacy-blobs")
    os.makedirs(qb, exist_ok=True)
    for f in blobs:
        os.rename(os.path.join(data_dir, f), os.path.join(qb, f))
    return Success(
        CheckReport(
            "H-5",
            True,
            f"cuarentenados {len(blobs)}: {sorted(blobs)} -> legacy-blobs/",
            remediation="cuarentena H-5",
        )
    )


def remediate_derivables(
    conn: sqlite3.Connection, backup_path: str | None
) -> Result[CheckReport, StorageStateError]:
    """H-6: derivables cpg/ckg se VACÍAN (directiva PO — opción recomendada);
    delta/trace son SSOT: NUNCA se tocan."""
    if backup_path is None:
        return Failure(
            StorageStateError(
                "H-6 (drop derivables) requiere backup verificado previo "
                "(T-01/M-02). Remediación: ejecutar make_verified_backup antes de mutar."
            )
        )
    idx_c = "`index`"
    before = dict(conn.execute(f"SELECT {idx_c}, COUNT(*) FROM docs GROUP BY {idx_c}"))
    conn.execute(f"DELETE FROM edges WHERE {idx_c} IN {DERIVABLES}")
    conn.execute(f"DELETE FROM docs WHERE {idx_c} IN {DERIVABLES}")
    conn.execute(
        f"DELETE FROM meta WHERE key='schema_hash' AND {idx_c} IN {DERIVABLES}"
    )  # catálogo limpio: el índice se re-registra al reconstruir
    conn.commit()
    after = dict(conn.execute(f"SELECT {idx_c}, COUNT(*) FROM docs GROUP BY {idx_c}"))
    dropped = sum(before.get(k, 0) for k in DERIVABLES)
    ssot_intact = (
        after.get("delta", 0) == before.get("delta", 0)
        and after.get("trace", 0) == before.get("trace", 0)
    )
    if not ssot_intact or any(after.get(k, 0) != 0 for k in DERIVABLES):
        return Failure(
            StorageStateError(
                f"H-6 falló: dropped={dropped}, SSOT intacto? {after}. "
                f"Remediación: restaurar {backup_path}."
            )
        )
    return Success(
        CheckReport(
            "H-6",
            True,
            f"dropped {dropped} docs derivables; SSOT intacto: {after}",
            remediation="drop H-6",
        )
    )


# ── Orquestación ─────────────────────────────────────────────────────────────

def run_gate(
    db_path: str, data_dir: str, backup_dir: str
) -> Result[GateReport, StorageStateError]:
    """Ejecuta el gate H0 completo (fail-fast en el primer Failure).

    Orden: H-7 → guard v3 (H-0) → backup verificado (T-01) → H-1 → H-2 →
    H-3 (purge) → H-4 → H-5 (cuarentena) → H-6 (drop) → VACUUM →
    re-verificación final. Ninguna mutación ocurre sin backup verificado.
    """
    reports: list[CheckReport] = []

    r = check_no_live_process(db_path)
    if isinstance(r, Failure):
        return r
    reports.append(r.unwrap())

    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA busy_timeout=10000")
    except sqlite3.Error as exc:
        return Failure(
            StorageStateError(
                f"no se pudo abrir {db_path}: {exc}. Remediación: verificar "
                "permisos y que la BD exista (o restaurar desde .cognitive/backups/)."
            )
        )

    def _fail(err: StorageStateError) -> Result[GateReport, StorageStateError]:
        conn.close()
        return Failure(err)

    r = check_not_migrated(conn, db_path)
    if isinstance(r, Failure):
        return _fail(r.failure())
    reports.append(r.unwrap())

    # Backup PRE-destructivo verificado — antes de TODA mutación (T-01/M-02)
    try:
        pre_docs, pre_edges = _pre_state_counts(conn)
    except sqlite3.Error as exc:
        return _fail(
            StorageStateError(
                f"no se pudo leer el pre-estado de {db_path}: {exc}. "
                "Remediación: revisar la BD (puede no ser una BD cognitiva walker)."
            )
        )
    r = make_verified_backup(conn, backup_dir, pre_docs, pre_edges)
    if isinstance(r, Failure):
        return _fail(r.failure())
    backup_path = r.unwrap()

    # Checks puros
    r = check_integrity(conn)
    if isinstance(r, Failure):
        return _fail(r.failure())
    reports.append(r.unwrap())

    r = check_wal_zero(conn, db_path)
    if isinstance(r, Failure):
        return _fail(r.failure())
    reports.append(r.unwrap())

    # Remediation actions (todas con backup verificado)
    r = remediate_orphan_edges(conn, backup_path)
    if isinstance(r, Failure):
        return _fail(r.failure())
    reports.append(r.unwrap())

    r = check_catalog(conn)
    if isinstance(r, Failure):
        return _fail(r.failure())
    reports.append(r.unwrap())

    r = remediate_legacy_blobs(data_dir, backup_path)
    if isinstance(r, Failure):
        return _fail(r.failure())
    reports.append(r.unwrap())

    r = remediate_derivables(conn, backup_path)
    if isinstance(r, Failure):
        return _fail(r.failure())
    reports.append(r.unwrap())

    conn.execute("VACUUM")  # archivo físico compacto para la migración v3

    # Re-verificación final
    ic2 = [r[0] for r in conn.execute("PRAGMA integrity_check")]
    orph3 = conn.execute(
        f"""SELECT COUNT(*) FROM edges e WHERE NOT EXISTS (
              SELECT 1 FROM docs d WHERE d.`index`=e.`index` AND d.id=e.src)"""
    ).fetchone()[0]
    final_idx = dict(
        conn.execute("SELECT `index`, COUNT(*) FROM docs GROUP BY `index`")
    )
    size = os.path.getsize(db_path)
    conn.close()

    if ic2 != ["ok"] or orph3 != 0:
        return Failure(
            StorageStateError(
                f"re-verificación final falló: integrity={ic2}, "
                f"orphan_edges={orph3}. Remediación: restaurar {backup_path}."
            )
        )
    return Success(
        GateReport(
            checks=tuple(reports),
            backup_path=backup_path,
            final_ok=True,
            final_detail=f"integrity ok, orphan_edges=0, size={size} bytes, "
            f"docs por índice (post-H0): {final_idx}",
        )
    )