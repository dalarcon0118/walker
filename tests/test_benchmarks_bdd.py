"""tests/test_benchmarks_bdd.py — T-09 (M-5): metrics() truthful + benchmarks.

Cierra la deuda M-5 del plan de remediación ADR-WALKER-075:
  - M-11: metrics() reporta SOLO valores medidos (page_size real via pragma,
    WAL del archivo) — jamás la constante 4096 ni 0 silencioso.
  - M-03: recall fulltext tras restart < 50 ms sobre fixture 16K
    (umbral absoluto del ADR).
  - FS-5: presupuesto de escritura RELATIVO al baseline M0
    (≤ 1.5× p99 de `save_doc_nuevo_ms` en docs/baselines/storage-v2.json)
    — umbral absoluto inestable en CI → presupuesto relativo (plan T-09).
  - FS-8: el WAL tras un build completo es observable (metrics) y acotado.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from fast_mcp.shared_kernel.cognitive_graph.backends import resolve_storage_backend
from fast_mcp.shared_kernel.cognitive_graph.connection import MemoryConnection
from tests.benchmarks.cpg_fixture import (
    CPG_SCHEMA_CONFIG,
    build_cpg_fixture,
    sample_search_terms,
)

ROOT = Path(__file__).resolve().parents[1]
BASELINE = json.loads((ROOT / "docs" / "baselines" / "storage-v2.json").read_text())
BASELINE_SAVE_P99 = float(BASELINE["metrics"]["save_doc_nuevo_ms"]["p99"])
WRITE_BUDGET_MS = 1.5 * BASELINE_SAVE_P99
FULLTEXT_BUDGET_MS = 50.0  # M-03 del ADR: recall tras restart < 50 ms


def _make_backend(tmp_path):
    conn = MemoryConnection(backend="sqlite", uri=f"sqlite://{tmp_path}")
    backend = resolve_storage_backend(conn)
    backend.register_index("cpg", CPG_SCHEMA_CONFIG)
    return backend


def _reopen(tmp_path):
    conn = MemoryConnection(backend="sqlite", uri=f"sqlite://{tmp_path}")
    return resolve_storage_backend(conn)


def _build_16k(tmp_path) -> None:
    """Fixture determinista 16K docs + 24K edges (mismo shape que el M0)."""
    docs, edges = build_cpg_fixture()
    backend = _make_backend(tmp_path)
    backend.store_many("cpg", docs)
    backend.sync_edges("cpg", edges)
    backend._db.close()  # noqa: SLF001


class TestMetricsTruthful:
    """M-11: metrics() reporta valores medidos, jamás constantes."""

    def test_page_size_matches_real_pragma(self, tmp_path):
        # BD creada con page_size != 4096 → la métrica debe reflejarlo
        db = tmp_path / "cognitive.sqlite3"
        c = sqlite3.connect(str(db))
        c.execute("PRAGMA page_size=1024")
        c.execute("CREATE TABLE t (id TEXT)")
        c.commit()
        c.close()
        backend = _make_backend(tmp_path)
        m = backend.metrics("cpg")
        assert m["page_size"] == 1024, f"page_size inventado: {m['page_size']}"
        assert m["storage_size"] != "0.0KB"

    def test_wal_bytes_matches_disk(self, tmp_path):
        docs, edges = build_cpg_fixture()
        backend = _make_backend(tmp_path)
        backend.replace_blob("cpg", docs, edges, {"version": 2})
        m = backend.metrics("cpg")
        wal = tmp_path / "cognitive.sqlite3-wal"
        on_disk = wal.stat().st_size if wal.exists() else 0
        assert m["wal_bytes"] == on_disk, "wal_bytes no corresponde al archivo"
        assert m["wal_pages"] == m["wal_bytes"] // m["page_size"]
        backend._db.close()  # noqa: SLF001


class TestBenchmarkM03:
    """M-03: recall fulltext tras restart < 50 ms sobre fixture 16K."""

    def test_fulltext_recall_after_restart_within_budget(self, tmp_path):
        docs, _edges = build_cpg_fixture()
        _build_16k(tmp_path)
        backend2 = _reopen(tmp_path)
        terms = sample_search_terms(docs, n=10)  # términos reales con hits
        t0 = time.perf_counter()
        for term in terms:
            res = backend2.search("cpg", {"text": term})
            assert res["hits"]["total"] > 0, f"recall roto tras restart: {term}"
        avg_ms = (time.perf_counter() - t0) * 1000 / len(terms)
        assert avg_ms < FULLTEXT_BUDGET_MS, (
            f"recall fulltext tras restart: {avg_ms:.1f} ms promedio — "
            f"presupuesto M-03 {FULLTEXT_BUDGET_MS} ms"
        )


class TestBenchmarkFS5:
    """FS-5: la latencia de escritura no regresa frente al baseline M0."""

    def test_save_doc_within_relative_budget(self, tmp_path):
        docs, edges = build_cpg_fixture()
        backend = _make_backend(tmp_path)
        backend.store_many("cpg", docs)
        backend.sync_edges("cpg", edges)

        t0 = time.perf_counter()
        backend.store(
            "cpg", "cpg:function:bench",
            {"name": "fn_bench", "type": "function", "path": "src/bench.py",
             "line": 1, "signature": "def fn_bench():", "kind": "def"},
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000
        assert elapsed_ms < WRITE_BUDGET_MS, (
            f"save 1 doc sobre 16K: {elapsed_ms:.1f} ms — presupuesto relativo "
            f"{WRITE_BUDGET_MS:.1f} ms (1.5× baseline p99 {BASELINE_SAVE_P99} ms)"
        )
        # paridad: el doc persiste y responde tras restart
        backend._db.close()  # noqa: SLF001
        backend2 = _reopen(tmp_path)
        assert backend2.search("cpg", {"text": "bench"})["hits"]["total"] >= 1


class TestBenchmarkFS8:
    """FS-8: tras un build completo el WAL es observable y acotado."""

    def test_wal_bounded_after_build(self, tmp_path):
        docs, edges = build_cpg_fixture()
        backend = _make_backend(tmp_path)
        backend.replace_blob("cpg", docs, edges, {"version": 2})
        m = backend.metrics("cpg")
        # techo duro: baseline M0 dejó 12.4 MB tras build; 128 MB = el WAL no
        # crece sin límite (disco lleno) y su tamaño queda en metrics (FS-8).
        assert m["wal_bytes"] < 128 * 1024 * 1024, f"WAL sin techo: {m['wal_bytes']}"
        # los datos sobreviven al restart (build durable)
        backend._db.close()  # noqa: SLF001
        backend2 = _reopen(tmp_path)
        assert backend2.search("cpg", {"text": "fn_0"})["hits"]["total"] > 0