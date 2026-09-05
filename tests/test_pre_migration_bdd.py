"""tests/test_pre_migration_bdd.py — Gate H0 (T-01 + T-04 del plan de remediación).

T-01 (H-3/L9): backup pre-mutación.
  1. El backup se captura ANTES de la primera mutación (purge H-3): el
     backup contiene el pre-estado, incluidos los edges huérfanos que el
     propio gate purga después. Backup con extensión .sqlite3 en el
     restore-point documentado (.cognitive/backups equivalente en tmp).
  2. Restore desde el backup (failure_action) → conteos idénticos al
     pre-estado, edge huérfano recuperado.
  3. Re-run sobre BD ya migrada (meta.__schema__ == v3) → StorageStateError,
     cero mutaciones, cero backup, blob legacy sin cuarentenar.

T-04 (deuda Anexo A #1): gate H0 como librería.
  4. Cada check retorna Result[CheckReport, StorageStateError] — caso ok +
     fallo forzado (M-04/M-05); mensajes con remediación (M-06).
  5. Remediation actions mutantes exigen backup verificado (M-02/T-01).
  6. run_gate() orquesta el flujo completo (invocable desde boot/CLI).
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

from returns.result import Failure, Success

from fast_mcp.shared_kernel.cognitive_graph.pre_migration import (
    check_catalog,
    check_integrity,
    check_no_live_process,
    check_not_migrated,
    check_wal_zero,
    remediate_derivables,
    remediate_legacy_blobs,
    remediate_orphan_edges,
    run_gate,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "pre_migration_check.py"

V2_SCHEMA = """
CREATE TABLE docs (
  `index` TEXT NOT NULL, id TEXT NOT NULL, data TEXT NOT NULL,
  updated_at TEXT, PRIMARY KEY (`index`, id));
CREATE TABLE edges (
  `index` TEXT NOT NULL, src TEXT NOT NULL, dst TEXT NOT NULL,
  attributes TEXT, PRIMARY KEY (`index`, src, dst));
CREATE TABLE meta (
  `index` TEXT NOT NULL, key TEXT NOT NULL, value TEXT,
  PRIMARY KEY (`index`, key));
