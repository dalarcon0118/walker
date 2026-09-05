"""tests/test_lazy_readmodel_bdd.py — F3 read model lazy + doc_sha1 + v3 (D4).

Checklist de la fase (§5 D4/M-09..M-12):
  - M-09: `_do_load` NO materializa docs por defecto (solo id+sha1+meta);
    `materialize_all()` explícito para consumidores full-graph
  - M-10: `doc_sha1` poblado en INSERT/UPDATE + backfill one-time +
    sample-verify en `metrics()` (FS-3)
  - M-11: re-save sin cambios = 0 writes (test de 701efe2 sigue verde lazy)
  - M-12: schema v3 con guard fail-loud para writers viejos + migración
    v2→v3 lazy in-situ (§10.1, FS-2)

FS-4 (sin lecturas parciales): paridad lazy/eager en tests — el resultado
de search text/structured es IDÉNTICO en índice ligero y materializado;
el write path en índice ligero usa tombstones (jamás `saved - current`,
que con docs no materializados borraría TODO lo no tocado).
"""

from __future__ import annotations

import sqlite3

import pytest

from fast_mcp.shared_kernel.cognitive_graph.backends import resolve_storage_backend
from fast_mcp.shared_kernel.cognitive_graph.connection import MemoryConnection
from fast_mcp.shared_kernel.cognitive_graph.storage_errors import (
    StorageSchemaVersionError,
    StorageStateError,
)

CPG_SCHEMA = {
    "fields": {"function": "name", "file": "path", "type": "type"},
    "types": {"function": {}, "class": {}},
    "search": {"type": "fulltext", "fields": ["name", "path", "signature"]},
    "description": "CPG fixture (tests F3)",
}

DOCS = {
    "f:1": {"name": "fn_authenticate", "type": "function",
            "path": "src/auth.py", "signature": "def fn_authenticate(u):"},
    "f:2": {"name": "fn_login", "type": "function",
            "path": "src/login.py", "signature": "def fn_login(t):"},
    "f:3": {"name": "AuthService", "type": "class",
            "path": "src/auth.py", "signature": "class AuthService:"},
}


def _make(tmp_path, seed=True):
    conn = MemoryConnection(backend="sqlite", uri=f"sqlite://{tmp_path}")
    backend = resolve_storage_backend(conn)
    backend.register_index("cpg", CPG_SCHEMA)
    if seed:
        backend.store_many("cpg", DOCS)
    return backend


def _reopen(tmp_path):
    conn = MemoryConnection(backend="sqlite", uri=f"sqlite://{tmp_path}")
    return resolve_storage_backend(conn)


def _hit_ids(result: dict) -> set[str]:
    return {h["_id"] for h in result["hits"]["hits"]}


def _db(tmp_path) -> sqlite3.Connection:
    return sqlite3.connect(str(tmp_path / "cognitive.sqlite3"))


