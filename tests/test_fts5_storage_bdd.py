"""tests/test_fts5_storage_bdd.py — F1 FTS5 external content (ADR-WALKER-075 D2).

Checklist de la fase (§5 D2/M-01..M-04):
  - M-01: el fulltext resuelve vía FTS5 MATCH (sin tokenizar/filtrar Python)
  - M-02: sync FTS en la MISMA transacción del `_save`; replace_blob resync
  - M-03: recall restart-proof (FTS vive en BD, no en RAM)
  - M-04: backfill verificado count(docs)==count(fts); drift → fail-fast

FS cubiertos: FS-1 (drift → StorageStateError accionable), FS-2 (tabla sin
marker → fail-fast; create+backfill+marker en UNA transacción).
"""

from __future__ import annotations

import sqlite3

import pytest

from fast_mcp.shared_kernel.cognitive_graph import fts as fts_mod
from fast_mcp.shared_kernel.cognitive_graph.backends import resolve_storage_backend
from fast_mcp.shared_kernel.cognitive_graph.connection import MemoryConnection
from fast_mcp.shared_kernel.cognitive_graph.storage_errors import (
    StorageContractError,
    StorageStateError,
)

CPG_SCHEMA = {
    "fields": {"function": "name", "file": "path", "type": "type"},
    "types": {"function": {}, "class": {}, "file": {}, "module": {}},
    "search": {
        "type": "fulltext",
        "fields": ["name", "path", "signature", "kind"],
    },
    "description": "CPG fixture (tests FTS5)",
}


def _make_backend(tmp_path):
    conn = MemoryConnection(backend="sqlite", uri=f"sqlite://{tmp_path}")
    backend = resolve_storage_backend(conn)
    backend.register_index("cpg", CPG_SCHEMA)
    return backend


def _seed_docs() -> dict[str, dict]:
    return {
        "cpg:function:1": {
            "name": "fn_authenticate", "type": "function",
            "path": "src/auth.py", "line": 10,
            "signature": "def fn_authenticate(user: str) -> bool:", "kind": "def",
        },
        "cpg:function:2": {
            "name": "fn_login", "type": "function",
            "path": "src/login.py", "line": 20,
            "signature": "def fn_login(token: str) -> bool:", "kind": "def",
        },
        "cpg:function:3": {
            "name": "fn_validate_auth", "type": "function",
            "path": "src/auth.py", "line": 30,
            "signature": "def fn_validate_auth(session: str) -> None:", "kind": "def",
        },
        "cpg:class:1": {
            "name": "AuthService", "type": "class",
            "path": "src/auth.py", "line": 1,
            "signature": "class AuthService:", "kind": "class",
        },
    }


def _reopen(tmp_path):
    conn = MemoryConnection(backend="sqlite", uri=f"sqlite://{tmp_path}")
    return resolve_storage_backend(conn)


def _hit_ids(result: dict) -> set[str]:
    return {h["_id"] for h in result["hits"]["hits"]}


class TestF1CreacionBackfill:
    """M-04: create+backfill+marker en una transacción, verificado por conteo."""

    def test_fts_created_with_marker_and_counts_equal(self, tmp_path):
        backend = _make_backend(tmp_path)
        backend.store_many("cpg", _seed_docs())

        c = sqlite3.connect(str(tmp_path / "cognitive.sqlite3"))
        marker = fts_mod.fts_marker(c, "cpg")
        assert marker is not None
        assert marker["fields"] == ["name", "path", "signature", "kind"]
        assert fts_mod.counts(c, "cpg") == (4, 4)  # M-04
        c.close()

    def test_reopen_is_idempotent_verified(self, tmp_path):
        backend = _make_backend(tmp_path)
        backend.store_many("cpg", _seed_docs())
        backend._db.close()

        backend2 = _reopen(tmp_path)
        assert backend2._indexes["cpg"]._fts_fields == CPG_SCHEMA["search"]["fields"]
        res = backend2.search("cpg", {"text": "authenticate"})
        assert _hit_ids(res) == {"cpg:function:1"}  # M-03: restart-proof