"""

DOCS = [
    ("delta", "s:1", '{"objective": "trabajo demo", "type": "spring"}', "2026-09-01T00:00:00Z"),
    ("delta", "s:2", '{"objective": "otro", "type": "spec"}', "2026-09-01T00:00:00Z"),
    ("trace", "t:1", '{"event_type": "ToolRegistered", "tool": "kg_search"}', "2026-09-01T00:00:01Z"),
    ("trace", "t:2", '{"event_type": "Submit", "node": "USER_GATE"}', "2026-09-01T00:00:02Z"),
]

# 1 edge válido (s:1→s:2) + 1 huérfano (src inexistente → H-3 lo purga)
EDGES = [
    ("delta", "s:1", "s:2", None),
    ("delta", "ghost", "s:1", None),
]

PRE_DOCS = {"delta": 2, "trace": 2}
PRE_EDGES = {"delta": 2}  # incluye el huérfano


def _build_v2_db(path: Path, data_dir: Path) -> None:
    """Fixture v2 real (mismo shape que el backup v2 de la canónica)."""
    c = sqlite3.connect(str(path))
    c.executescript(V2_SCHEMA)
    c.executemany(
        "INSERT INTO meta VALUES (?,?,?)",
        [
            ("__db__", "__schema__", "v2"),
            ("delta", "schema_hash", "fixture-hash-delta"),
            ("trace", "schema_hash", "fixture-hash-trace"),
        ],
    )
    c.executemany("INSERT INTO docs VALUES (?,?,?,?)", DOCS)
    c.executemany("INSERT INTO edges VALUES (?,?,?,?)", EDGES)
    c.commit()
    c.close()
    # blob legacy → cuarentena H-5 (nunca borrado silencioso)
    (data_dir / "legacy.json.gz").write_text("{}")
    (data_dir / "legacy.json.gz.lock").write_text("")


def _counts(conn: sqlite3.Connection, table: str) -> dict[str, int]:
    return dict(
        conn.execute(f"SELECT `index`, COUNT(*) FROM {table} GROUP BY `index`")
    )


def _run_gate(tmp_path: Path, db_name: str = "cognitive.sqlite3") -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["WALKER_DATA_DIR"] = str(tmp_path / "data")
    env["WALKER_DB_PATH"] = str(tmp_path / "data" / db_name)
    env["WALKER_BACKUP_DIR"] = str(tmp_path / "backups")
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True, text=True, env=env, cwd=ROOT,
    )


class TestBackupPreMutation:
    """T-01: el backup precede a toda mutación y es el restore-point fiel."""

    def test_backup_captured_before_purge_and_restore_restores(self, tmp_path):
        data = tmp_path / "data"
        data.mkdir()
        _build_v2_db(data / "cognitive.sqlite3", data)

        r = _run_gate(tmp_path)
        assert r.returncode == 0, f"gate falló:\n{r.stdout}\n{r.stderr}"

        # Backup único, extensión .sqlite3, en el restore-point documentado
        backups = list((tmp_path / "backups").glob("cognitive-pre-H0-*.sqlite3"))
        assert len(backups) == 1, f"backups inesperados: {backups}"
        assert not list((tmp_path / "backups").glob("*.db")), "extensión .db obsoleta"

        # El backup contiene el PRE-estado: incluye el edge huérfano que el
        # gate purgó DESPUÉS → prueba que el snapshot precede al primer DELETE
        b = sqlite3.connect(f"file:{backups[0]}?mode=ro", uri=True)
        assert _counts(b, "docs") == PRE_DOCS
        assert _counts(b, "edges") == PRE_EDGES
        assert [r[0] for r in b.execute("PRAGMA integrity_check")] == ["ok"]
        b.close()

        # La BD viva sí mutó (H-3 purgó el huérfano) — el backup no es de post-estado
        live = sqlite3.connect(str(data / "cognitive.sqlite3"))
        assert _counts(live, "edges") == {"delta": 1}
        live.close()

        # RESTORE (failure_action): backup → conteos idénticos al pre-estado
        shutil.copy(backups[0], data / "cognitive.sqlite3")
        restored = sqlite3.connect(str(data / "cognitive.sqlite3"))
        assert _counts(restored, "docs") == PRE_DOCS
        assert _counts(restored, "edges") == PRE_EDGES
        restored.close()

        # H-5 corrió tras el backup: blobs legacy cuarentenados
        assert (data / "legacy-blobs" / "legacy.json.gz").exists()
        assert (data / "legacy-blobs" / "legacy.json.gz.lock").exists()

    def test_rerun_on_v3_aborts_without_mutation(self, tmp_path):
        data = tmp_path / "data"
        data.mkdir()
        _build_v2_db(data / "cognitive.sqlite3", data)
        # marcar la BD como ya migrada → re-run ilegal
        c = sqlite3.connect(str(data / "cognitive.sqlite3"))
        c.execute(
            "UPDATE meta SET value='v3' WHERE `index`='__db__' AND key='__schema__'"
        )
        c.commit()
        c.close()

        r = _run_gate(tmp_path)
        assert r.returncode != 0, f"gate debió abortar:\n{r.stdout}"
        assert "GATE ABORTADO" in r.stderr
        assert "v3" in r.stderr

        # Cero mutaciones: conteos intactos, huérfano NO purgado, blob NO cuarentenado
        c = sqlite3.connect(str(data / "cognitive.sqlite3"))
        assert _counts(c, "docs") == PRE_DOCS
        assert _counts(c, "edges") == PRE_EDGES
        assert c.execute(
            "SELECT value FROM meta WHERE `index`='__db__' AND key='__schema__'"
        ).fetchone()[0] == "v3"
        c.close()
        assert not (data / "legacy-blobs").exists()
        assert (data / "legacy.json.gz").exists()

        # Cero backup creado
        assert list((tmp_path / "backups").glob("*")) == []


class TestChecks:
    """T-04 (M-04/M-05): cada check retorna Result — caso ok + fallo forzado."""

    def _v2(self, tmp_path) -> Path:
        data = tmp_path / "data"
        data.mkdir()
        _build_v2_db(data / "cognitive.sqlite3", data)
        return data / "cognitive.sqlite3"

    def test_check_not_migrated_ok_on_v2(self, tmp_path):
        db = self._v2(tmp_path)
        c = sqlite3.connect(str(db))
        r = check_not_migrated(c, str(db))
        assert isinstance(r, Success)
        assert r.unwrap().check == "H-0" and r.unwrap().ok
        c.close()

    def test_check_not_migrated_fails_on_v3(self, tmp_path):
        db = self._v2(tmp_path)
        c = sqlite3.connect(str(db))
        c.execute(
            "UPDATE meta SET value='v3' WHERE `index`='__db__' AND key='__schema__'"
        )
        c.commit()
        r = check_not_migrated(c, str(db))
        assert isinstance(r, Failure)
        assert "v3" in str(r.failure())
        assert "Remediación" in str(r.failure())
        c.close()

    def test_check_not_migrated_fails_on_unknown_schema(self, tmp_path):
        db = self._v2(tmp_path)
        c = sqlite3.connect(str(db))
        c.execute(
            "UPDATE meta SET value='v9' WHERE `index`='__db__' AND key='__schema__'"
        )
        c.commit()
        r = check_not_migrated(c, str(db))
        assert isinstance(r, Failure)
        assert "v9" in str(r.failure())
        c.close()

    def test_check_integrity_ok(self, tmp_path):
        db = self._v2(tmp_path)
        r = check_integrity(sqlite3.connect(str(db)))
        assert isinstance(r, Success) and r.unwrap().check == "H-1"

    def test_check_integrity_fails_on_corrupt_db(self, tmp_path):
        db = self._v2(tmp_path)
        with open(db, "r+b") as f:
            f.write(b"\x00" * 100)  # header destruido → ilegible (FS-5)
        r = check_integrity(sqlite3.connect(str(db)))
        assert isinstance(r, Failure)
        assert "Remediación" in str(r.failure())

    def test_check_wal_zero_ok(self, tmp_path):
        db = self._v2(tmp_path)
        c = sqlite3.connect(str(db))
        r = check_wal_zero(c, str(db))
        assert isinstance(r, Success) and r.unwrap().check == "H-2"
        c.close()

    def test_check_wal_zero_fails_when_wal_locked(self, tmp_path):
        db = tmp_path / "wal.sqlite3"
        c = sqlite3.connect(str(db))
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("CREATE TABLE t (id TEXT)")
        c.execute("BEGIN")  # transacción abierta → checkpoint no truncable
        c.execute("INSERT INTO t VALUES (?)", ("y" * 10000,))
        try:
            r = check_wal_zero(c, str(db))
            assert isinstance(r, Failure)
            assert "Remediación" in str(r.failure())
        finally:
            c.rollback()
            c.close()

    def test_check_no_live_process_ok(self, tmp_path):
        db = self._v2(tmp_path)
        r = check_no_live_process(str(db))
        assert isinstance(r, Success) and r.unwrap().check == "H-7"

    def test_check_no_live_process_fails_when_open(self, tmp_path):
        db = self._v2(tmp_path)
        c = sqlite3.connect(str(db))  # el proceso pytest mantiene la BD abierta
        try:
            r = check_no_live_process(str(db))
            assert isinstance(r, Failure)
            assert "procesos" in str(r.failure())
        finally:
            c.close()

    def test_check_catalog_ok(self, tmp_path):
        db = self._v2(tmp_path)
        r = check_catalog(sqlite3.connect(str(db)))
        assert isinstance(r, Success) and r.unwrap().check == "H-4"

    def test_check_catalog_fails_on_ghost_index(self, tmp_path):
        db = tmp_path / "ghost.sqlite3"
        c = sqlite3.connect(str(db))
        c.executescript(V2_SCHEMA)
        c.executemany(
            "INSERT INTO meta VALUES (?,?,?)",
            [("__db__", "__schema__", "v2"),
             ("delta", "schema_hash", "hash-delta")],  # trace sin schema_hash
        )
        c.executemany("INSERT INTO docs VALUES (?,?,?,?)", DOCS)
        c.commit()
        r = check_catalog(c)
        assert isinstance(r, Failure)
        assert "trace" in str(r.failure())
        c.close()


class TestRemediations:
    """T-04 (M-02): remediations mutan SOLO con backup verificado previo."""

    def test_orphan_purge_requires_backup(self, tmp_path):
        db = tmp_path / "x.sqlite3"
        _build_v2_db(db, tmp_path)
        r = remediate_orphan_edges(sqlite3.connect(str(db)), None)
        assert isinstance(r, Failure)
        assert "backup verificado" in str(r.failure())

    def test_legacy_blobs_requires_backup(self, tmp_path):
        r = remediate_legacy_blobs(str(tmp_path), None)
        assert isinstance(r, Failure)
        assert "backup verificado" in str(r.failure())

    def test_derivables_requires_backup(self, tmp_path):
        db = tmp_path / "x.sqlite3"
        _build_v2_db(db, tmp_path)
        r = remediate_derivables(sqlite3.connect(str(db)), None)
        assert isinstance(r, Failure)
        assert "backup verificado" in str(r.failure())

    def test_orphan_purge_purges_only_orphans(self, tmp_path):
        db = tmp_path / "x.sqlite3"
        _build_v2_db(db, tmp_path)
        c = sqlite3.connect(str(db))
        r = remediate_orphan_edges(c, "backup-ok")
        assert isinstance(r, Success)
        assert "purge de 1" in r.unwrap().detail
        assert dict(c.execute(
            "SELECT `index`, COUNT(*) FROM edges GROUP BY `index`"
        )) == {"delta": 1}  # solo se eliminó el huérfano
        c.close()

    def test_legacy_blobs_quarantine(self, tmp_path):
        data = tmp_path / "data"
        data.mkdir()
        (data / "legacy.json.gz").write_text("{}")
        r = remediate_legacy_blobs(str(data), "backup-ok")
        assert isinstance(r, Success)
        assert (data / "legacy-blobs" / "legacy.json.gz").exists()
        assert not (data / "legacy.json.gz").exists()

    def test_derivables_drop_keeps_ssot(self, tmp_path):
        db = tmp_path / "x.sqlite3"
        _build_v2_db(db, tmp_path)
        c = sqlite3.connect(str(db))
        c.execute("INSERT INTO docs VALUES ('cpg', 'c:1', '{}', NULL)")
        c.commit()
        r = remediate_derivables(c, "backup-ok")
        assert isinstance(r, Success)
        after = dict(c.execute(
            "SELECT `index`, COUNT(*) FROM docs GROUP BY `index`"
        ))
        assert after == {"delta": 2, "trace": 2}  # cpg vaciado; SSOT intacto
        assert "dropped 1" in r.unwrap().detail
        c.close()


class TestRunGateLibrary:
    """T-04: run_gate invocable desde boot (librería, no solo CLI)."""

    def test_run_gate_success_on_v2_fixture(self, tmp_path):
        data = tmp_path / "data"
        data.mkdir()
        _build_v2_db(data / "cognitive.sqlite3", data)
        r = run_gate(str(data / "cognitive.sqlite3"), str(data),
                     str(tmp_path / "backups"))
        assert isinstance(r, Success), r
        report = r.unwrap()
        assert report.final_ok
        assert report.backup_path is not None
        assert report.backup_path.endswith(".sqlite3")
        checks = {c.check for c in report.checks}
        assert {"H-0", "H-1", "H-2", "H-3", "H-4", "H-5", "H-6", "H-7"} <= checks
        # backup contiene el pre-estado (incluye el huérfano que H-3 purgó)
        b = sqlite3.connect(f"file:{report.backup_path}?mode=ro", uri=True)
        assert dict(b.execute(
            "SELECT `index`, COUNT(*) FROM edges GROUP BY `index`"
        )) == PRE_EDGES
        b.close()

    def test_run_gate_failure_on_v3(self, tmp_path):
        data = tmp_path / "data"
        data.mkdir()
        _build_v2_db(data / "cognitive.sqlite3", data)
        c = sqlite3.connect(str(data / "cognitive.sqlite3"))
        c.execute(
            "UPDATE meta SET value='v3' WHERE `index`='__db__' AND key='__schema__'"
        )
        c.commit()
        c.close()
        r = run_gate(str(data / "cognitive.sqlite3"), str(data),
                     str(tmp_path / "backups"))
        assert isinstance(r, Failure)
        assert "v3" in str(r.failure())
        assert list((tmp_path / "backups").glob("*")) == []  # sin backup en aborto