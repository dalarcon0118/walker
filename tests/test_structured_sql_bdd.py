"""tests/test_structured_sql_bdd.py — F2 columnas generadas + índices SQL (D3).

Checklist de la fase (§5 D3/M-05..M-08):
  - M-05: filtros estructurados resuelven por índice SQL verificado con
    EXPLAIN QUERY PLAN en tests — anti-patrón: full materialize + filtro Python
  - M-06: columnas generadas VIRTUAL (0 duplicación de payload)
  - M-07: los hits cargan SOLO los docs matcheados (sin materializar el
    read model — json.loads por hit de la página, jamás SELECT * global)
  - M-08: contratos de search parametrizados sobre AMBOS backends
    (sqlite=SQL/FTS, File=TokenIndex) con semántica idéntica

Hallazgo documentado (fuera de scope F2): el schema de trace mapea
`event_type → "keyword"` (tipo ES, no campo) → sus filtros estructurados
caen al path base hoy y el router F2 preserva ese comportamiento exacto
(parity ≥ 0 hits). Corregir el mapping es decisión del PO (cambia
comportamiento: de 0 hits a hits correctos).
"""

from __future__ import annotations

import sqlite3

import pytest

from fast_mcp.shared_kernel.cognitive_graph import structured as structured_mod
from fast_mcp.shared_kernel.cognitive_graph.backends import resolve_storage_backend
from fast_mcp.shared_kernel.cognitive_graph.connection import MemoryConnection
from fast_mcp.shared_kernel.cognitive_graph.file_backend import FileBackend

CPG_SCHEMA = {
    "fields": {"function": "name", "file": "path", "type": "type"},
    "types": {"function": {}, "class": {}, "file": {}, "module": {}},
    "search": {"type": "fulltext", "fields": ["name", "path", "signature", "kind"]},
    "description": "CPG fixture (tests F2)",
}

DELTA_SCHEMA = {
    "fields": {
        "task": "spec_id",
        "spec": "spec_id",
        "spring": "spring_id",
        "domain": "domain",
        "type": "type",
    },
    "types": {"spec": {}, "spring": {}},
    "description": "DeltaGraph fixture (tests F2, sin fulltext)",
}


def _make_backend(tmp_path, schemas=("cpg", "delta")):
    conn = MemoryConnection(backend="sqlite", uri=f"sqlite://{tmp_path}")
    backend = resolve_storage_backend(conn)
    if "cpg" in schemas:
        backend.register_index("cpg", CPG_SCHEMA)
    if "delta" in schemas:
        backend.register_index("delta", DELTA_SCHEMA)
    return backend


def _seed_cpg(backend):
    backend.store_many("cpg", {
        "f:1": {"name": "fn_authenticate", "type": "function",
                "path": "src/auth.py", "line": 10,
                "signature": "def fn_authenticate(u: str) -> bool:", "kind": "def"},
        "f:2": {"name": "fn_login", "type": "function",
                "path": "src/login.py", "line": 20,
                "signature": "def fn_login(t: str) -> bool:", "kind": "def"},
        "f:3": {"name": "AuthService", "type": "class",
                "path": "src/auth.py", "line": 1,
                "signature": "class AuthService:", "kind": "class"},
        "f:4": {"name": "LegacyScope", "type": "class",
                "path": "src/legacy.py", "line": 5,
                "metadata": {"scope": "ckg:legacy"},
                "signature": "class LegacyScope:", "kind": "class"},
    })


def _hit_ids(result: dict) -> set[str]:
    return {h["_id"] for h in result["hits"]["hits"]}


def _reopen(tmp_path):
    conn = MemoryConnection(backend="sqlite", uri=f"sqlite://{tmp_path}")
    return resolve_storage_backend(conn)