class TestF3Lazy:
    """M-09: el read model NACE ligero; materialize_all es explícito."""

    def test_default_state_is_light_after_reopen(self, tmp_path):
        backend = _make(tmp_path)
        backend._db.close()
        backend2 = _reopen(tmp_path)
        idx = backend2._indexes["cpg"]
        assert idx._loaded is False          # NO materializado
        assert idx._meta_loaded is False     # ni siquiera light aún (lazy)
        assert idx._docs == {}
        # primer acceso light (sin materializar): keys() va al diff-cache
        assert set(idx.keys()) == set(DOCS)
        assert idx._loaded is False
        assert len(idx._saved_docs) == 3     # diff-cache completo (id+sha1)

    def test_structured_search_never_materializes(self, tmp_path):
        backend = _make(tmp_path)
        backend._db.close()
        backend2 = _reopen(tmp_path)
        idx = backend2._indexes["cpg"]
        res = backend2.search("cpg", {"type": "function"})
        assert _hit_ids(res) == {"f:1", "f:2"}
        assert idx._loaded is False  # ruta SQL: 0 materialización

    def test_text_search_never_materializes(self, tmp_path):
        backend = _make(tmp_path)
        backend._db.close()
        backend2 = _reopen(tmp_path)
        idx = backend2._indexes["cpg"]
        res = backend2.search("cpg", {"text": "auth"})
        assert "f:1" in _hit_ids(res)
        hit = res["hits"]["hits"][0]
        assert hit["_source"]["name"]  # _source llega por SQL (página)
        # _related solo aparece si el hit tiene edges (f:1 no tiene aquí)
        assert isinstance(hit.get("_related", []), list)
        assert idx._loaded is False  # la ruta FTS tampoco materializa

    def test_get_is_on_demand_single_doc(self, tmp_path):
        backend = _make(tmp_path)
        backend._db.close()
        backend2 = _reopen(tmp_path)
        idx = backend2._indexes["cpg"]
        doc = idx.get("f:2")
        assert doc["_id"] == "f:2" and doc["_source"]["name"] == "fn_login"
        assert idx._loaded is False         # 1 doc cacheado, NO el índice
        assert set(idx._docs) == {"f:2"}
        assert idx.get("no-existe") is None

    def test_get_docs_materializes_explicitly(self, tmp_path):
        backend = _make(tmp_path)
        backend._db.close()
        backend2 = _reopen(tmp_path)
        docs = backend2._indexes["cpg"].get_docs()
        assert set(docs) == set(DOCS)
        assert backend2._indexes["cpg"]._loaded is True  # base = eager

    def test_materialize_all_contract(self, tmp_path):
        backend = _make(tmp_path)
        backend._db.close()
        backend2 = _reopen(tmp_path)
        report = backend2.materialize_all("cpg")
        assert report["docs"] == 3
        assert backend2._indexes["cpg"]._loaded is True

    def test_fs4_parity_lazy_vs_eager(self, tmp_path):
        """El resultado de search es IDÉNTICO en índice ligero y materializado."""
        backend = _make(tmp_path)
        backend._db.close()
        backend2 = _reopen(tmp_path)
        res_light = backend2.search("cpg", {"text": "auth"})
        backend2.materialize_all("cpg")
        res_eager = backend2.search("cpg", {"text": "auth"})
        assert _hit_ids(res_light) == _hit_ids(res_eager)


class TestF3WritePath:
    """Writes SIN materializar: diff por doc_sha1 + tombstones."""

    def test_store_on_light_does_not_materialize(self, tmp_path):
        backend = _make(tmp_path)
        backend._db.close()
        backend2 = _reopen(tmp_path)
        idx = backend2._indexes["cpg"]
        res = backend2.store("cpg", "f:9", {
            "name": "fn_nuevo", "type": "function", "path": "src/n.py",
        })
        assert res["ok"] is True
        assert idx._loaded is False      # el write NO materializó (M-09)
        assert idx._saved_docs["f:9"]    # committed-state actualizado
        assert "f:1" in idx._saved_docs  # lo NO tocado SOBREVIVE
        backend3 = _reopen(tmp_path)
        assert set(backend3._indexes["cpg"].keys()) == set(DOCS) | {"f:9"}

    def test_delete_on_light_tombstone(self, tmp_path):
        backend = _make(tmp_path)
        backend._db.close()
        backend2 = _reopen(tmp_path)
        idx = backend2._indexes["cpg"]
        res = backend2.delete("cpg", "f:2")
        assert res["deleted"] is True
        assert idx._loaded is False
        assert "f:2" not in idx._saved_docs
        backend3 = _reopen(tmp_path)
        assert set(backend3._indexes["cpg"].keys()) == {"f:1", "f:3"}

    def test_delete_unknown_returns_false(self, tmp_path):
        backend = _make(tmp_path)
        res = backend.delete("cpg", "fantasma")
        assert res["deleted"] is False

    def test_delete_then_readd_same_doc(self, tmp_path):
        backend = _make(tmp_path)
        backend.delete("cpg", "f:2")
        backend.store("cpg", "f:2", {
            "name": "fn_login_v2", "type": "function", "path": "src/login.py",
        })
        backend2 = _reopen(tmp_path)
        doc = backend2._indexes["cpg"].get("f:2")
        assert doc["_source"]["name"] == "fn_login_v2"
        assert set(backend2._indexes["cpg"].keys()) == {"f:1", "f:2", "f:3"}

    def test_replace_blob_full_replace_via_tombstones(self, tmp_path):
        backend = _make(tmp_path)
        backend._db.close()
        backend2 = _reopen(tmp_path)
        backend2.replace_blob("cpg", {
            "f:9": {"name": "nuevo_mundo", "type": "function",
                    "path": "src/n.py"},
        }, [], {"version": 2})
        idx = backend2._indexes["cpg"]
        assert set(idx._saved_docs) == {"f:9"}   # lo viejo: tombstoned
        assert set(idx._docs) == {"f:9"}         # read model = estado nuevo
        assert idx._loaded is True               # post-replace = completo
        backend3 = _reopen(tmp_path)
        assert set(backend3._indexes["cpg"].keys()) == {"f:9"}
        assert backend3.search("cpg", {"text": "authenticate"})["hits"]["total"] == 0

    def test_sync_edges_on_light_keeps_docs_light(self, tmp_path):
        backend = _make(tmp_path)
        backend._db.close()
        backend2 = _reopen(tmp_path)
        backend2._indexes["cpg"].sync_edges([
            {"from": "f:1", "to": "f:2", "rel": "calls"},
        ])
        idx = backend2._indexes["cpg"]
        assert idx._loaded is False
        assert idx._graph.number_of_edges() == 1
        assert set(idx._saved_docs) == set(DOCS)  # docs intactos (light)
        backend3 = _reopen(tmp_path)
        idx3 = backend3._indexes["cpg"]
        idx3._ensure_light()  # el grafo de edges carga en el light-load
        assert idx3._graph.number_of_edges() == 1

    def test_m11_resave_without_changes_is_zero_writes(self, tmp_path):
        """M-11 (test de 701efe2, ahora lazy): re-save idéntico = 0 writes."""
        backend = _make(tmp_path)
        backend.store("cpg", "f:1", dict(DOCS["f:1"]))  # contenido idéntico
        db = _db(tmp_path)
        ts1 = db.execute(
            "SELECT updated_at FROM docs WHERE `index`='cpg' AND id='f:1'"
        ).fetchone()[0]
        db.close()
        backend.store("cpg", "f:1", dict(DOCS["f:1"]))  # mismo payload
        db = _db(tmp_path)
        ts2 = db.execute(
            "SELECT updated_at FROM docs WHERE `index`='cpg' AND id='f:1'"
        ).fetchone()[0]
        db.close()
        assert ts1 == ts2  # 0 writes (el diff por sha1 lo detecta)

    def test_metrics_counts_are_light_safe(self, tmp_path):
        backend = _make(tmp_path)
        backend._db.close()
        backend2 = _reopen(tmp_path)
        m = backend2.metrics("cpg")
        assert m["total_docs"] == 3  # diff-cache, NO len(_docs)==0


