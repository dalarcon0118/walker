"""tests/test_migrate_verify_post_bdd.py — T-02 (M-1/L10): verify_post por
IGUALDAD de sets y fail-fast del tipo de backend.

Escenarios (validación de T-02 del plan de remediación ADR-WALKER-075):
  1. Esquema canónico en BD tmp → verify_post pasa: 6 columnas generadas,
     7 índices SQL (patrón de completitud de AGENTS.md).
  2. Índice extra → AssertionError listando el exceso (un "8-vs-7" no puede
     colarse por membresía).
  3. Índice faltante → AssertionError listando la falta.
  4. Backend de otro tipo → fail-fast (L10) con mensaje accionable.
"""

from __future__ import annotations

import pytest

from fast_mcp.shared_kernel.cognitive_graph.backends import resolve_storage_backend
from fast_mcp.shared_kernel.cognitive_graph.connection import MemoryConnection
from scripts.migrate_canonical_v3 import PRODUCTION_INDEX_SCHEMAS, verify_post

EXPECTED_GEN_COLS = {
    "doc_type", "doc_name", "doc_path", "doc_scope", "doc_event", "doc_sprint",
}
EXPECTED_SQL_INDEXES = {
    "idx_docs_type", "idx_docs_name", "idx_docs_path", "idx_docs_scope",
    "idx_docs_event", "idx_docs_sprint", "idx_edges_dst",
}

DELTA_DOCS = {
    "d:1": {"objective": "trabajo demo objetivo", "title": "T-02 fixture",
            "type": "spring", "spring_id": "sp-1", "domain": "storage"},
    "d:2": {"objective": "otro objetivo", "title": "segundo doc",
            "type": "spec", "spec_id": "s-2", "domain": "storage"},
}
TRACE_DOCS = {
    "t:1": {"event_type": "ToolRegistered", "tool": "kg_search", "node": "start"},
    "t:2": {"event_type": "Submit", "node": "USER_GATE", "task_id": "T-02"},
}
EXPECTED_DOCS = {"delta": 2, "trace": 2}


def _make_full(tmp_path):
    """Backend real sobre tmp: esquema v3 completo (mismo flujo que la canónica:
    registrar schemas de producción → store → re-apertura lazy)."""
    conn = MemoryConnection(backend="sqlite", uri=f"sqlite://{tmp_path}")
    backend = resolve_storage_backend(conn)
    for name in ("delta", "trace"):
        backend.register_index(name, PRODUCTION_INDEX_SCHEMAS[name])
    backend.store_many("delta", DELTA_DOCS)
    backend.store_many("trace", TRACE_DOCS)
    backend._db.close()  # noqa: SLF001
    conn2 = MemoryConnection(backend="sqlite", uri=f"sqlite://{tmp_path}")
    return resolve_storage_backend(conn2)


class TestVerifyPostEquality:
    """M-03: la validación DDL es por igualdad de sets, no membresía."""

    def test_canonical_schema_passes(self, tmp_path):
        backend = _make_full(tmp_path)
        try:
            report = verify_post(backend, EXPECTED_DOCS, {})
            assert set(report["generated_columns"]) == EXPECTED_GEN_COLS
            assert set(report["sql_indexes"]) == EXPECTED_SQL_INDEXES
        finally:
            backend._db.close()  # noqa: SLF001

    def test_extra_index_fails_listing_excess(self, tmp_path):
        backend = _make_full(tmp_path)
        db = backend._db  # noqa: SLF001
        db.execute("CREATE INDEX idx_zz_extra ON docs(id)")
        db.commit()
        try:
            with pytest.raises(AssertionError) as ei:
                verify_post(backend, EXPECTED_DOCS, {})
            assert "exceso=" in str(ei.value)
            assert "idx_zz_extra" in str(ei.value)
        finally:
            backend._db.close()  # noqa: SLF001

    def test_missing_index_fails_listing_missing(self, tmp_path):
        backend = _make_full(tmp_path)
        db = backend._db  # noqa: SLF001
        db.execute("DROP INDEX idx_edges_dst")
        db.commit()
        try:
            with pytest.raises(AssertionError) as ei:
                verify_post(backend, EXPECTED_DOCS, {})
            assert "falta=" in str(ei.value)
            assert "idx_edges_dst" in str(ei.value)
        finally:
            backend._db.close()  # noqa: SLF001

    def test_wrong_backend_type_fails_fast(self, tmp_path):
        with pytest.raises(AssertionError) as ei:
            verify_post(object(), {}, {})
        assert "SqliteBackend" in str(ei.value)