class TestF2DDL:
    """M-06: columnas VIRTUAL (0 duplicación) — DDL idempotente."""

    def test_columns_are_virtual_and_indexes_exist(self, tmp_path):
        _make_backend(tmp_path)
        db = sqlite3.connect(str(tmp_path / "cognitive.sqlite3"))
        sql_docs = db.execute(
            "SELECT sql FROM sqlite_master WHERE name='docs'"
        ).fetchone()[0]
        assert "GENERATED ALWAYS" in sql_docs
        assert "VIRTUAL" in sql_docs
        assert "STORED" not in sql_docs  # M-06: jamás STORED
        for col in ("doc_type", "doc_name", "doc_path", "doc_scope",
                    "doc_event", "doc_sprint"):
            assert col in sql_docs
        idx_names = {r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}
        assert structured_mod.EDGES_DST_INDEX in idx_names
        assert "idx_docs_type" in idx_names and "idx_docs_sprint" in idx_names
        db.close()

    def test_ddl_idempotent_on_reopen(self, tmp_path):
        _make_backend(tmp_path)
        backend2 = _reopen(tmp_path)
        # segunda pasada del ensure → sin columnas nuevas
        report = structured_mod.ensure_structured_indexes(backend2._db)
        assert report["added_columns"] == []

    def test_virtual_columns_have_no_storage_cost(self, tmp_path):
        """M-06: la columna VIRTUAL no duplica payload — el valor vive en data."""
        backend = _make_backend(tmp_path)
        _seed_cpg(backend)
        db = sqlite3.connect(str(tmp_path / "cognitive.sqlite3"))
        # la fila cruda sigue siendo (index, id, data, updated_at) + col virtual
        raw = db.execute(
            "SELECT data FROM docs WHERE `index`='cpg' AND id='f:1'"
        ).fetchone()[0]
        assert raw == str(raw) and "fn_authenticate" in raw
        # y la columna resuelve igual que el JSON
        dt = db.execute(
            "SELECT doc_type, doc_name FROM docs WHERE `index`='cpg' AND id='f:1'"
        ).fetchone()
        assert dt == ("function", "fn_authenticate")
        db.close()


class TestF2Router:
    """M-07: solo los docs matcheados; sin materializar el read model."""

    def test_structured_route_avoids_materialization(self, tmp_path):
        backend = _make_backend(tmp_path)
        _seed_cpg(backend)
        backend._db.close()

        backend2 = _reopen(tmp_path)
        idx = backend2._indexes["cpg"]
        assert idx._loaded is False  # fresh: read model NO materializado
        res = backend2.search("cpg", {"type": "function"})
        assert _hit_ids(res) == {"f:1", "f:2"}
        assert idx._loaded is False  # M-07: la ruta SQL NO materializa
        assert res["hits"]["total"] == 2

    def test_type_filter_exact_case_sensitive(self, tmp_path):
        backend = _make_backend(tmp_path)
        _seed_cpg(backend)
        assert _hit_ids(backend.search("cpg", {"type": "function"})) == {"f:1", "f:2"}
        assert _hit_ids(backend.search("cpg", {"type": "Function"})) == set()  # exact

    def test_field_filter_case_insensitive(self, tmp_path):
        backend = _make_backend(tmp_path)
        _seed_cpg(backend)
        # "function"→name: case-insensitive (como _field_index lowered)
        assert _hit_ids(backend.search("cpg", {"function": "authservice"})) == {"f:3"}
        assert _hit_ids(backend.search("cpg", {"file": "SRC/AUTH.PY"})) == {"f:1", "f:3"}

    def test_metadata_scope_fallback_via_coalesce(self, tmp_path):
        backend = _make_backend(tmp_path)
        _seed_cpg(backend)
        # f:4 tiene scope SOLO en metadata — la columna lowered hace
        # COALESCE(top, metadata) y lo matchea con un solo índice
        assert _hit_ids(backend.search("cpg", {"scope": "ckg:legacy"})) == {"f:4"}

    def test_multi_filter_and(self, tmp_path):
        backend = _make_backend(tmp_path)
        _seed_cpg(backend)
        res = backend.search("cpg", {"type": "function", "file": "src/auth.py"})
        assert _hit_ids(res) == {"f:1"}

    def test_pagination_with_real_total(self, tmp_path):
        backend = _make_backend(tmp_path)
        _seed_cpg(backend)
        res = backend.search("cpg", {"type": "function", "max_results": 1})
        assert res["hits"]["total"] == 2  # total real (G8)
        assert _hit_ids(res) == {"f:1"}
        page2 = backend.search(
            "cpg", {"type": "function", "max_results": 1, "_from": 1}
        )
        assert _hit_ids(page2) == {"f:2"}
        assert page2["hits"]["from"] == 1

    def test_unresolvable_filter_falls_back_to_base(self, tmp_path):
        backend = _make_backend(tmp_path)
        _seed_cpg(backend)
        # campo sin columna generada (line) + valor int → path base (eager)
        res = backend.search("cpg", {"line": 20})
        assert _hit_ids(res) == {"f:2"}
        assert backend._indexes["cpg"]._loaded is True  # base materializa

    def test_non_fts_index_uses_sql_route(self, tmp_path):
        """El router estructurado NO depende de FTS (delta no tiene fulltext)."""
        backend = _make_backend(tmp_path, schemas=("delta",))
        backend.store_many("delta", {
            "s:1": {"type": "spec", "spec_id": "SPEC-001", "spring_id": "spring-1",
                    "domain": "storage", "title": "Storage spec"},
            "s:2": {"type": "spec", "spec_id": "SPEC-002", "spring_id": "spring-1",
                    "domain": "graph", "title": "Graph spec"},
            "sp:1": {"type": "spring", "spring_id": "spring-1",
                     "objective": "normalizar storage"},
        })
        backend._db.close()
        backend2 = _reopen(tmp_path)
        res = backend2.search("delta", {"type": "spec"})
        assert _hit_ids(res) == {"s:1", "s:2"}
        assert backend2._indexes["delta"]._loaded is False  # sin materializar
        # sprint_id vía doc_sprint (query field "spring" → "spring_id")
        assert _hit_ids(backend2.search("delta", {"spring": "spring-1"})) == {
            "s:1", "s:2", "sp:1"
        }