class TestF3DocSha1:
    """M-10/FS-3: doc_sha1 poblado, backfill, sample-verify en metrics."""

    def test_sha_populated_on_insert(self, tmp_path):
        backend = _make(tmp_path)
        db = _db(tmp_path)
        rows = db.execute(
            "SELECT id, doc_sha1 FROM docs WHERE `index`='cpg'"
        ).fetchall()
        db.close()
        assert all(sha for _id, sha in rows)
        assert len(rows) == 3

    def test_backfill_resumes_pending_rows(self, tmp_path):
        backend = _make(tmp_path)
        backend._db.close()
        db = _db(tmp_path)
        db.execute("UPDATE docs SET doc_sha1=''")  # simula crash a mitad
        db.commit()
        db.close()
        backend2 = _reopen(tmp_path)  # boot: pending > 0 → backfill re-anuda
        db = _db(tmp_path)
        shas = db.execute(
            "SELECT doc_sha1 FROM docs WHERE `index`='cpg'"
        ).fetchall()
        db.close()
        assert all(sha for (sha,) in shas)
        assert backend2.metrics("cpg")["total_docs"] == 3  # sample-verify OK

    def test_fs3_stale_sha_fails_fast_in_metrics(self, tmp_path):
        backend = _make(tmp_path)
        backend._db.close()
        db = _db(tmp_path)
        db.execute("UPDATE docs SET doc_sha1='corrupto' WHERE id='f:1'")
        db.commit()
        db.close()
        backend2 = _reopen(tmp_path)
        with pytest.raises(StorageStateError, match="doc_sha1 stale"):
            backend2.metrics("cpg")


