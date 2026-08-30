"""Tests — FileBackend como utilidad de EXPORT/seed (§6.6, ADR-WALKER-063).

ADR-WALKER-061 (F2) extrajo la E/S de Index a FileBackend. Desde el corte
directo (ADR-063) FileBackend NO es runtime (sqlite v2 es SSOT): se usa
solo como utilidad de export/migración/tests. Estos tests validan que:
  - el blob .json.gz resultante es IDÉNTICO en formato al histórico de
    Index: {docs, edges, metadata, token_index, _type_index, _field_index,
    schema_hash} (formato de EXPORT que consume migrate_cli.py);
  - la facade sin conn resuelve a sqlite (nunca a FileBackend).
"""
from __future__ import annotations

import json
import os
import tempfile

import pytest

from shared.libs.cognitive_graph.connection import MemoryConnection
from shared.libs.cognitive_graph.file_backend import FileBackend
from shared.libs.cognitive_graph.file_io import read_blob
from shared.libs.cognitive_graph.ports import StorageBackend


def _make_backend(tmp_dir: str) -> FileBackend:
    conn = MemoryConnection(uri=f"file://{tmp_dir}")
    fb = FileBackend(conn)
    fb.register_index(
        "ckg",
        {
            "fields": {"type": "type", "label": "statement"},
            "types": {"decision": {}, "refactor": {}},
            "storage": {"path": "ckg.json.gz", "format": "json.gz"},
            "search": {"type": "fulltext", "fields": ["statement", "rationale"]},
        },
    )
    return fb


def test_file_backend_satisfies_storage_backend_port():
    with tempfile.TemporaryDirectory() as d:
        fb = _make_backend(d)
        assert isinstance(fb, StorageBackend)


def test_file_backend_store_get_search_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        fb = _make_backend(d)
        fb.store("ckg", "decision-001", {"type": "decision", "statement": "usar DI"})
        got = fb.get("ckg", "decision-001")
        assert got is not None and got["_source"]["statement"] == "usar DI"
        result = fb.search("ckg", {"type": "decision"})
        assert result["hits"]["total"] == 1


def test_file_backend_persists_gz_with_identical_blob_format():
    with tempfile.TemporaryDirectory() as d:
        fb = _make_backend(d)
        fb.store("ckg", "decision-001", {"type": "decision", "statement": "x"})
        path = os.path.join(d, "ckg.json.gz")
        assert os.path.exists(path)
        raw = read_blob(path)
        assert raw is not None
        blob = json.loads(raw)
        # Mismo formato histórico de Index._save
        assert blob["index"] == "ckg"
        assert blob["schema_hash"]
        assert any(
            doc.get("_id") == "decision-001" for doc in blob["docs"]
        )


def test_file_backend_store_many_delete_many_max_key():
    with tempfile.TemporaryDirectory() as d:
        fb = _make_backend(d)
        fb.store_many(
            "ckg",
            {
                "decision-001": {"type": "decision", "a": 1},
                "decision-002": {"type": "decision", "a": 2},
                "refactor-001": {"type": "refactor", "a": 3},
            },
        )
        assert fb.max_key("ckg", "decision") == ("decision-002", 2)
        out = fb.delete_many("ckg", {"decision-001", "refactor-001"})
        assert out["deleted"] == 2
        assert fb.get("ckg", "decision-001") is None


def test_file_backend_reload_from_disk_is_consistent():
    """Tras escribir, un NUEVO FileBackend (misma ruta) ve los datos.

    Valida que el blob persistido es auto-suficiente (igual que Index).
    """
    with tempfile.TemporaryDirectory() as d:
        fb = _make_backend(d)
        fb.store("ckg", "decision-001", {"type": "decision", "statement": "x"})
        # Nuevo backend sobre el mismo directorio (simula reinicio)
        fb2 = _make_backend(d)
        got = fb2.get("ckg", "decision-001")
        assert got is not None and got["_source"]["statement"] == "x"