class TestF2Explain:
    """M-05: EXPLAIN QUERY PLAN verifica el uso del índice."""

    def test_explain_type_filter_uses_index(self, tmp_path):
        _make_backend(tmp_path)
        db = sqlite3.connect(str(tmp_path / "cognitive.sqlite3"))
        plan = db.execute(
            "EXPLAIN QUERY PLAN SELECT id, data FROM docs "
            "WHERE `index`=? AND doc_type=?", ("cpg", "function"),
        ).fetchall()
        plan_text = " ".join(str(r[-1]) for r in plan)
        assert "USING INDEX idx_docs_type" in plan_text  # M-05
        db.close()

    def test_explain_name_filter_uses_index(self, tmp_path):
        _make_backend(tmp_path)
        db = sqlite3.connect(str(tmp_path / "cognitive.sqlite3"))
        plan = db.execute(
            "EXPLAIN QUERY PLAN SELECT id, data FROM docs "
            "WHERE `index`=? AND doc_name=?", ("cpg", "fn_login"),
        ).fetchall()
        plan_text = " ".join(str(r[-1]) for r in plan)
        assert "USING INDEX idx_docs_name" in plan_text
        db.close()

    def test_explain_edges_dst_uses_index(self, tmp_path):
        """Los lookups por dst NUNCA full-scan: índice (PK covering o
        idx_edges_dst). En tabla vacía sqlite puede preferir el PK covering
        (coste igual); con datos/ANALYZE elige idx_edges_dst — lo que M-05
        exige es que el dst lookup sea indexado, jamás SCAN edges."""
        _make_backend(tmp_path)
        db = sqlite3.connect(str(tmp_path / "cognitive.sqlite3"))
        db.execute(
            "INSERT INTO edges (`index`, src, dst, attributes) VALUES "
            "('cpg', 'f:1', 'f:2', '{}'), ('cpg', 'f:2', 'f:1', '{}')"
        )
        plan = db.execute(
            "EXPLAIN QUERY PLAN SELECT src FROM edges "
            "WHERE `index`=? AND dst=?", ("cpg", "f:2"),
        ).fetchall()
        plan_text = " ".join(str(r[-1]) for r in plan)
        assert "SCAN edges" not in plan_text  # jamás full-scan
        assert "INDEX" in plan_text  # COVERING INDEX (PK) o idx_edges_dst
        db.close()