class TestF1RecallViaMatch:
    """M-01: el fulltext va por MATCH nativo (sin tokenización Python)."""

    def test_text_only_search_shape_bm25_total_pagination(self, tmp_path):
        backend = _make_backend(tmp_path)
        docs = _seed_docs()
        docs["cpg:function:4"] = {
            "name": "fn_auth_helper", "type": "function",
            "path": "src/auth/inner.py", "line": 40,
            "signature": "def fn_auth_helper(a: str, b: str) -> bool:", "kind": "def",
        }
        backend.store_many("cpg", docs)

        res = backend.search("cpg", {"text": "auth"})
        assert res["hits"]["total"] == 4  # total real (G8), no el cap
        assert res["hits"]["from"] == 0
        # bm25: fn_validate_auth tiene "auth" 2 veces (name+signature) y
        # AuthService 2 veces (name+signature) — el orden DEBE ser por rank
        scores = [h["_score"] for h in res["hits"]["hits"]]
        assert scores == sorted(scores, reverse=True)

        page2 = backend.search("cpg", {"text": "auth", "max_results": 2, "_from": 2})
        assert page2["hits"]["from"] == 2
        assert len(page2["hits"]["hits"]) == 2
        assert page2["hits"]["total"] == 4
        # páginas distintas: sin solapamiento de ids
        assert _hit_ids(page2).isdisjoint(
            {h["_id"] for h in res["hits"]["hits"][:2]}
        )

    def test_text_only_uses_fts_path_not_python(self, tmp_path):
        backend = _make_backend(tmp_path)
        backend.store_many("cpg", _seed_docs())
        idx = backend._indexes["cpg"]
        assert idx._fts_fields is not None
        # El hook resuelve por FTS: el TokenIndex NO se puebla en load
        # (M-03 anti-patrón: reconstruir token index en cada load).
        res = idx.search({"text": "login"})
        assert _hit_ids(res) == {"cpg:function:2"}

    def test_combined_query_intersects_structured_filter(self, tmp_path):
        backend = _make_backend(tmp_path)
        backend.store_many("cpg", _seed_docs())
        res = backend.search("cpg", {"text": "auth", "type": "class"})
        assert _hit_ids(res) == {"cpg:class:1"}

    def test_field_scoped_search_via_fts_columns(self, tmp_path):
        backend = _make_backend(tmp_path)
        backend.store_many("cpg", _seed_docs())
        res = backend.search(
            "cpg", {"text": {"query": "login", "fields": ["name"]}}
        )
        assert _hit_ids(res) == {"cpg:function:2"}

    def test_field_scoped_outside_fts_fails_fast(self, tmp_path):
        backend = _make_backend(tmp_path)
        backend.store_many("cpg", _seed_docs())
        with pytest.raises(StorageContractError, match="sin FTS"):
            backend.search(
                "cpg", {"text": {"query": "x", "fields": ["objetivo_fantasma"]}}
            )


class TestF1SyncTransaccional:
    """M-02: sync FTS en la MISMA transacción; rowid estable en updates."""

    def test_update_reflects_new_content_and_keeps_rowid(self, tmp_path):
        backend = _make_backend(tmp_path)
        backend.store_many("cpg", _seed_docs())
        idx = backend._indexes["cpg"]

        rowid_before = idx._db.execute(
            "SELECT rowid FROM docs WHERE `index`='cpg' AND id='cpg:function:2'"
        ).fetchone()[0]

        doc = dict(_seed_docs()["cpg:function:2"])
        doc["signature"] = "def fn_login_renovado(pass: str) -> bool:"
        backend.store("cpg", "cpg:function:2", doc)

        rowid_after = idx._db.execute(
            "SELECT rowid FROM docs WHERE `index`='cpg' AND id='cpg:function:2'"
        ).fetchone()[0]
        assert rowid_before == rowid_after  # upsert preserva rowid (D2)

        c = sqlite3.connect(str(tmp_path / "cognitive.sqlite3"))
        assert fts_mod.counts(c, "cpg") == (4, 4)  # sin drift tras update
        c.close()

        backend2 = _reopen(tmp_path)
        # token de la firma NUEVA presente; token SOLO de la firma VIEJA ausente
        assert _hit_ids(backend2.search("cpg", {"text": "renovado"})) == {
            "cpg:function:2"
        }
        assert _hit_ids(backend2.search("cpg", {"text": "token"})) == set()

    def test_delete_removes_fts_postings(self, tmp_path):
        backend = _make_backend(tmp_path)
        backend.store_many("cpg", _seed_docs())
        backend.delete("cpg", "cpg:function:2")

        backend2 = _reopen(tmp_path)
        assert _hit_ids(backend2.search("cpg", {"text": "fn_login"})) == set()
        c = sqlite3.connect(str(tmp_path / "cognitive.sqlite3"))
        assert fts_mod.counts(c, "cpg") == (3, 3)
        c.close()

    def test_replace_blob_resyncs_fts(self, tmp_path):
        backend = _make_backend(tmp_path)
        backend.store_many("cpg", _seed_docs())
        backend.replace_blob(
            "cpg",
            {"cpg:function:9": {
                "name": "fn_nuevo_mundo", "type": "function",
                "path": "src/nuevo.py", "signature": "def fn_nuevo_mundo():",
                "kind": "def",
            }},
            [],
            {"version": 2},
        )
        backend2 = _reopen(tmp_path)
        res = backend2.search("cpg", {"text": "mundo"})
        assert _hit_ids(res) == {"cpg:function:9"}
        assert backend2.search("cpg", {"text": "authenticate"})["hits"]["total"] == 0
        c = sqlite3.connect(str(tmp_path / "cognitive.sqlite3"))
        assert fts_mod.counts(c, "cpg") == (1, 1)
        c.close()


