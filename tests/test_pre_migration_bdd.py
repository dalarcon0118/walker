"""tests/test_pre_migration_bdd.py — T-01 (H-3/L9): backup pre-mutación del gate H0.

Escenarios (validación de T-01 del plan de remediación ADR-WALKER-075):
  1. El backup se captura ANTES de la primera mutación (purge H-3): el
     backup contiene el pre-estado, incluidos los edges huérfanos que el
     propio gate purga después. Backup con extensión .sqlite3 en el
     restore-point documentado (.cognitive/backups equivalente en tmp).
  2. Restore desde el backup (failure_action) → conteos idénticos al
     pre-estado, edge huérfano recuperado.
  3. Re-run sobre BD ya migrada (meta.__schema__ == v3) → StorageStateError,
     cero mutaciones, cero backup, blob legacy sin cuarentenar.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

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