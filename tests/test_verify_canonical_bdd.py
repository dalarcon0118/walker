"""tests/test_verify_canonical_bdd.py — T-03 (M-7): verificación fail-loud.

Escenarios (validación de T-03 del plan de remediación ADR-WALKER-075):
  1. BD sana en tmp (v3 con FTS delta/cpg/ckg) → exit 0 (exit-code truthful).
  2. Drift FTS por tabla faltante (FS-2) → exit != 0.
  3. Drift FTS por desync docs↔FTS (FS-1) → exit != 0.

Un CI no puede dar verde ante drift: todo check termina en assert, exit
explícito o excepción fail-loud.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from fast_mcp.shared_kernel.cognitive_graph.backends import resolve_storage_backend
from fast_mcp.shared_kernel.cognitive_graph.connection import MemoryConnection
from scripts.migrate_canonical_v3 import PRODUCTION_INDEX_SCHEMAS

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_canonical_v3.py"

DELTA_DOCS = {
    "d:1": {"objective": "trabajo demo objetivo del verifier", "title": "T-03",
            "type": "spring", "spring_id": "sp-1", "domain": "storage"},
    "d:2": {"objective": "segundo objetivo con tokens", "title": "T-03 b",
            "type": "spec", "spec_id": "s-2", "domain": "storage"},
}
TRACE_DOCS = {
    "t:1": {"event_type": "ToolRegistered", "tool": "kg_search", "node": "start"},
    "t:2": {"event_type": "Submit", "node": "USER_GATE", "task_id": "T-03"},
}


def _build_healthy(tmp_path):
    """BD v3 completa en tmp: schemas de producción + docs delta/trace."""
    conn = MemoryConnection(backend="sqlite", uri=f"sqlite://{tmp_path}")
    backend = resolve_storage_backend(conn)
    for name in ("delta", "trace", "cpg", "ckg"):
        backend.register_index(name, PRODUCTION_INDEX_SCHEMAS[name])
    backend.store_many("delta", DELTA_DOCS)
    backend.store_many("trace", TRACE_DOCS)
    backend._db.close()  # noqa: SLF001


def _run_verify(tmp_path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["WALKER_DATA_DIR"] = str(tmp_path)
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True, text=True, env=env, cwd=ROOT,
    )


class TestVerifyFailLoud:
    """M-01: todo check termina en assert o exit explícito — nunca FAIL + exit 0."""

    def test_healthy_db_exits_zero(self, tmp_path):
        _build_healthy(tmp_path)
        r = _run_verify(tmp_path)
        assert r.returncode == 0, f"BD sana no debió fallar:\n{r.stdout}\n{r.stderr}"
        assert "SMOKE OK" in r.stdout

    def test_missing_fts_table_exits_nonzero(self, tmp_path):
        _build_healthy(tmp_path)
        c = sqlite3.connect(str(tmp_path / "cognitive.sqlite3"))
        c.execute("DROP TABLE delta_fts")  # FS-2: marker sin tabla → boot fail-loud
        c.commit()
        c.close()
        r = _run_verify(tmp_path)
        assert r.returncode != 0, "drift FTS (tabla faltante) no puede dar exit 0"
        assert "StorageStateError" in r.stderr

    def test_fts_desync_exits_nonzero(self, tmp_path):
        _build_healthy(tmp_path)
        c = sqlite3.connect(str(tmp_path / "cognitive.sqlite3"))
        # doc insertado fuera del backend (writer pre-F1): sin postings FTS
        c.execute(
            "INSERT INTO docs (`index`, id, data, updated_at) VALUES (?,?,?,?)",
            ("delta", "d:3", '{"objective": "doc extra sin sync fts"}', "2026-09-05T00:00:00Z"),
        )
        c.commit()
        c.close()
        r = _run_verify(tmp_path)
        assert r.returncode != 0, "drift FTS (desync docs↔fts) no puede dar exit 0"
        assert "StorageStateError" in r.stderr