class TestF1FailFast:
    """FS-1/FS-2: drift y estados a medias → StorageStateError accionable."""

    def test_drift_fails_fast_on_boot(self, tmp_path):
        backend = _make_backend(tmp_path)
        backend.store_many("cpg", _seed_docs())
        backend._db.close()

        # writer pre-F1 simulado: INSERT directo SIN sync de FTS (FS-1) —
        # el doc lleva tokens fulltext para que sea detectable por sample
        c = sqlite3.connect(str(tmp_path / "cognitive.sqlite3"))
        c.execute(
            "INSERT INTO docs (`index`, id, data) VALUES "
            "('cpg', 'cpg:raw:1', '{\"name\": \"writer_pre_f1\", \"type\": \"function\"}')"
        )
        c.commit()
        c.close()

        with pytest.raises(StorageStateError, match="drift"):
            _reopen(tmp_path)

    def test_table_without_marker_fails_fast(self, tmp_path):
        backend = _make_backend(tmp_path)
        backend.store_many("cpg", _seed_docs())
        backend._db.close()

        c = sqlite3.connect(str(tmp_path / "cognitive.sqlite3"))
        c.execute(
            "DELETE FROM meta WHERE `index`='cpg' AND key='fts_config'"
        )
        c.commit()
        c.close()

        # FS-2: tabla FTS sin marker = migración a medias → fail-fast
        with pytest.raises(StorageStateError, match="a medias"):
            _reopen(tmp_path)

    def test_schema_fields_change_fails_fast(self, tmp_path):
        backend = _make_backend(tmp_path)
        backend.store_many("cpg", _seed_docs())
        backend._db.close()

        schema_changed = dict(CPG_SCHEMA)
        schema_changed["search"] = {
            "type": "fulltext",
            "fields": ["name", "path", "signature", "kind", "line"],
        }
        conn = MemoryConnection(backend="sqlite", uri=f"sqlite://{tmp_path}")
        backend2 = resolve_storage_backend(conn)  # boot verifica con schema viejo ✓
        backend2._db.close()
        # Ahora el código registra un schema DIFERENTE del marker → fail-fast
        conn2 = MemoryConnection(backend="sqlite", uri=f"sqlite://{tmp_path}")
        backend3 = resolve_storage_backend(conn2)
        with pytest.raises(StorageStateError, match="cambió"):
            backend3.register_index("cpg", schema_changed)

    def test_drop_fts_remediation_recovers(self, tmp_path):
        backend = _make_backend(tmp_path)
        backend.store_many("cpg", _seed_docs())
        backend._db.close()

        c = sqlite3.connect(str(tmp_path / "cognitive.sqlite3"))
        fts_mod.drop_fts(c, "cpg")  # remediación explícita (docs intactos)
        c.close()

        backend2 = _reopen(tmp_path)  # re-backfill transparente
        res = backend2.search("cpg", {"text": "authenticate"})
        assert _hit_ids(res) == {"cpg:function:1"}