class TestF2ParidadBackends:
    """M-08: contratos parametrizados sobre AMBOS backends (semántica idéntica)."""

    FIXTURE = {
        "f:1": {"name": "fn_authenticate", "type": "function",
                "path": "src/auth.py", "line": 10, "kind": "def"},
        "f:2": {"name": "fn_login", "type": "function",
                "path": "src/login.py", "line": 20, "kind": "def"},
        "f:3": {"name": "AuthService", "type": "class",
                "path": "src/auth.py", "line": 1, "kind": "class"},
        "f:4": {"name": "LegacyScope", "type": "class",
                "path": "src/legacy.py", "line": 5, "kind": "class",
                "metadata": {"scope": "ckg:legacy"}},
    }

    QUERIES = (
        {"type": "function"},
        {"type": "Function"},          # exact: 0 hits en ambos
        {"function": "authservice"},   # lowered
        {"file": "src/auth.py"},
        {"scope": "ckg:legacy"},       # fallback metadata
        {"text": "auth"},              # fulltext
        {"text": "auth", "type": "class"},  # combinado
        {"line": 10},                  # no resoluble → path base en ambos
    )

    def test_sqlite_file_same_sets(self, tmp_path):
        conn = MemoryConnection(backend="sqlite", uri=f"sqlite://{tmp_path}/sqlite")
        sqlite_backend = resolve_storage_backend(conn)
        sqlite_backend.register_index("cpg", CPG_SCHEMA)
        sqlite_backend.store_many("cpg", self.FIXTURE)

        file_backend = FileBackend(
            MemoryConnection(backend="file", uri=f"file://{tmp_path}/file")
        )
        file_backend.register_index("cpg", CPG_SCHEMA)
        file_backend.store_many("cpg", self.FIXTURE)

        for q in self.QUERIES:
            r_sql = sqlite_backend.search("cpg", dict(q))
            r_file = file_backend.search("cpg", dict(q))
            assert _hit_ids(r_sql) == _hit_ids(r_file), (
                f"paridad rota para {q}: sql={_hit_ids(r_sql)} "
                f"file={_hit_ids(r_file)}"
            )
            assert r_sql["hits"]["total"] == r_file["hits"]["total"]


class TestF2FallbackVisible:
    """T-07 (M-09): el fallback eager (filtro sin columna generada) es
    VISIBLE — warning + métrica con docs materializados; jamás silencio.
    """

    def test_unsupported_filter_emits_warning_and_metric(self, tmp_path, caplog):
        import logging

        backend = _make_backend(tmp_path)
        _seed_cpg(backend)
        idx = backend._indexes["cpg"]  # noqa: SLF001
        assert idx._loaded is False  # estado ligero: el fallback materializará

        with caplog.at_level(
            logging.WARNING, logger="walker.cognitive_graph.sqlite_backend"
        ):
            res = backend.search("cpg", {"kind": "def"})

        assert "hits" in res and isinstance(res["hits"]["hits"], list)
        assert any("fallback EAGER" in r.message for r in caplog.records), (
            "fallback sin warning = degradación silenciosa (M-09)"
        )
        assert idx._fallback_materializations >= 1
        assert idx._fallback_last_docs == 4  # los 4 docs del fixture
        assert "kind" in idx._fallback_last_reason

        metric = backend.metrics("cpg")["fallback_eager"]
        assert metric["materializations"] >= 1
        assert metric["last_docs"] == 4

    def test_supported_filter_does_not_trip_fallback(self, tmp_path, caplog):
        import logging

        backend = _make_backend(tmp_path)
        _seed_cpg(backend)
        idx = backend._indexes["cpg"]  # noqa: SLF001

        with caplog.at_level(
            logging.WARNING, logger="walker.cognitive_graph.sqlite_backend"
        ):
            res = backend.search("cpg", {"type": "function"})

        assert _hit_ids(res) == {"f:1", "f:2"}
        assert not any("fallback EAGER" in r.message for r in caplog.records)
        assert idx._fallback_materializations == 0
        assert idx._loaded is False  # ruta SQL: 0 materialización (M-07)
