"""Tests — SqliteBackend prototipo Fase 1 (ADR-WALKER-063).

Cubre el checklist de aceptación de Fase 1 (§6.1) validando contra el
comportamiento del puerto real:
  - completitud del contrato: 27/27 métodos (AGENTS.md: test de completitud).
  - CA-1  append de 1 doc = 1 INSERT (sin write amplification, triggers audit).
  - CA-2  persistencia/roundtrip tras reabrir la BD (sin batching).
  - CA-4  search estructurado (read model en memoria, IV-3).
  - H5    max_key (secuencias decision-NNN).
  - H6    edges cross-index (add_edge/get_related/find_node_index).
  - IV-6/IV-7  errores custom (StorageContractError, StorageSchemaVersionError,
    StorageIntegrityError) y shape explícito de sin-hits.
  - IV-5  compact/flush no-op.
  - CA-3  smoke de rendimiento (1000 appends).
"""
from __future__ import annotations

import sqlite3
import time

import pytest

from shared.libs.cognitive_graph import CognitiveGraph
from shared.libs.cognitive_graph.backends import (
    available_backends,
    resolve_storage_backend,
)
from shared.libs.cognitive_graph.config import (
    ENV_MEMORY_BACKEND,
    ENV_MEMORY_URI,
    load_memory_connection,
)
from shared.libs.cognitive_graph.file_backend import FileBackend
from shared.libs.cognitive_graph.file_io import write_blob
from shared.libs.cognitive_graph.migrations import (
    import_json_gz_to_sqlite,
)
from shared.libs.cognitive_graph.connection import MemoryConnection
from shared.libs.cognitive_graph.ports import StorageBackend
from shared.libs.cognitive_graph.sqlite_backend import SqliteBackend
from shared.libs.cognitive_graph.storage_errors import (
    StorageContractError,
    StorageIntegrityError,
    StorageMigrationCountMismatchError,
    StorageMigrationError,
    StorageSchemaVersionError,
)
from shared.core.engine.infrastructure.trace_log import TraceLogger

TRACE_SCHEMA = {
    "fields": {"type": "event_type", "task": "task_id", "session": "session_id"},
    "types": {"trace": {"description": "evento de auditoría"}},
    "search": {"type": "structured"},
    "description": "índice de prueba (trace-like)",
}

EXPECTED_METHODS = {
    # 24 de StorageBackend (ADR-061) + 3 de ADR-063 = 27
    "register_index", "store", "store_many", "get", "delete", "delete_many",
    "max_key", "search", "add_edge", "remove_edge", "get_edges", "get_related",
    "replace_blob", "sync_edges", "set_metadata", "get_metadata", "get_docs",
    "get_all_edges", "keys", "upsert", "schema_info", "metrics", "list_indexes",
    "find_node_index",
    "append", "compact", "flush",
}


def _backend(tmp_path) -> SqliteBackend:
    conn = MemoryConnection(backend="sqlite", uri="file://" + str(tmp_path))
    backend = SqliteBackend(conn)
    backend.register_index("trace", TRACE_SCHEMA)
    return backend