class TestF1ProbeTokenizerParity:
    """El verificador y FTS5 deben tokenizar IGUAL.

    `models.tokenize` parte camelCase (`isMap` → `is`/`map`) y filtra
    stop-words; FTS5 (unicode61) indexa `ismap`. Sondear con el token de
    Python reportaba drift FALSO (reproducido 2026-11-22 con
    `cpg-506bc0a667cf`).
    """

    def test_probe_usa_la_granularidad_de_fts5(self):
        assert fts_mod._fts_probe(["isMap", "function"]) == "isMap"
        assert fts_mod._fts_probe(["", 42, "  fn_login(x)"]) == "42"
        assert fts_mod._fts_probe(["", "   "]) == ""

    def test_sample_verify_no_reporta_drift_falso_por_camelcase(self, tmp_path):
        backend = _make_backend(tmp_path)
        docs = _seed_docs()
        docs["cpg:function:9"] = {
            "name": "isMap", "type": "function",
            "path": "web/image.js", "line": 113,
            "signature": "export function isMap(image) {", "kind": "function",
        }
        backend.store_many("cpg", docs)
        backend._db.close()

        c = sqlite3.connect(str(tmp_path / "cognitive.sqlite3"))
        report = fts_mod._sample_verify(
            c, "cpg", ["name", "path", "signature", "kind"], sample_size=100
        )
        c.close()

        assert report["missing"] == []
        assert report["checked"] >= 1


class TestF1BootCheapVerify:
    """T-06 (M-08): boot en estado sano NO ejecuta full reconcile O(docs+terms).

    El verified-path usa COUNT docs↔fts + sample-verify acotado; el
    reconcile completo (fts5vocab + scans) solo corre si hay sospecha.
    """

    def test_healthy_reopen_skips_full_reconcile(self, tmp_path, monkeypatch):
        backend = _make_backend(tmp_path)
        backend.store_many("cpg", _seed_docs())
        backend._db.close()

        calls = {"n": 0}
        real_reconcile = fts_mod.reconcile

        def spy(*args, **kwargs):
            calls["n"] += 1
            return real_reconcile(*args, **kwargs)

        monkeypatch.setattr(fts_mod, "reconcile", spy)
        backend2 = _reopen(tmp_path)
        assert calls["n"] == 0, (
            "boot sano no debe ejecutar reconcile completo (M-08 anti-patrón: "
            "scan O(docs+terms) por apertura)"
        )
        # búsqueda operativa tras el boot barato (paridad M-03)
        res = backend2.search("cpg", {"text": "authenticate"})
        assert _hit_ids(res) == {"cpg:function:1"}

    def test_term_corruption_with_equal_counts_detected(self, tmp_path):
        """Término corrupto con conteos IGUALES (COUNT delega a la view):
        el sample-verify lo detecta y escala a reconcile → drift FS-1."""
        backend = _make_backend(tmp_path)
        backend.store_many("cpg", _seed_docs())
        backend._db.close()

        c = sqlite3.connect(str(tmp_path / "cognitive.sqlite3"))
        c.execute(
            "UPDATE docs SET data=? WHERE `index`='cpg' AND id='cpg:function:1'",
            ('{"name": "zanzibar unico", "type": "function", '
             '"path": "src/auth.py", "signature": "def zanzibar():", '
             '"kind": "def"}',),
        )
        c.commit()
        assert fts_mod.counts(c, "cpg") == (4, 4)  # conteos NO delatan el drift
        c.close()

        with pytest.raises(StorageStateError, match="drift"):
            _reopen(tmp_path)

    def test_insert_without_sync_still_escalates(self, tmp_path):
        """El conteo es la primera señal: doc insertado sin sync → full
        reconcile → StorageStateError accionable (FS-1, sin regresión)."""
        backend = _make_backend(tmp_path)
        backend.store_many("cpg", _seed_docs())
        backend._db.close()

        c = sqlite3.connect(str(tmp_path / "cognitive.sqlite3"))
        c.execute(
            "INSERT INTO docs (`index`, id, data) VALUES "
            "('cpg', 'cpg:raw:9', '{\"name\": \"writer_pre_f1\", "
            "\"type\": \"function\"}')"
        )
        c.commit()
        c.close()

        with pytest.raises(StorageStateError, match="drift"):
            _reopen(tmp_path)