def test_cognitive_graph_accepts_backend_and_conn():
    """ADR-WALKER-061 F3: CognitiveGraph(backend, conn) sin storage en schema.

    El schema del índice NO lleva 'storage'; el filename se deriva del
    nombre ({name}.json.gz) y el directorio de MemoryConnection.uri.
    FileBackend se inyecta EXPLÍCITAMENTE aquí como utilidad de export/tests
    (§6.6 ADR-063): el runtime nunca lo selecciona solo (default = sqlite).
    """
    from shared.libs.cognitive_graph import CognitiveGraph

    with tempfile.TemporaryDirectory() as d:
        conn = MemoryConnection(uri=f"file://{d}")
        fb = FileBackend(conn)
        cg = CognitiveGraph(backend=fb, conn=conn)
        cg.register_index(
            "ckg",
            {
                "fields": {"type": "type"},
                "types": {"decision": {}},
                "search": {"type": "fulltext", "fields": ["statement"]},
            },
        )
        cg.store("ckg", "decision-001", {"type": "decision", "statement": "x"})
        assert cg.get("ckg", "decision-001")["_source"]["statement"] == "x"
        # Sin 'storage' en el schema registrado (IndexSchema solo tiene fields)
        assert "storage" not in cg._indexes["ckg"].fields
        # Persiste en <uri>/ckg.json.gz (filename derivado del nombre)
        assert os.path.exists(os.path.join(d, "ckg.json.gz"))


def test_index_schema_has_no_storage_key_when_absent():
    """IndexSchema sin 'storage' => storage_path None (deriva de nombre)."""
    from shared.libs.cognitive_graph.index import IndexSchema

    schema = IndexSchema({"fields": {"type": "type"}})
    assert schema.storage_path is None


def test_cognitive_graph_is_storage_backend_and_delegates():
    """ADR-WALKER-061 corregido: CognitiveGraph ES un StorageBackend y delega.

    La abstracción debe funcionar: CognitiveGraph implementa el puerto y
    delega al backend concreto. ADR-063 (corte directo): sin backend
    inyectado se resuelve desde la config → SqliteBackend (nunca
    FileBackend); el almacenamiento es v2 (cognitive.sqlite3), sin blobs.
    """
    from shared.libs.cognitive_graph import CognitiveGraph
    from shared.libs.cognitive_graph.ports import StorageBackend
    from shared.libs.cognitive_graph.sqlite_backend import SqliteBackend

    with tempfile.TemporaryDirectory() as d:
        # Sin conn/backend inyectados → config default sqlite (ADR-063).
        cg = CognitiveGraph(d)
        assert isinstance(cg, StorageBackend)
        assert isinstance(cg._backend, SqliteBackend)
        # Operaciones end-to-end via delegacion al backend.
        cg.store("ckg", "decision-001", {"type": "decision", "statement": "x"})
        assert cg.get("ckg", "decision-001")["_source"]["statement"] == "x"
        assert cg.search("ckg", {"type": "decision"})["hits"]["total"] == 1
        # Almacenamiento v2: BD sqlite en el dir; NINGÚN blob .json.gz creado.
        assert os.path.exists(os.path.join(d, "cognitive.sqlite3"))
        assert not os.path.exists(os.path.join(d, "ckg.json.gz"))


def test_graph_factory_accepts_memory_connection():
    """ADR-WALKER-061 F4: el service 'cognitive_graph' recibe MemoryConnection.

    El composition root construye CognitiveGraph(backend=FileBackend(conn),
    conn=conn). El backend es el dueño real del storage. Un path str sigue
    siendo aceptado por backward-compat (callers del engine).
    """
    from app.container import AppContainer
    from shared.libs.cognitive_graph.connection import MemoryConnection

    with tempfile.TemporaryDirectory() as d:
        app = AppContainer.build(force=True)
        factory = app.module_registry.get_service("cognitive_graph")
        # Canonico: MemoryConnection
        cg = factory(MemoryConnection(uri="file://" + d))
        cg.store("ckg", "d1", {"type": "decision", "statement": "x"})
        assert cg.get("ckg", "d1")["_source"]["statement"] == "x"
        assert isinstance(cg._backend, StorageBackend)
        # Backward-compat: path str (callers del engine que no conocen conn)
        cg2 = factory(d)
        cg2.store("ckg", "d2", {"type": "decision", "statement": "y"})
        assert cg2.get("ckg", "d2")["_source"]["statement"] == "y"