def _audit_setup(db) -> None:
    """Triggers de auditoría para medir writes reales (CA-1)."""
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS _audit (op TEXT);
        DROP TRIGGER IF EXISTS trg_docs_i;
        DROP TRIGGER IF EXISTS trg_docs_u;
        DROP TRIGGER IF EXISTS trg_docs_d;
        CREATE TRIGGER IF NOT EXISTS trg_docs_i AFTER INSERT ON docs
            BEGIN INSERT INTO _audit(op) VALUES ('i'); END;
        CREATE TRIGGER IF NOT EXISTS trg_docs_u AFTER UPDATE ON docs
            BEGIN INSERT INTO _audit(op) VALUES ('u'); END;
        CREATE TRIGGER IF NOT EXISTS trg_docs_d AFTER DELETE ON docs
            BEGIN INSERT INTO _audit(op) VALUES ('d'); END;
        """
    )


def _audit_counts(db) -> dict[str, int]:
    rows = db.execute("SELECT op, COUNT(*) FROM _audit GROUP BY op").fetchall()
    counts = {"i": 0, "u": 0, "d": 0}
    for op, n in rows:
        counts[op] = n
    return counts


# ── Completitud del puerto (AGENTS.md) ───────────────────────────────────────

def test_port_completeness_27_methods(tmp_path):
    backend = _backend(tmp_path)
    assert isinstance(backend, StorageBackend)  # runtime_checkable
    missing = EXPECTED_METHODS - set(dir(backend))
    assert not missing, f"Faltan métodos del puerto: {missing}"


# ── CA-1: append de 1 doc NO reescribe docs ajenos (sin write amplification) ─

def test_ca1_single_store_is_single_insert(tmp_path):
    backend = _backend(tmp_path)
    _audit_setup(backend._db)  # noqa: SLF001 (métricas de test)
    backend.store_many(
        "trace", {f"evt-{i:04d}": {"event_type": "ToolInvoked"} for i in range(100)}
    )
    backend._db.execute("DELETE FROM _audit")  # noqa: SLF001 — reset tras setup
    backend.store("trace", "evt-new", {"event_type": "ToolInvoked"})
    counts = _audit_counts(backend._db)  # noqa: SLF001
    assert counts == {"i": 1, "u": 0, "d": 0}, (
        f"append de 1 doc debe ser 1 INSERT (CA-1/IV-1), got {counts}"
    )
    # El doc ajeno permanece intacto
    raw = backend._db.execute(  # noqa: SLF001
        "SELECT data FROM docs WHERE `index`='trace' AND id='evt-0000'"
    ).fetchone()[0]
    assert '"event_type": "ToolInvoked"' in raw


# ── CA-2/CA-6: persistencia durable, roundtrip sin batching ──────────────────

def test_persistence_roundtrip_and_counts(tmp_path):
    backend = _backend(tmp_path)
    backend.store_many(
        "trace",
        {f"evt-{i:03d}": {"event_type": "kg_query", "task_id": f"task-{i}"} for i in range(50)},
    )
    backend.append("trace", {"evt-x": {"event_type": "WorkflowForced"}})

    # Reabrir la BD (nuevo proceso/backend) → los datos committed sobreviven
    reopened = _backend(tmp_path)
    docs = reopened.get_docs("trace")
    assert len(docs) == 51  # conteo sqlite == lo escrito (CA-6)
    assert docs["evt-x"]["event_type"] == "WorkflowForced"
    assert reopened.get("trace", "evt-000")["_source"]["task_id"] == "task-0"
    # events (append-only) con el mismo conteo
    events = reopened._db.execute(  # noqa: SLF001
        "SELECT COUNT(*) FROM events WHERE `index`='trace'"
    ).fetchone()[0]
    assert events == 1


# ── CA-4: search estructurado sobre el read model (IV-3) ─────────────────────

def test_ca4_structured_search_and_replay_filter(tmp_path):
    backend = _backend(tmp_path)
    backend.store_many(
        "trace",
        {
            "e1": {"event_type": "kg_query", "task_id": "T-1", "session_id": "S-1"},
            "e2": {"event_type": "ToolRegistered", "task_id": "T-2", "session_id": "S-1"},
            "e3": {"event_type": "ToolRegistered", "task_id": "T-3", "session_id": "S-2"},
        },
    )
    hits = backend.search("trace", {"type": "ToolRegistered"})
    assert hits["hits"]["total"] == 2
    ids = {h["_id"] for h in hits["hits"]["hits"]}
    assert ids == {"e2", "e3"}
    # replay con filtro por session (gate: has_kg_query)
    replayed = backend.search("trace", {"type": "kg_query", "session": "S-1"})
    assert replayed["hits"]["total"] == 1
    assert replayed["hits"]["hits"][0]["_id"] == "e1"


# ── IV-7: shape explícito de sin-hits (nunca {} silencioso) ──────────────────

def test_iv7_empty_search_has_explicit_shape(tmp_path):
    backend = _backend(tmp_path)
    backend.store("trace", "e1", {"event_type": "ToolRegistered"})
    result = backend.search("trace", {"type": "no-existe"})
    assert "hits" in result and result["hits"]["total"] == 0
    assert result["hits"]["hits"] == []
    assert "took" in result and "index" in result  # no dict vacío


# ── H5: max_key — secuencias por prefijo (CKG decision-NNN) ──────────────────

def test_h5_max_key_sequence(tmp_path):
    backend = _backend(tmp_path)
    backend.store_many(
        "trace",
        {
            "decision-001": {"event_type": "decision"},
            "decision-002": {"event_type": "decision"},
            "refactor-001": {"event_type": "refactor"},
        },
    )
    assert backend.max_key("trace", "decision") == ("decision-002", 2)
    assert backend.max_key("trace", "refactor") == ("refactor-001", 1)
    assert backend.max_key("trace", "zzz") is None


# ── H6: edges cross-index (find_node_index/add_edge/get_related) ─────────────

def test_h6_edges_cross_index(tmp_path):
    backend = _backend(tmp_path)
    backend.register_index("delta", {"fields": {"type": "type"}, "description": "d"})
    backend.store("trace", "cpg:src/auth.py", {"event_type": "file"})
    backend.store("delta", "delta:TASK-042", {"type": "task", "objective": "fix auth"})
    assert backend.find_node_index("delta:TASK-042") == "delta"
    assert backend.find_node_index("cpg:src/auth.py") == "trace"

    out = backend.add_edge("delta:TASK-042", "cpg:src/auth.py", type="impacts_file")
    assert out["ok"] is True and out["index"] == "delta"
    related = backend.get_related("delta:TASK-042")
    assert {n["_id"] for n in related["nodes"]} == {"delta:TASK-042", "cpg:src/auth.py"}
    assert len(backend.get_all_edges("delta")) == 1
    # idempotencia del edge + remove
    backend.add_edge("delta:TASK-042", "cpg:src/auth.py", type="impacts_file")
    assert len(backend.get_all_edges("delta")) == 1  # PK (index, src, dst)
    backend.remove_edge("delta:TASK-042", "cpg:src/auth.py")
    assert backend.get_all_edges("delta") == []


# ── IV-6: errores custom de Fase 1 (fail ruidoso, nunca vacío) ───────────────

def test_iv6_contract_error_unregistered_index(tmp_path):
    backend = _backend(tmp_path)
    with pytest.raises(StorageContractError):
        backend.store("no-existe", "1", {})


def test_iv6_schema_version_guard(tmp_path):
    backend = _backend(tmp_path)
    # simular una BD con schema v99 (más nuevo que soportado)
    backend._db.execute(  # noqa: SLF001
        "UPDATE meta SET value='v99' WHERE `index`='__db__' AND key='__schema__'"
    )
    backend._db.commit()
    with pytest.raises(StorageSchemaVersionError):
        _backend(tmp_path)


def test_iv6_integrity_error_on_corrupt_db(tmp_path):
    db_file = tmp_path / "cognitive.sqlite3"
    db_file.write_bytes(b"GARBAGE-NOT-A-SQLITE-DB-" * 64)
    conn = MemoryConnection(backend="sqlite", uri="file://" + str(tmp_path))
    with pytest.raises(StorageIntegrityError):
        SqliteBackend(conn)


# ── IV-5: compact/flush no-op (interfaz conservada por el puerto) ────────────

def test_iv5_compact_flush_noop(tmp_path):
    backend = _backend(tmp_path)
    assert backend.compact("trace") == {"ok": True, "noop": True, "_index": "trace"}
    assert backend.flush("trace") == {"ok": True, "noop": True, "_index": "trace"}


# ── CA-3: smoke de rendimiento (1000 appends, sin degradación pico) ──────────

def test_ca3_smoke_1000_appends(tmp_path):
    backend = _backend(tmp_path)
    start = time.time()
    for i in range(1000):
        backend.store("trace", f"evt-{i:05d}", {"event_type": "Trace"})
    elapsed = time.time() - start
    assert elapsed < 10.0, f"1000 appends tomaron {elapsed:.1f}s (CA-3 smoke)"
    assert backend.metrics("trace")["total_docs"] == 1000


# ═══════════════════════════════════════════════════════════════════════════
# FASE 2 prototipo — switch config-driven (ADR-063 §6.1) + smoke trace log
# ═══════════════════════════════════════════════════════════════════════════

class _FakePathResolver:
    """Resuelve cualquier chain_dir al dir base del test (para TraceLogger)."""

    def __init__(self, root):
        self._root = str(root)

    def resolve_project_root(self, chain_dir):
        return self._root


def _sqlite_cg_factory(proj_dir):
    """Factory de CognitiveGraph sobre el backend sqlite (composition root)."""
    conn = MemoryConnection(backend="sqlite", uri="sqlite://" + str(proj_dir))
    return CognitiveGraph(backend=resolve_storage_backend(conn), conn=conn)


def test_f2_resolve_storage_backend_switches_by_config(tmp_path):
    """ADR-063 corte directo: sqlite es el ÚNICO backend; 'file' falla fail-fast."""
    sqlite_conn = MemoryConnection(backend="sqlite", uri="sqlite://" + str(tmp_path))
    assert isinstance(resolve_storage_backend(sqlite_conn), SqliteBackend)

    # 'file' legacy: NO registrado → fail-fast (sin fallback, sin convivencia).
    file_conn = MemoryConnection(backend="file", uri="file://" + str(tmp_path))
    with pytest.raises(ValueError, match="desconocido"):
        resolve_storage_backend(file_conn)

    assert available_backends() == ["sqlite"]

    unknown = MemoryConnection(backend="mongo", uri="mongodb://x")
    with pytest.raises(ValueError):
        resolve_storage_backend(unknown)


def test_f2_load_memory_connection_sqlite_env(monkeypatch, tmp_path):
    monkeypatch.setenv(ENV_MEMORY_BACKEND, "sqlite")
    monkeypatch.delenv(ENV_MEMORY_URI, raising=False)

    conn = load_memory_connection(base_dir=str(tmp_path))
    assert conn.backend == "sqlite"
    assert conn.uri == "sqlite://" + str(tmp_path)

    # override file:// es honrado para sqlite (mismo resolver de ruta)
    monkeypatch.setenv(ENV_MEMORY_URI, "file://" + str(tmp_path))
    conn2 = load_memory_connection(base_dir=str(tmp_path))
    assert conn2.uri == "file://" + str(tmp_path)


def test_f2_smoke_trace_log_persists_over_sqlite(tmp_path):
    """TraceLogger (feature trace) persiste vía CognitiveGraph sobre SQLite.

    CA-2 reforzado: los eventos sobreviven a la reconstrucción completa del
    backend (nueva conexión + nuevo CognitiveGraph + re-registro de índices).
    """
    tracelog = TraceLogger(
        path_resolver=_FakePathResolver(tmp_path),
        cognitive_graph_factory=_sqlite_cg_factory,
    )
    tracelog.trace(
        "proj", "ToolInvoked", {"tool": "walk"}, task_id="T-1", session_id="S-1"
    )
    tracelog.trace(
        "proj", "WorkflowForced", {"reason": "user"}, task_id="T-2", session_id="S-1"
    )
    # F1 (ADR-063): SIN flush — cada trace() = 1 append + COMMIT inmediato;
    # la evidencia reciente ya está committed al volver de trace().

    # 1) Reconstrucción: mismo dir → misma BD sqlite → datos committed
    cg2 = _sqlite_cg_factory(str(tmp_path))
    docs = cg2.get_docs("trace")
    assert len(docs) == 2, f"trace log esperaba 2 eventos, obtuvo {len(docs)}"
    e1 = min(docs.values(), key=lambda d: d["timestamp"])
    assert e1["event_type"] == "ToolInvoked"
    assert e1["task_id"] == "T-1"
    assert e1["session_id"] == "S-1"
    assert e1["tool"] == "walk"

    # 2) La BD física contiene las filas (conteo sqlite == escrito, CA-6)
    counts = cg2._backend._db.execute(  # noqa: SLF001
        "SELECT COUNT(*) FROM docs WHERE `index`='trace'"
    ).fetchone()[0]
    assert counts == 2


# ═══════════════════════════════════════════════════════════════════════════
# FASE 3 prototipo — migración v1→v2 (CA-6/§7.7) + doble escritura temporal
# ═══════════════════════════════════════════════════════════════════════════

def _make_blob(blob_path, payload: dict) -> None:
    """Escribe un blob .json.gz con el formato histórico de Index._save."""
    import json as _json

    write_blob(str(blob_path), _json.dumps(payload, ensure_ascii=False))


def _seed_file_backend(tmp_path) -> tuple[FileBackend, str]:
    """FileBackend (v1) con un índice real: docs + edges + metadata → blob path."""
    conn = MemoryConnection(backend="file", uri="file://" + str(tmp_path))
    fb = FileBackend(conn)
    fb.register_index("trace", TRACE_SCHEMA)
    fb.store_many(
        "trace",
        {f"evt-{i:03d}": {"event_type": "kg_query", "task_id": f"task-{i}"} for i in range(100)},
    )
    fb.store("trace", "evt-A", {"event_type": "ToolInvoked", "tool": "walk"})
    fb.store("trace", "evt-B", {"event_type": "kg_query", "session_id": "S-9"})
    fb.add_edge("evt-A", "evt-000", type="implied_by")
    fb.add_edge("evt-B", "evt-A", type="follows")
    fb.set_metadata("trace", {"migrated_from": "v1", "origin": "test"})
    return fb, fb.path_for("trace")


def test_f3_import_json_gz_transactional(tmp_path):
    _, blob = _seed_file_backend(tmp_path)

    sqlite = _backend(tmp_path)  # "trace" registrado con TRACE_SCHEMA
    summary = import_json_gz_to_sqlite(sqlite, blob, index_name="trace")
    assert summary == {
        "index": "trace", "migrated": True, "docs": 102,
        "edges": 2, "metadata": 2, "source": blob,
    }

    # CA-6: conteo sqlite == conteo del blob
    conn = MemoryConnection(backend="sqlite", uri="file://" + str(tmp_path))
    db = SqliteBackend(conn)
    db.register_index("trace", TRACE_SCHEMA)  # read model re-registrado al reabrir
    assert db._db.execute(  # noqa: SLF001
        "SELECT COUNT(*) FROM docs WHERE `index`='trace'"
    ).fetchone()[0] == 102
    assert db._db.execute(  # noqa: SLF001
        "SELECT COUNT(*) FROM edges WHERE `index`='trace'"
    ).fetchone()[0] == 2

    # Read model reconstruido desde sqlite: docs, edges y metadata intactos
    docs = db.get_docs("trace")
    assert docs["evt-A"]["tool"] == "walk"
    assert docs["evt-000"]["task_id"] == "task-0"
    related = db.get_related("evt-A")
    assert {n["_id"] for n in related["nodes"]} == {"evt-A", "evt-B", "evt-000"}
    assert db.get_metadata("trace")["migrated_from"] == "v1"


def test_f3_import_corrupt_blob_rollback(tmp_path):
    bad_blob = tmp_path / "corrupt.json.gz"
    bad_blob.write_bytes(b"GARBAGE-NOT-GZIP")
    sqlite = _backend(tmp_path)

    with pytest.raises(StorageMigrationError):
        import_json_gz_to_sqlite(sqlite, str(bad_blob), index_name="trace")
    # Rollback completo: ningún doc parcial del índice
    assert sqlite._db.execute(  # noqa: SLF001
        "SELECT COUNT(*) FROM docs WHERE `index`='trace'"
    ).fetchone()[0] == 0


def test_f3_import_count_mismatch_rollback(tmp_path):
    # Blob con _id duplicados = corrupción: el conteo sqlite NUNCA puede
    # igualar al conteo del blob → StorageMigrationCountMismatchError + rollback
    _make_blob(
        tmp_path / "dup.json.gz",
        {
            "index": "trace",
            "schema_hash": "h",
            "docs": [
                {"_id": "a", "type": "x"},
                {"_id": "b", "type": "y"},
                {"_id": "a", "type": "z"},  # duplicado
            ],
        },
    )
    sqlite = _backend(tmp_path)
    with pytest.raises(StorageMigrationCountMismatchError):
        import_json_gz_to_sqlite(sqlite, str(tmp_path / "dup.json.gz"), index_name="trace")
    assert sqlite._db.execute(  # noqa: SLF001
        "SELECT COUNT(*) FROM docs WHERE `index`='trace'"
    ).fetchone()[0] == 0  # todo-o-nada (FS-6)


def test_f3_import_index_mismatch(tmp_path):
    _make_blob(
        tmp_path / "delta.json.gz",
        {"index": "delta", "schema_hash": "h", "docs": [{"_id": "T-1", "type": "task"}]},
    )
    sqlite = _backend(tmp_path)
    with pytest.raises(StorageMigrationError, match="delta"):
        import_json_gz_to_sqlite(sqlite, str(tmp_path / "delta.json.gz"), index_name="trace")


def test_f3_single_write_path_no_legacy_blobs(tmp_path):
    """Corte directo (sin convivencia): escribir NUNCA crea/actualiza blobs v1.

    ADR-WALKER-063: el runtime es UN solo camino (sqlite v2 SSOT). Tras
    writes vía el backend, NO debe existir ningún <index>.json.gz en el dir
    (la migración de blobs existentes es solo con migrate_cli.py y los
    blobs legacy no modificados quedan de backup hasta retiro manual).
    """
    primary = _backend(tmp_path)

    primary.register_index("trace", TRACE_SCHEMA)
    primary.store("trace", "e1", {"event_type": "ToolInvoked", "tool": "walk"})
    primary.store_many(
        "trace", {"e2": {"event_type": "kg_query"}, "e3": {"event_type": "kg_query"}}
    )
    primary.set_metadata("trace", {"single": "path"})
    primary.append("trace", {"e4": {"event_type": "WorkflowForced"}})

    assert len(primary.get_docs("trace")) == 4
    # Sin doble escritura: ningún blob legacy se creó ni modificó.
    legacy_blobs = list(tmp_path.glob("*.json.gz"))
    assert legacy_blobs == [], (
        f"doble escritura detectada (sin convivencia): {legacy_blobs}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# ROLLOUT F1 (ADR-063 §6.1) — limpieza del TraceLogger + puerto elevado +
# CA-8 multi-conexión + cutover §7.7 por data_dir
# ═══════════════════════════════════════════════════════════════════════════

def test_f1_trace_sin_batching_commit_inmediato(tmp_path):
    """Limpieza F1: TraceLogger sin buffer — cada trace() commitea al momento.

    El checklist exige: sin _pending, sin _flush_pending, sin flush() previo
    a gates (replay siempre ve la evidencia reciente) y sin atexit. El COMMIT
    se verifica de forma independiente: otra conexión al MISMO sqlite (WAL)
    ve el evento sin ningún paso adicional del logger.
    """
    tracelog = TraceLogger(
        path_resolver=_FakePathResolver(tmp_path),
        cognitive_graph_factory=_sqlite_cg_factory,
    )
    # La limpieza eliminó el buffer (IV-2/§6.1 F1)
    assert not hasattr(tracelog, "_pending"), "batching reintroducido"
    assert not hasattr(tracelog, "_flush_pending"), "flush previo reintroducido"
    assert not hasattr(tracelog, "flush"), "flush() público no debe existir"

    tracelog.trace("proj", "ToolInvoked", {"tool": "walk"}, task_id="T-1")

    # Lector independiente (misma BD, nueva conexión): el evento YA está
    # committed — sin flush, sin cierre, sin atexit.
    conn = MemoryConnection(backend="sqlite", uri="file://" + str(tmp_path))
    reader = SqliteBackend(conn)
    reader.register_index("trace", TRACE_SCHEMA)
    n = reader._db.execute(  # noqa: SLF001
        "SELECT COUNT(*) FROM docs WHERE `index`='trace'"
    ).fetchone()[0]
    assert n == 1, f"esperado 1 evento committed tras trace(), obtuvo {n}"
    docs = reader.get_docs("trace")
    (ev,) = docs.values()
    assert ev["event_type"] == "ToolInvoked" and ev["task_id"] == "T-1"

    # 3 eventos más sin lote: todos visibles inmediatamente desde otra conexión
    for i in range(3):
        tracelog.trace("proj", "kg_query", {"index": "ckg"}, task_id=f"T-{i + 2}")
    n = reader._db.execute(  # noqa: SLF001
        "SELECT COUNT(*) FROM docs WHERE `index`='trace'"
    ).fetchone()[0]
    assert n == 4


def test_f1_facade_append_contract_error(tmp_path):
    """Facade.append: backend legacy sin append → StorageContractError (IV-6).

    El puerto elevado StorageDb (27) no se degrada silenciosamente: un
    backend que no implementa append falla ruidoso, nunca con retorno vacío.
    """
    class LegacyBackend:
        def store(self, index, doc_id, data):
            return {"ok": True}

    cg = CognitiveGraph(chain_dir=str(tmp_path))
    cg._backend = LegacyBackend()  # noqa: SLF001
    with pytest.raises(StorageContractError, match="append"):
        cg.append("trace", {"e1": {"event_type": "x"}})


def test_ca8_multi_conexion_wal_reader_ve_commits(tmp_path):
    """CA-8: concurrencia multi-conexión sobre la MISMA BD sqlite (WAL).

    El proceso escritor commitea cada operación; un lector (otra conexión/
    otro backend, simulando otro proceso/shared_cache) ve los commits sin
    locking ni reconstrucción. Es el prerequisito del refresh multi-proceso
    del ADR: datos escritos por un proceso son visibles para el otro.
    """
    writer = _backend(tmp_path)
    reader = SqliteBackend(
        MemoryConnection(backend="sqlite", uri="file://" + str(tmp_path))
    )
    reader.register_index("trace", TRACE_SCHEMA)

    assert reader.get_docs("trace") == {}
    writer.store("trace", "e1", {"event_type": "ToolInvoked", "tool": "walk"})
    writer.store_many(
        "trace",
        {"e2": {"event_type": "kg_query", "index": "cpg"}, "e3": {"event_type": "kg_query"}},
    )
    # Lector fresco ve los 3 commits (WAL shared-cache; sin flush explícito)
    assert set(reader.get_docs("trace")) == {"e1", "e2", "e3"}
    hits = reader.search("trace", {"event_type": "kg_query"})
    assert hits["hits"]["total"] == 2

    # El lector también puede escribir y el writer lo ve (doble sentido)
    reader.store("trace", "e4", {"event_type": "WorkflowForced"})
    assert set(writer.get_docs("trace")) == {"e1", "e2", "e3", "e4"}


def test_f3_cutover_migrate_walker_memories(tmp_path):
    """Cutover §7.7: importa los blobs canónicos cpg/ckg/delta de data_dir.

    trace.json.gz ausente → imported=False (índice aún sin eventos, no es
    fallo). El peer legacy cog/data/<idx>.json.gz se contempla. Los conteos
    del resumen (CA-6) se verifican contra la BD.
    """
    from shared.libs.cognitive_graph.migrations import migrate_walker_memories

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    for name in ("cpg", "ckg", "delta"):
        d = data_dir / f"{name}.json.gz"
        _make_blob(
            d,
            {
                "index": name,
                "schema_hash": f"h-{name}",
                "docs": [
                    {"_id": f"{name}-1", "type": "node"},
                    {"_id": f"{name}-2", "type": "node"},
                ],
                "edges": [{"from": f"{name}-1", "to": f"{name}-2", "type": "links"}],
                "metadata": {"source": "cutover-test"},
            },
        )

    sqlite = _backend(tmp_path)
    results = migrate_walker_memories(str(data_dir), sqlite)

    by_index = {r["index"]: r for r in results}
    assert len(by_index) == 4  # cpg, ckg, delta, trace
    for name in ("cpg", "ckg", "delta"):
        r = by_index[name]
        assert r["imported"] is True and r["docs"] == 2 and r["edges"] == 1
        assert r["metadata"] == 1 and r["migrated"] is True
    assert by_index["trace"]["imported"] is False  # sin blob, sin error

    # CA-6 contra la BD física
    db: sqlite3.Connection = sqlite._db  # noqa: SLF001
    for name in ("cpg", "ckg", "delta"):
        n = db.execute(
            "SELECT COUNT(*) FROM docs WHERE `index`=?", (name,)
        ).fetchone()[0]
        assert n == 2, f"{name}: sqlite tiene {n} docs, esperaba 2"
    # La BD está en modo WAL (requisito CA-8)
    (journal,) = db.execute("PRAGMA journal_mode").fetchone()
    assert journal.lower() == "wal"