class TestF3SchemaV3:
    """M-12: guard fail-loud + migración v2→v3 lazy in-situ (§10.1)."""

    def test_fresh_db_is_v3(self, tmp_path):
        _make(tmp_path, seed=False)
        db = _db(tmp_path)
        version = db.execute(
            "SELECT value FROM meta WHERE `index`='__db__' AND key='__schema__'"
        ).fetchone()[0]
        db.close()
        assert version == "v3"

    def test_v2_db_migrates_to_v3_on_boot(self, tmp_path):
        backend = _make(tmp_path)
        backend._db.close()
        db = _db(tmp_path)
        db.execute(
            "UPDATE meta SET value='v2' "
            "WHERE `index`='__db__' AND key='__schema__'"
        )
        db.commit()
        db.close()
        backend2 = _reopen(tmp_path)  # §10.1: migración lazy in-situ
        db = _db(tmp_path)
        version = db.execute(
            "SELECT value FROM meta WHERE `index`='__db__' AND key='__schema__'"
        ).fetchone()[0]
        db.close()
        assert version == "v3"
        # y la BD migrada funciona: datos intactos + searches operativos
        assert set(backend2._indexes["cpg"].keys()) == set(DOCS)
        assert backend2.search("cpg", {"text": "auth"})["hits"]["total"] >= 1

    def test_unknown_version_fails_loud(self, tmp_path):
        backend = _make(tmp_path)
        backend._db.close()
        db = _db(tmp_path)
        db.execute(
            "UPDATE meta SET value='v0' "
            "WHERE `index`='__db__' AND key='__schema__'"
        )
        db.commit()
        db.close()
        with pytest.raises(StorageSchemaVersionError, match="no soportado"):
            _reopen(tmp_path)


class TestEdgeWritePathTouched:
    """T-05 (M-07): el write path de edges es O(touched) — nunca O(E).

    El _save difiere SOLO edges tocados vía cache de raw (_edge_raws) +
    dirty set; json.dumps no se re-ejecuta sobre el grafo completo.
    """

    def _many_edges(self, n: int) -> list[dict]:
        return [
            {"from": f"n:{i}", "to": f"n:{i + 1}", "type": "chain"}
            for i in range(n)
        ]

    def test_roundtrip_add_remove_sync_parity(self, tmp_path):
        """Paridad CA-1/M-08: add/remove/sync sobreviven re-open idénticos."""
        backend = _make(tmp_path)  # 3 docs cpg
        backend.sync_edges("cpg", [
            {"from": "f:1", "to": "f:2", "type": "calls"},
            {"from": "f:2", "to": "f:3", "type": "uses"},
        ])
        backend.add_edge("f:1", "f:3", type="imports")
        backend.remove_edge("f:2", "f:3")
        # doc con edge incidente → delete debe borrar también sus edges
        backend.delete("cpg", "f:2")
        backend._db.close()

        backend2 = _reopen(tmp_path)
        edges = backend2.get_all_edges("cpg")
        assert sorted((e["from"], e["to"]) for e in edges) == [
            ("f:1", "f:3"),
        ]
        assert edges[0]["type"] == "imports"
        # SSOT: la tabla edges coincide exactamente con el read model
        db = _db(tmp_path)
        rows = db.execute(
            "SELECT src, dst, attributes FROM edges WHERE `index`='cpg'"
        ).fetchall()
        assert [(r[0], r[1]) for r in rows] == [("f:1", "f:3")]
        db.close()

    def test_sync_edges_replaces_all(self, tmp_path):
        backend = _make(tmp_path)
        backend.sync_edges("cpg", self._many_edges(500))
        backend.sync_edges("cpg", [{"from": "a", "to": "b", "type": "fresh"}])
        backend._db.close()

        backend2 = _reopen(tmp_path)
        edges = backend2.get_all_edges("cpg")
        assert [(e["from"], e["to"]) for e in edges] == [("a", "b")]
        assert edges[0]["type"] == "fresh"

    def test_dumps_per_save_do_not_scale_with_edge_count(self, tmp_path, monkeypatch):
        """Spy: add_edge sobre un índice con K edges hace ~1 dump, no K+1."""
        import json as json_mod

        backend = _make(tmp_path, seed=False)
        # add_edge del backend resuelve el índice por el nodo con fallback
        # 'delta' → registrar delta (vacío) para el spy
        backend.register_index("delta", {"fields": {"type": "type"}})
        backend.sync_edges("delta", self._many_edges(2000))  # K=2000, sin docs

        calls = {"n": 0}
        real_dumps = json_mod.dumps

        def spy(*args, **kwargs):
            calls["n"] += 1
            return real_dumps(*args, **kwargs)

        monkeypatch.setattr(
            "fast_mcp.shared_kernel.cognitive_graph.sqlite_backend.json.dumps", spy
        )
        backend.add_edge("n:0", "n:2001", type="spy")
        # constante 3 = 1 edge tocado + 2 overhead fijo por save
        # (_sha1(metadata) + _schema_hash) — INDEPENDIENTE del nº de edges;
        # el código previo serializaba K+1 (2001 dumps de edges).
        assert calls["n"] <= 3, (
            f"add_edge serializó {calls['n']} raws — esperado ~1 (solo el edge "
            f"tocado); el _save no debe re-serializar el grafo completo (M-07)"
        )
        # el edge tocado sí persiste (paridad)
        backend._db.close()
        backend2 = _reopen(tmp_path)
        edges = backend2.get_all_edges("delta")
        assert any(e["from"] == "n:0" and e["to"] == "n:2001" for e in edges)
        assert len(edges) == 2001

