"""cognitive_graph/sqlite_backend.py — SqliteBackend (Fase 1 de ADR-WALKER-063).

Prototipo de Fase 1: el MISMO puerto `StorageBackend` (24 métodos, ADR-061)
+ `append`/`compact`/`flush` (27 total, ADR-063 §7.1), implementado sobre
`sqlite3` stdlib (WAL, `synchronous=NORMAL`). Reemplaza al `FileBackend`
sin tocar a los consumidores (facade, repos, gates).

Diseño (fiel al ADR §3.4/§7.2-§7.4):
  - Read model en memoria = el `Index` real (search estructurado/fulltext,
    grafo nx.DiGraph, índices secundarios O(1)) — la búsqueda queda INTACTA.
  - Persistencia: `SqliteIndex._save()` persiste el DELTA transaccional
    (diff contra el estado previamente guardado) → `append` de 1 doc =
    1 INSERT, sin write amplification (IV-1/CA-1).
  - `events` = append-only con `UNIQUE(id)` (idempotencia de reintentos).
  - `meta` guarda `__schema__` (schema_version, v2) → guarda de lector
    ruidosa (`StorageSchemaVersionError`, §6.4/IV-6).
  - `compact`/`flush` = no-op (IV-5; la interfaz se conserva por el puerto).

Errores (IV-6/IV-7): los fallos de contrato lanzan `StorageContractError`;
el resto de fallos de BD se traducen a los `StorageError` de la fase y se
propagan (el Index los devuelve como `{"ok": False, "error": ...}` — nunca
`except: pass`). Un `search` sin hits devuelve shape explícito
(`{"hits": {"total": 0, "hits": []}}`), nunca `{}`.

Limitación conocida del prototipo: `metrics.storage_size/last_updated` se
derivan de la BD (no del blob); el search handler custom del trace (H3, que
HOY recarga el blob por query) debe migrarse al read model en el rollout —
el puerto aquí ya recibe el handler registrado como en FileBackend.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from typing import Any

from .connection import MemoryConnection
from .index import Index, IndexSchema
from .models import TokenIndex
from .ports import StorageBackend
from .storage_errors import (
    StorageContractError,
    StorageIntegrityError,
    StorageReadError,
    StorageSchemaVersionError,
    StorageWriteError,
)

_SCHEMA_VERSION = "v2"
_DEFAULT_SCHEMA_VERSION = "v1"  # blobs .json.gz (legacy, §6.4)


def _sha1(value: Any) -> str:
    """Hash determinista del JSON canónico (diff de persistencia)."""
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


class SqliteIndex(Index):
    """Index con persistencia SQLite (read model en memoria + delta writes).

    Reutiliza toda la lógica del Index (search, grafo, índices secundarios,
    token index). Solo se sustituye el mecanismo de persistencia:
      - `_do_load()`: SELECT desde sqlite → poblar read model.
      - `_save()`: diff contra `_saved_docs/_saved_edges` → INSERT/UPDATE/
        DELETE transaccional (O(log N) por write, sin rewrite del índice).
    """

    def __init__(
        self,
        name: str,
        schema: IndexSchema,
        db: sqlite3.Connection,
        search_handler: callable | None = None,
        db_path: str = "",
    ):
        super().__init__(name, schema, chain_dir="", search_handler=search_handler)
        self._db = db
        self._db_path = db_path
        self._saved_docs: dict[str, str] = {}  # doc_id -> sha1(json) ya en BD
        self._saved_edges: dict[tuple[str, str], str] = {}  # (u,v) -> sha1(attrs)
        self._metadata_hash: str = ""

    # ── Locks: SQLite+WAL serializa; sin fcntl (evita ficheros .lock) ──

    def _acquire_write_lock(self) -> None:
        return None  # WAL: 1 escritor, serializado por sqlite (IV-2)

    def _release_write_lock(self) -> None:
        return None

    # ── Persistencia: load desde BD ──

    def _do_load(self) -> None:  # type: ignore[override]
        """Carga el read model desde sqlite (reemplaza la lectura del blob)."""
        try:
            docs: dict[str, dict] = {}
            for raw_id, raw_data in self._db.execute(
                "SELECT id, data FROM docs WHERE `index`=?", (self.name,)
            ):
                docs[raw_id] = json.loads(raw_data)
                self._saved_docs[raw_id] = _sha1(docs[raw_id])
            self._docs = docs
            for doc_id, doc_data in docs.items():
                self._graph.add_node(doc_id, **doc_data)

            self._saved_edges = {}
            for src, dst, attrs_raw in self._db.execute(
                "SELECT src, dst, attributes FROM edges WHERE `index`=?", (self.name,)
            ):
                attrs = json.loads(attrs_raw) if attrs_raw else {}
                self._graph.add_edge(src, dst, **attrs)
                self._saved_edges[(src, dst)] = _sha1(attrs)

            self._metadata = {}
            self._metadata_hash = ""
            stored_hash = ""
            for key, value in self._db.execute(
                "SELECT key, value FROM meta WHERE `index`=? AND value IS NOT NULL",
                (self.name,),
            ):
                if key == "schema_hash":
                    stored_hash = value
                    continue
                if key == "__schema__":
                    continue
                try:
                    self._metadata[key] = json.loads(value)
                except json.JSONDecodeError:
                    self._metadata[key] = value
            if self._metadata:
                self._metadata_hash = _sha1(self._metadata)

            # schema_hash: migración de estructura igual que el blob (G3)
            if stored_hash and stored_hash != self._schema_hash:  # pragma: no cover
                self._rebuild_secondary_indexes()
                self._rebuild_token_index_inline()
        except (sqlite3.Error, json.JSONDecodeError) as e:
            raise StorageReadError(
                f"SqliteIndex['{self.name}']: no se pudo cargar desde la BD "
                f"({type(e).__name__}: {e})"
            ) from e

    def _rebuild_token_index_inline(self) -> None:
        if self._token_index is None:
            return
        self._token_index = TokenIndex()
        for doc_id, doc in self._docs.items():
            for field in self.schema.fulltext_fields:
                value = self._resolve_field(doc, field)
                if isinstance(value, str) and value:
                    self._token_index.index_node(doc_id, value, field)
                elif isinstance(value, list):
                    for item in value:
                        if isinstance(item, str):
                            self._token_index.index_node(doc_id, item, field)

    # ── Persistencia: save con delta transaccional (IV-1/CA-1) ──

    def _save(self) -> None:  # type: ignore[override]
        """Persiste el DELTA del read model a sqlite, transaccional.

        Diff contra `_saved_docs`/`_saved_edges`: un `append` de 1 doc
        produce 1 INSERT (nunca reescribe filas ajenas — CA-1).
        """
        cur = self._db
        try:
            with cur:  # commit/rollback automático (sqlite3)
                # ── docs ──
                current: dict[str, str] = {}
                for doc_id, data in self._docs.items():
                    h = _sha1(data)
                    current[doc_id] = h
                    if doc_id in self._saved_docs:
                        if self._saved_docs[doc_id] != h:
                            cur.execute(
                                "UPDATE docs SET data=?, updated_at=? "
                                "WHERE `index`=? AND id=?",
                                (
                                    json.dumps(data, ensure_ascii=False, default=str),
                                    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                    self.name,
                                    doc_id,
                                ),
                            )
                    else:
                        cur.execute(
                            "INSERT OR REPLACE INTO docs (`index`, id, data, updated_at) "
                            "VALUES (?,?,?,?)",
                            (
                                self.name,
                                doc_id,
                                json.dumps(data, ensure_ascii=False, default=str),
                                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                            ),
                        )
                for doc_id in set(self._saved_docs) - set(current):
                    cur.execute(
                        "DELETE FROM docs WHERE `index`=? AND id=?", (self.name, doc_id)
                    )
                self._saved_docs = current

                # ── edges (diff por par, O(E) al vuelo) ──
                graph_edges: dict[tuple[str, str], str] = {}
                for u, v, data in self._graph.edges(data=True):
                    graph_edges[(u, v)] = _sha1(data)
                for key in set(self._saved_edges) - set(graph_edges):
                    cur.execute(
                        "DELETE FROM edges WHERE `index`=? AND src=? AND dst=?",
                        (self.name, key[0], key[1]),
                    )
                for (u, v), h in graph_edges.items():
                    if (u, v) in self._saved_edges:
                        if self._saved_edges[(u, v)] != h:
                            attrs = dict(self._graph.edges[(u, v)])
                            cur.execute(
                                "UPDATE edges SET attributes=? "
                                "WHERE `index`=? AND src=? AND dst=?",
                                (
                                    json.dumps(attrs, ensure_ascii=False, default=str),
                                    self.name,
                                    u,
                                    v,
                                ),
                            )
                    else:
                        attrs = dict(self._graph.edges[(u, v)])
                        cur.execute(
                            "INSERT OR REPLACE INTO edges (`index`, src, dst, attributes) "
                            "VALUES (?,?,?,?)",
                            (
                                self.name,
                                u,
                                v,
                                json.dumps(attrs, ensure_ascii=False, default=str),
                            ),
                        )
                self._saved_edges = graph_edges

                # ── metadata (passthrough, ADR-054 D5) ──
                mh = _sha1(self._metadata)
                if mh != self._metadata_hash:
                    cur.execute(
                        "DELETE FROM meta WHERE `index`=? AND key NOT IN "
                        "('__schema__', 'schema_hash')",
                        (self.name,),
                    )
                    for key, value in self._metadata.items():
                        cur.execute(
                            "INSERT OR REPLACE INTO meta (`index`, key, value) "
                            "VALUES (?,?,?)",
                            (self.name, key, json.dumps(value, ensure_ascii=False)),
                        )
                    self._metadata_hash = mh

                # ── schema_hash (migración de estructura, G3) ──
                cur.execute(
                    "INSERT OR REPLACE INTO meta (`index`, key, value) VALUES (?,?,?)",
                    (self.name, "schema_hash", self._schema_hash),
                )
        except sqlite3.Error as e:
            raise StorageWriteError(
                f"SqliteIndex['{self.name}']: persistencia falló "
                f"({type(e).__name__}: {e}) — sin cambio parcial (rollback)."
            ) from e

    # ── metrics sin path de blob (IV-10: layout sqlite) ──

    def metrics(self) -> dict:  # type: ignore[override]
        self._load()
        size = 0
        try:
            for (n,) in self._db.execute(
                "SELECT page_count FROM pragma_page_count()"
            ):  # noqa: SIM117
                size = int(n) * 4096
        except sqlite3.Error:
            size = 0
        last_updated = "never"
        row = self._db.execute(
            "SELECT MAX(updated_at) FROM docs WHERE `index`=?", (self.name,)
        ).fetchone()
        if row and row[0]:
            last_updated = row[0]
        return {
            "total_docs": len(self._docs),
            "total_edges": self._graph.number_of_edges(),
            "storage_size": f"{size / 1024:.1f}KB",
            "storage_path": self._db_path,
            "last_updated": last_updated,
        }


class SqliteBackend:
    """StorageBackend (24 métodos, ADR-061) + append/compact/flush sobre SQLite.

    Fase 1 de ADR-WALKER-063. 27/27 métodos del contrato (§7.1).
    """

    def __init__(self, conn: MemoryConnection):
        self._conn = conn
        self._db_path = self._resolve_db_path(conn)
        self._db = sqlite3.connect(self._db_path, timeout=10)
        self._db.row_factory = sqlite3.Row
        self._integrity_guard()  # antes de cualquier PRAGMA/lectura (FS-5)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.execute("PRAGMA busy_timeout=10000")
        self._create_schema()
        self._ensure_schema_version()
        self._schemas: dict[str, IndexSchema] = {}
        self._indexes: dict[str, SqliteIndex] = {}

    @property
    def _base_dir(self) -> str:
        return os.path.dirname(self._db_path)

    # ── Setup ──

    @staticmethod
    def _resolve_db_path(conn: MemoryConnection) -> str:
        uri = conn.uri
        if uri.startswith("sqlite://"):
            path = uri[len("sqlite://"):]
            if path.endswith(".sqlite3"):
                return os.path.abspath(path)
            return os.path.abspath(os.path.join(path, "cognitive.sqlite3"))
        base = os.path.abspath(uri[len("file://"):] if uri.startswith("file://") else uri)
        if base.endswith(".sqlite3"):
            return base
        return os.path.join(base, "cognitive.sqlite3")

    def _create_schema(self) -> None:
        with self._db:
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS docs (
                  `index` TEXT NOT NULL,
                  id    TEXT NOT NULL,
                  data  TEXT NOT NULL,
                  updated_at TEXT,
                  PRIMARY KEY (`index`, id)
                );
                CREATE TABLE IF NOT EXISTS events (
                  `index` TEXT NOT NULL,
                  seq   INTEGER PRIMARY KEY AUTOINCREMENT,
                  id    TEXT NOT NULL UNIQUE,
                  data  TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS edges (
                  `index` TEXT NOT NULL,
                  src   TEXT NOT NULL,
                  dst   TEXT NOT NULL,
                  attributes TEXT,
                  PRIMARY KEY (`index`, src, dst)
                );
                CREATE TABLE IF NOT EXISTS meta (
                  `index` TEXT NOT NULL,
                  key   TEXT NOT NULL,
                  value TEXT,
                  PRIMARY KEY (`index`, key)
                );
                """
            )

    def _ensure_schema_version(self) -> None:
        row = self._db.execute(
            "SELECT value FROM meta WHERE `index`='__db__' AND key='__schema__'"
        ).fetchone()
        existing = row["value"] if row else None
        if existing is None:
            with self._db:
                self._db.execute(
                    "INSERT OR REPLACE INTO meta (`index`, key, value) VALUES ('__db__','__schema__',?)",
                    (_SCHEMA_VERSION,),
                )
            return
        if existing != _SCHEMA_VERSION:
            raise StorageSchemaVersionError(
                f"schema_version '{existing}' no soportado (esperado '{_SCHEMA_VERSION}'). "
                f"Guarda de lector §6.4 — fail ruidoso, nunca datos stale."
            )

    def _integrity_guard(self) -> None:
        try:
            row = self._db.execute("PRAGMA integrity_check").fetchone()
        except sqlite3.DatabaseError as e:
            raise StorageIntegrityError(
                f"cognitive.sqlite3 corrupto o ilegible ({type(e).__name__}: {e}) — FS-5, "
                f"restaurar desde backup."
            ) from e
        if not row or row[0] != "ok":
            raise StorageIntegrityError(
                f"PRAGMA integrity_check: {row} — BD corrupta (FS-5). "
                f"Nunca se leen datos posiblemente corruptos (IV-6)."
            )

    # ── Registro ──

    def register_index(self, name: str, schema: Any, search_handler: Any = None) -> None:
        if isinstance(schema, dict):
            schema = IndexSchema(schema)
        self._schemas[name] = schema
        self._indexes[name] = SqliteIndex(
            name, schema, self._db, search_handler, db_path=self._db_path
        )

    def _idx(self, name: str) -> SqliteIndex:
        idx = self._indexes.get(name)
        if idx is None:
            raise StorageContractError(
                f"Índice '{name}' no registrado. Disponibles: {list(self._indexes.keys())} "
                f"(IV-6: fallo ruidoso, no retorno vacío)."
            )
        return idx

    def _get_index(self, name: str) -> SqliteIndex:
        """Acceso del CognitiveGraph a los handlers de trace/ref internos.

        Mismo contrato que FileBackend._get_index: el registro en la facade
        (refs/trace) usa el Index interno del backend. Con sqlite el Index
        envuelve la conexión (SqliteIndex), no un archivo.
        """
        return self._idx(name)

    # ── CRUD doc-level ──

    def store(self, index: str, doc_id: str, data: dict) -> dict:
        return self._idx(index).store(doc_id, data)

    def store_many(self, index: str, docs: dict[str, dict]) -> dict:
        return self._idx(index).store_many(docs)

    def append(self, index: str, docs: dict[str, dict]) -> dict:
        """Write-side append-only (trace): INSERT en `events` (UNIQUE id) + read model."""
        idx = self._idx(index)
        with self._db:
            for doc_id, data in docs.items():
                self._db.execute(
                    "INSERT OR IGNORE INTO events (`index`, id, data) VALUES (?,?,?)",
                    (index, doc_id, json.dumps(data, ensure_ascii=False, default=str)),
                )
        return idx.store_many(docs)  # read model + tabla docs (delta, IV-1)

    def get(self, index: str, doc_id: str) -> dict | None:
        return self._idx(index).get(doc_id)

    def delete(self, index: str, doc_id: str) -> dict:
        return self._idx(index).delete(doc_id)

    def delete_many(self, index: str, doc_ids: set[str]) -> dict:
        return self._idx(index).delete_many(doc_ids)

    def max_key(self, index: str, prefix: str = "") -> tuple[str, int] | None:
        return self._idx(index).max_key(prefix)

    def search(self, index: str, query: dict) -> dict:
        return self._idx(index).search(query)

    # ── Edges / grafo (cross-index, H6) ──

    def find_node_index(self, node_id: str) -> str | None:
        for name, idx in self._indexes.items():
            if node_id in idx.keys():  # keys() asegura _load()
                return name
        return None

    def add_edge(self, from_id: str, to_id: str, **attrs: Any) -> dict:
        idx_name = self.find_node_index(from_id) or "delta"
        idx = self._indexes.get(idx_name)
        if idx is None:
            raise StorageContractError(
                f"Index '{idx_name}' no registrado (add_edge de '{from_id}') — IV-6."
            )
        result = idx.add_edge(from_id, to_id, **attrs)
        return {
            "ok": True, "from": from_id, "to": to_id,
            "attrs": attrs, "index": idx_name, "result": result,
        }

    def remove_edge(self, from_id: str, to_id: str) -> dict:
        idx_name = self.find_node_index(from_id) or "delta"
        idx = self._indexes.get(idx_name)
        if idx is None:
            raise StorageContractError(
                f"Index '{idx_name}' no registrado (remove_edge) — IV-6."
            )
        return idx.remove_edge(from_id, to_id)

    def get_edges(self, node_id: str, direction: str = "both") -> list:
        name = self.find_node_index(node_id)
        if name:
            return self._indexes[name].get_edges(node_id, direction)
        return []

    def get_related(self, node_id: str, depth: int = 1) -> dict:
        name = self.find_node_index(node_id)
        if name:
            return self._indexes[name].get_related(node_id, depth)
        for idx in self._indexes.values():
            if node_id in idx.get_docs():
                return idx.get_related(node_id, depth)
        return {"center": node_id, "nodes": [], "edges": [], "total": 0}

    def get_all_edges(self, index: str) -> list[dict]:
        return self._idx(index).get_all_edges()

    # ── Blob / metadata ──

    def replace_blob(self, index: str, nodes: dict, edges: list, metadata: dict) -> dict:
        return self._idx(index).replace_blob(nodes, edges, metadata)

    def sync_edges(self, index: str, edges: list[dict]) -> dict:
        return self._idx(index).sync_edges(edges)

    def set_metadata(self, index: str, metadata: dict) -> dict:
        return self._idx(index).set_metadata(metadata)

    def get_metadata(self, index: str) -> dict:
        return self._idx(index).get_metadata()

    def get_docs(self, index: str) -> dict[str, dict]:
        return self._idx(index).get_docs()

    def keys(self, index: str) -> list[str]:
        return self._idx(index).keys()

    def upsert(self, index: str, key: str, data: dict) -> dict:
        existing = self.get(index, key)
        if existing:
            merged = dict(existing.get("_source") or {})
            merged.update(data)
            result = self.store(index, key, merged)
            return {"created": False, "updated": True, "result": result}
        result = self.store(index, key, data)
        return {"created": True, "updated": False, "result": result}

    def schema_info(self, index: str) -> dict:
        return self._idx(index).schema_info()

    def metrics(self, index: str) -> dict:
        return self._idx(index).metrics()

    def list_indexes(self) -> list[str]:
        return list(self._indexes.keys())

    # ── Mantenimiento (IV-5: no-op en SQLite) ──

    def compact(self, index: str) -> dict:
        """No-op en SqliteBackend (retención + VACUUM opcional; §3.4/§6.3d)."""
        return {"ok": True, "noop": True, "_index": index}

    def flush(self, index: str) -> dict:
        """No-op: cada operación commitea sola (transacción implícita sqlite3)."""
        return {"ok": True, "noop": True, "_index": index}

    # ── Inspección (tests) ──

    def path_for(self, name: str) -> str:
        return self._db_path

    @property
    def schema_version(self) -> str:
        return _SCHEMA_VERSION


__all__ = ["SqliteBackend", "SqliteIndex", "StorageBackend"]