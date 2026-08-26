"""Tests — MemoryConnection + StorageBackend port (ADR-WALKER-061, F1).

F1 introduce los tipos SIN cambiar behavior: MemoryConnection (config, no
path) y StorageBackend (Protocol). Estos tests validan que los tipos son
correctos y que un adapter mínimo satisface el contrato del puerto.
"""
from __future__ import annotations

import pytest

from shared.core.cognitive_graph.connection import MemoryConnection
from shared.core.cognitive_graph.ports import StorageBackend


# ── MemoryConnection ────────────────────────────────────────────────────────

def test_memory_connection_defaults_are_file():
    conn = MemoryConnection()
    assert conn.backend == "file"
    assert conn.uri == "file://./data"
    assert conn.database == "walker"
    assert conn.collection_prefix == ""


def test_memory_connection_is_frozen_config_not_path():
    conn = MemoryConnection()
    with pytest.raises(Exception):  # dataclass frozen
        conn.backend = "postgres"  # type: ignore[misc]


def test_memory_connection_index_name_respects_prefix():
    bare = MemoryConnection()
    assert bare.index_name("ckg") == "ckg"
    prefixed = MemoryConnection(collection_prefix="walker_")
    assert prefixed.index_name("ckg") == "walker_ckg"


def test_memory_connection_no_filesystem_path_in_defaults():
    conn = MemoryConnection()
    # Invariante AP-4/AP-8: el uri es lógico, no un path de directorio crudo.
    assert "chain_dir" not in conn.uri
    assert "\\" not in conn.uri and "/" in conn.uri  # es uri, no path windows


# ── StorageBackend port ──────────────────────────────────────────────────────

class _InMemoryBackend:
    """Adapter mínimo de prueba (NO es FileBackend, eso es F2)."""

    def __init__(self):
        self._data: dict[str, dict[str, dict]] = {}

    def store(self, index, doc_id, doc):
        self._data.setdefault(index, {})[doc_id] = doc
        return {"ok": True, "_id": doc_id}

    def get(self, index, doc_id):
        doc = self._data.get(index, {}).get(doc_id)
        return {"_id": doc_id, "_source": doc} if doc is not None else None

    def search(self, index, query):
        docs = self._data.get(index, {})
        hits = [{"_id": k, "_source": v} for k, v in docs.items()]
        return {"hits": {"total": len(hits), "hits": hits}, "took": 0}

    def delete(self, index, doc_id):
        existed = doc_id in self._data.get(index, {})
        self._data.get(index, {}).pop(doc_id, None)
        return {"ok": True, "deleted": existed}

    def store_many(self, index, docs):
        self._data.setdefault(index, {}).update(docs)
        return {"ok": True, "stored": len(docs)}

    def delete_many(self, index, ids):
        store = self._data.get(index, {})
        deleted = sum(1 for i in ids if store.pop(i, None) is not None)
        return {"ok": True, "deleted": deleted}

    def max_key(self, index, prefix):
        best_id, best_num = None, -1
        for doc_id in self._data.get(index, {}):
            if doc_id.startswith(prefix + "-"):
                num = int(doc_id.rsplit("-", 1)[-1])
                if num > best_num:
                    best_num, best_id = num, doc_id
        return (best_id, best_num) if best_id else None


def test_storage_backend_protocol_satisfied_by_adapter():
    # El adapter real (FileBackend) implementa el puerto completo.
    from shared.core.cognitive_graph.file_backend import FileBackend

    conn = MemoryConnection(uri="file:///tmp")
    backend = FileBackend(conn)
    assert isinstance(backend, StorageBackend)  # runtime_checkable, puerto completo


def test_storage_backend_protocol_rejected_when_methods_missing():
    class _Partial:
        def store(self, index, doc_id, doc):
            return {}

    assert not isinstance(_Partial(), StorageBackend)


def test_in_memory_backend_roundtrip_store_get_search():
    backend = _InMemoryBackend()
    backend.store("ckg", "decision-001", {"statement": "x"})
    got = backend.get("ckg", "decision-001")
    assert got is not None and got["_source"]["statement"] == "x"
    result = backend.search("ckg", {})
    assert result["hits"]["total"] == 1


def test_in_memory_backend_store_many_delete_many_max_key():
    backend = _InMemoryBackend()
    backend.store_many("ckg", {
        "decision-001": {"a": 1},
        "decision-002": {"a": 2},
        "refactor-001": {"a": 3},
    })
    assert backend.max_key("ckg", "decision") == ("decision-002", 2)
    out = backend.delete_many("ckg", {"decision-001", "refactor-001"})
    assert out["deleted"] == 2
    assert backend.get("ckg", "decision-001") is None