class TestGenerationCas:
    """T-12a (H-1): proceso stale NO puede escribir — CAS por generación.

    WAL multi-proceso está in-contract (ADR-WALKER-063 CA-8): el guard no es
    un lock de fichero sino un CAS transaccional sobre `__generation__`; el
    diff-cache stale falla con StorageStateError accionable (reabrir) en vez
    de corromper silenciosamente.
    """

    def test_stale_process_save_rejected(self, tmp_path):
        backend = _make(tmp_path)  # 3 docs → generación 1
        # "proceso B": abre ANTES de que A escriba y fuerza el light-load
        conn_b = MemoryConnection(backend="sqlite", uri=f"sqlite://{tmp_path}")
        b_backend = resolve_storage_backend(conn_b)
        b_idx = b_backend._indexes["cpg"]  # noqa: SLF001
        assert set(b_idx.keys()) == set(DOCS)  # cache con generación 1
        assert b_idx._generation == 1

        # "proceso A": escribe (generación 1 → 2)
        backend.store("cpg", "f:9", {"name": "fn_nueve", "type": "function",
                                      "path": "src/nueve.py",
                                      "signature": "def fn_nueve():", "kind": "def"})

        # B guarda con cache stale → CAS falla, cero corrupción silenciosa
        result = b_backend.store(
            "cpg", "f:10", {"name": "fn_diez", "type": "function",
                             "path": "src/diez.py",
                             "signature": "def fn_diez():", "kind": "def"})
        assert result.get("ok") is False
        assert "cambió bajo el cache" in result.get("error", "")
        db = _db(tmp_path)
        ids = {r[0] for r in db.execute("SELECT id FROM docs WHERE `index`='cpg'")}
        assert "f:9" in ids and "f:10" not in ids
        db.close()

    def test_same_process_no_false_positive(self, tmp_path):
        backend = _make(tmp_path)
        assert backend.store("cpg", "f:9", {"name": "fn_nueve", "type": "function",
                                             "path": "src/nueve.py",
                                             "signature": "def fn_nueve():", "kind": "def"})
        backend._db.close()
        backend2 = _reopen(tmp_path)  # re-lee la generación committed
        r = backend2.store("cpg", "f:10", {"name": "fn_diez", "type": "function",
                                            "path": "src/diez.py",
                                            "signature": "def fn_diez():", "kind": "def"})
        assert r.get("ok") is True  # CAS ok: sin falso positivo (FS-P3)

    def test_metadata_upsert_preserves_foreign_keys(self, tmp_path):
        backend = _make(tmp_path)
        backend.set_metadata("cpg", {"version": "1.0", "keep": True})
        # otra "proceso" escribe una key de metadata vía SQL directo
        db = _db(tmp_path)
        db.execute(
            "INSERT OR REPLACE INTO meta VALUES ('cpg','foreign_key','{\"x\": 1}')"
        )
        db.commit()
        db.close()
        # este proceso actualiza SOLO su metadata → la key extranjera sobrevive
        backend.set_metadata("cpg", {"version": "1.0"})
        db = _db(tmp_path)
        keys = {r[0] for r in db.execute("SELECT key FROM meta WHERE `index`='cpg'")}
        assert "foreign_key" in keys, "upsert-por-key borró metadata ajena"
        assert "keep" not in keys  # la removida explícitamente SÍ se elimina
        assert "version" in keys
        db.close()
