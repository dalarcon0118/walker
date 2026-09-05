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

from . import fts as fts_mod
from .connection import MemoryConnection
from .index import Index, IndexSchema
from .models import TokenIndex
from .ports import StorageBackend
from .storage_errors import (
    StorageContractError,
    StorageIntegrityError,
    StorageReadError,
    StorageSchemaVersionError,
    StorageStateError,
    StorageWriteError,
)

_SCHEMA_VERSION = "v2"
_DEFAULT_SCHEMA_VERSION = "v1"  # blobs .json.gz (legacy, §6.4)

# System-keys de meta EXCLUIDAS del passthrough de metadata de dominio
# (el DELETE de metadata de _save las preserva; los reads de metadata las
# filtran). El marker FTS (fts_config) es system-key: borrarlo = "migración
# a medias" fail-fast al boot (FS-2).
_META_SYSTEM_KEYS = ("__schema__", "schema_hash", fts_mod.MARKER_KEY)


def _sha1(value: Any) -> str:
    """Hash determinista del JSON canónico (diff de persistencia)."""
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _sha1_raw(raw: str) -> str:
    """sha1 del payload EXACTO almacenado en BD (sin re-serializar).

    El diff load/save compara el hash de los bytes guardados. Antes el
    load recomputaba `_sha1(doc)` (un json.dumps(sort_keys) POR FILA:
    ~40K dumps de docs + ~24K de edges en un CPG mediano → minutos de
    CPU dentro del event loop del server MCP). Ahora el payload se
    escribe canónico (sort_keys) y el hash se calcula sobre el raw:
    costo O(bytes), cero serialización.
    """
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
        # F1 (ADR-WALKER-075 D2): columnas FTS activas para este índice
        # (schema.search.fields) o None si el índice no tiene FTS — lo setea
        # SqliteBackend tras ensure_fts (boot/register). Con FTS activo el
        # fulltext va por MATCH y el TokenIndex NO se reconstruye en load.
        self._fts_fields: list[str] | None = None

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
                # FIX perf: hash del payload EXACTO (sin re-serializar el doc).
                # Antes: _sha1(docs[raw_id]) = json.dumps(sort_keys) por fila
                # (~40K dumps en un CPG mediano). Nota: filas escritas por el
                # save anterior (payload sin sort_keys) se normalizan en el
                # primer save posterior (rewrite one-time, self-healing).
                self._saved_docs[raw_id] = _sha1_raw(raw_data)
            self._docs = docs
            for doc_id, doc_data in docs.items():
                self._graph.add_node(doc_id, **doc_data)

            self._saved_edges = {}
            for src, dst, attrs_raw in self._db.execute(
                "SELECT src, dst, attributes FROM edges WHERE `index`=?", (self.name,)
            ):
                attrs = json.loads(attrs_raw) if attrs_raw else {}
                self._graph.add_edge(src, dst, **attrs)
                # FIX perf: hash del raw de atributos; NULL/"" ≡ "{}" (el
                # save canónico escribe dumps({}) = "{}").
                self._saved_edges[(src, dst)] = _sha1_raw(attrs_raw or "{}")

            self._metadata = {}
            self._metadata_hash = ""
            stored_hash = ""
            for key, value in self._db.execute(
                "SELECT key, value FROM meta WHERE `index`=? AND value IS NOT NULL",
                (self.name,),
            ):
                if key in _META_SYSTEM_KEYS:
                    if key == "schema_hash":
                        stored_hash = value
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

            # BUG (preexistente, grave): SqliteIndex._do_load nunca seteaba
            # _loaded → el base _load() re-ejecutaba ESTE método en CADA
            # acceso (search/get/save): re-SELECT de TODAS las filas + JSON
            # parse por operación (16K docs + 24K edges en el CPG real →
            # segundos por cada llamada, dentro del event loop).
            # Además: FIX FTS sordo (sqlite) — el read model viene de FILAS,
            # no hay blob que traiga token_index/_type_index persistidos (no
            # existe tabla de tokens). Sin rebuild, el fulltext quedaba MUDO
            # tras cada restart: 0 hits con el CPG poblado aunque el schema
            # configurara search fulltext (el índice solo se llenaba en
            # memoria durante writes del MISMO proceso).
            self._rebuild_secondary_indexes()
            # M-03 (anti-patrón: reconstruir el token index en cada load):
            # con FTS activo el TokenIndex NO es camino de producción (D2) —
            # el fulltext va por MATCH y el rebuild se omite (el load baja
            # el costo del token reindex completo).
            if self._fts_fields is None:
                self._rebuild_token_index_inline()
            self._loaded = True
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

    # ── F1: FTS5 MATCH como camino de producción del fulltext ────────────

    def _fulltext_ids(
        self,
        text_query: str,
        max_results: int,
        text_fields: list[str] | None,
    ) -> set[str]:
        """M-01: la parte fulltext resuelve vía FTS5 MATCH (C-speed),
        sin tokenizar/filtrar en Python. Fallback TokenIndex SOLO si el
        índice no tiene FTS activo. Si piden field-scoping sobre columnas
        fuera del FTS → StorageContractError (fail-fast: el TokenIndex no
        se reconstruye cuando FTS es el camino — responder vacío sería un
        hit sordo, IV-6/IV-7)."""
        if self._fts_fields is None:
            return super()._fulltext_ids(text_query, max_results, text_fields)
        if text_fields and not set(text_fields) <= set(self._fts_fields):
            raise StorageContractError(
                f"field-scoped search sobre columna(s) sin FTS en "
                f"'{self.name}': {sorted(set(text_fields) - set(self._fts_fields))} "
                f"(columnas FTS: {self._fts_fields}) — añada el campo a "
                f"IndexSchema.search.fields y re-backfill (drop_fts + reabrir), "
                f"o use un campo indexado (fail-fast: sin FTS activo el "
                f"TokenIndex no está poblado; responder vacío sería sordera "
                f"silenciosa)."
            )
        rows, _total = fts_mod.search_fts(
            self._db, self.name, text_query,
            max_results=max_results, fields_scoped=text_fields,
        )
        return {r[0] for r in rows}

    def search(self, query: Any) -> dict:  # type: ignore[override]
        """F1 (D2/Enmienda 1): query PURA-TEXTUAL resuelve 100% vía FTS5
        MATCH con orden bm25 y total real (sin cap de token index) — el
        contrato declara mismo SET de docs, orden por bm25 (mejora
        documentada, no orden idéntico). Toda otra combinación (filtros
        estructurados/combinados) → Index.search base, cuyo texto pasa por
        `_fulltext_ids` (FTS) y cuyos filtros usan los índices secundarios
        hasta F2 (columnas generadas)."""
        if self._search_handler is not None or self._fts_fields is None:
            return super().search(query)
        if not isinstance(query, dict) or query.get("text") is None:
            return super().search(query)

        filters = dict(query)
        raw_text = filters.pop("text")
        for _mc in ("max_results", "size", "max_events", "_index", "query",
                    "_from", "from", "text_fields"):
            filters.pop(_mc, None)
        if filters:  # hay filtros estructurados → path combinado (base)
            return super().search(query)

        max_results = (
            query.get("max_results") or query.get("size")
            or query.get("max_events")
        )
        try:
            max_results = int(max_results) if max_results else 50
        except (TypeError, ValueError):
            max_results = 50
        try:
            _from = max(int(query.get("_from") or query.get("from") or 0), 0)
        except (TypeError, ValueError):
            _from = 0

        if isinstance(raw_text, dict):
            text = raw_text.get("query", "")
            fields_scoped = raw_text.get("fields")
        else:
            text, fields_scoped = str(raw_text), None
        if fields_scoped is None:
            tf = query.get("text_fields")
            fields_scoped = tf if isinstance(tf, list) else ([tf] if tf else None)
        if fields_scoped and not set(fields_scoped) <= set(self._fts_fields):
            raise StorageContractError(
                f"field-scoped search sobre columna(s) sin FTS en "
                f"'{self.name}': {sorted(set(fields_scoped) - set(self._fts_fields))} "
                f"(columnas FTS: {self._fts_fields}) — añada el campo a "
                f"IndexSchema.search.fields y re-backfill (drop_fts + reabrir)."
            )

        took = time.time()
        self._load()  # materializa el read model (los hits leen _source)
        ranked, total = fts_mod.search_fts(
            self._db, self.name, text,
            max_results=max_results, fields_scoped=fields_scoped,
            offset=_from,
        )
        results = []
        for doc_id, rank in ranked:
            doc = self._docs.get(doc_id)
            if doc is None:  # FTS-drift no debería pasar (M-04 verifica en boot)
                continue
            entry = {
                "_id": doc_id,
                "_index": self.name,
                "_score": round(-rank, 6),  # bm25 es costo; _score = relevancia
                "_source": doc,
            }
            related = self._get_neighbor_ids(doc_id, depth=1)
            if related:
                entry["_related"] = related
            results.append(entry)
        took = int((time.time() - took) * 1000)
        return {
            "took": took,
            "index": self.name,
            "hits": {
                "total": total,
                "hits": results,
                "from": _from,
                "size": max_results,
            },
            "schema": self._schema_info(),
        }

    def get_metadata_only(self) -> dict:
        """Lee la tabla meta DIRECTO de sqlite (sin poblar el read model).

        El _do_load() completo hace json.loads + sha1(json.dumps) POR FILA
        (docs + edges: ~80K serializaciones en un CPG mediano) solo para
        devolver la metadata. Para chequeos de staleness (fingerprint del
        CPG, invocado por watcher.scan() en cada scan) basta un SELECT de
        la tabla meta: O(ms), sin tocar docs/edges/_saved_*/grafos.
        """
        try:
            metadata: dict = {}
            for key, value in self._db.execute(
                "SELECT key, value FROM meta WHERE `index`=? AND value IS NOT NULL",
                (self.name,),
            ):
                if key in _META_SYSTEM_KEYS:
                    continue
                try:
                    metadata[key] = json.loads(value)
                except json.JSONDecodeError:
                    metadata[key] = value
            return metadata
        except sqlite3.Error as e:
            raise StorageReadError(
                f"SqliteIndex['{self.name}']: no se pudo leer la metadata "
                f"({type(e).__name__}: {e})"
            ) from e

    # ── Persistencia: save con delta transaccional (IV-1/CA-1) ──

    def _save(self) -> None:  # type: ignore[override]
        """Persiste el DELTA del read model a sqlite, transaccional.

        Diff contra `_saved_docs`/`_saved_edges`: un `append` de 1 doc
        produce 1 INSERT (nunca reescribe filas ajenas — CA-1).

        F1 (M-02, ADR-WALKER-075): el sync de FTS ocurre DENTRO de esta
        MISMA transacción. Para external content, el comando 'delete' exige
        los valores indexados ORIGINALES: se capturan de la view ANTES de
        tocar `docs`; los nuevos se leen de la view DESPUÉS de escribir.
        El UPDATE de docs pasa a upsert (ON CONFLICT DO UPDATE) para
        PRESERVAR el rowid — vínculo fts.rowid == docs.rowid (D2); con
        INSERT OR REPLACE el rowid cambiaba por update y dejaba postings
        huérfanas (hits muertos, FS-1).
        """
        cur = self._db
        try:
            with cur:  # commit/rollback automático (sqlite3)
                # ── diff de docs (solo hashes + raws, sin writes aún) ──
                # FIX perf: UN json.dumps(sort_keys) por doc sirve para el
                # hash (payload canónico ≡ _sha1) Y para el INSERT/UPDATE —
                # antes eran 2 dumps por fila cambiada + 1 por no-cambiada.
                _ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                current: dict[str, str] = {}
                raws: dict[str, str] = {}
                for doc_id, data in self._docs.items():
                    raw = json.dumps(
                        data, sort_keys=True, ensure_ascii=False, default=str
                    )
                    raws[doc_id] = raw
                    current[doc_id] = _sha1_raw(raw)
                changed = [
                    d for d, h in current.items() if self._saved_docs.get(d) != h
                ]
                deleted = list(set(self._saved_docs) - set(current))

                # ── FTS: valores VIEJOS de los afectados (ANTES de tocar
                #    docs — el 'delete' de external content los exige) ──
                old_fts: dict[str, tuple] = {}
                if self._fts_fields and (changed or deleted):
                    old_fts = fts_mod.fetch_view_rows(
                        cur, self.name, self._fts_fields,
                        [*changed, *deleted],
                    )

                # ── docs (upsert rowid-preserving: D2 vínculo por rowid) ──
                for doc_id in changed:
                    cur.execute(
                        "INSERT INTO docs (`index`, id, data, updated_at) "
                        "VALUES (?,?,?,?) "
                        "ON CONFLICT(`index`, id) DO UPDATE SET "
                        "data=excluded.data, updated_at=excluded.updated_at",
                        (self.name, doc_id, raws[doc_id], _ts),
                    )
                for doc_id in deleted:
                    cur.execute(
                        "DELETE FROM docs WHERE `index`=? AND id=?",
                        (self.name, doc_id),
                    )
                self._saved_docs = current

                # ── FTS: deletes con valores indexados originales ──
                if self._fts_fields:
                    for doc_id in (*deleted, *changed):
                        row = old_fts.get(doc_id)
                        if row is not None:
                            fts_mod.apply_delete(
                                cur, self.name, self._fts_fields, row
                            )

                # ── edges (diff por par, O(E) al vuelo) ──
                graph_edges: dict[tuple[str, str], str] = {}
                graph_raws: dict[tuple[str, str], str] = {}
                for u, v, data in self._graph.edges(data=True):
                    raw = json.dumps(
                        dict(data), sort_keys=True, ensure_ascii=False, default=str
                    )
                    graph_raws[(u, v)] = raw
                    graph_edges[(u, v)] = _sha1_raw(raw)
                for key in set(self._saved_edges) - set(graph_edges):
                    cur.execute(
                        "DELETE FROM edges WHERE `index`=? AND src=? AND dst=?",
                        (self.name, key[0], key[1]),
                    )
                for (u, v), h in graph_edges.items():
                    if (u, v) in self._saved_edges:
                        if self._saved_edges[(u, v)] == h:
                            continue  # sin cambios — 0 writes, 0 dumps extra
                        cur.execute(
                            "UPDATE edges SET attributes=? "
                            "WHERE `index`=? AND src=? AND dst=?",
                            (graph_raws[(u, v)], self.name, u, v),
                        )
                    else:
                        cur.execute(
                            "INSERT OR REPLACE INTO edges (`index`, src, dst, attributes) "
                            "VALUES (?,?,?,?)",
                            (self.name, u, v, graph_raws[(u, v)]),
                        )
                self._saved_edges = graph_edges

                # ── metadata (passthrough, ADR-054 D5) ──
                # NOTA F1: las system-keys (incl. fts_config) NUNCA se
                # tocan aquí — borrar el marker FTS = FS-2 al boot.
                mh = _sha1(self._metadata)
                if mh != self._metadata_hash:
                    cur.execute(
                        "DELETE FROM meta WHERE `index`=? AND key NOT IN "
                        "('__schema__', 'schema_hash', ?)",
                        (self.name, fts_mod.MARKER_KEY),
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

                # ── FTS: inserts con valores NUEVOS (docs ya escritos) ──
                if self._fts_fields and changed:
                    new_rows = fts_mod.fetch_view_rows(
                        cur, self.name, self._fts_fields, changed
                    )
                    fts_mod.apply_insert(
                        cur, self.name, self._fts_fields,
                        [new_rows[d] for d in changed if d in new_rows],
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
        # Robustez: el backend garantiza su ubicación (el dir padre puede no
        # existir aún — tests/proyectos nuevos). Antes fallaba con
        # "unable to open database file".
        parent = os.path.dirname(self._db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
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
        # Restaura el catálogo de índices persistido (opción 2 — adr).
        # Al reabrir una BD existente los índices de dominio ya registrados
        # quedan disponibles sin depender de que cada caller recuerde
        # volver a registrarlos (resuelve de raíz el "Índice 'X' no
        # registrado. Disponibles: []").
        self._restore_persisted_indexes()
        # F1 (§5.1): FTS para índices con fulltext — crea+backfill si falta,
        # verifica conteos si existe (fail-fast en drift FS-1). ANTES de
        # cualquier acceso: todo write posterior sincroniza FTS en su
        # transacción (M-02).
        self._ensure_fts_indexes()

    def _persist_index_config(self, name: str, schema: IndexSchema) -> None:
        """Guarda el config del schema para restaurarlo al reabrir la BD.

        Se usa INSERT OR REPLACE: el registro explícito SIEMPRE gana (si el
        caller registra con un schema actualizado, la persistencia sigue).
        El search_handler (callable Python) no es serializable → no se
        persiste; los que no requieren handler (cpg/ckg/delta/trace) se
        restauran sin problema.
        """
        with self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO meta (`index`, key, value) VALUES (?,?,?)",
                (
                    "__indexes__",
                    f"{name}/config",
                    json.dumps(schema.to_config(), ensure_ascii=False, default=str),
                ),
            )

    def _restore_persisted_indexes(self) -> None:
        """Restaura los índices cuyo config fue persistido en `meta`."""
        try:
            rows = self._db.execute(
                "SELECT key, value FROM meta WHERE `index`='__indexes__'"
            ).fetchall()
        except sqlite3.DatabaseError:
            # Guarda de lector: si la tabla meta no expone el catálogo (BD
            # antigua), no hay índices persistidos — proceder con vacío no es
            # un fallo silencioso, el registro explícito sigue siendo idempotente.
            return
        for row in rows:
            key, value = row["key"], row["value"]
            if not key.endswith("/config"):
                continue
            name = key[: -len("/config")]
            try:
                config = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                continue
            self._register_index_without_persist(name, config)

    def _register_index_without_persist(self, name: str, config: dict) -> None:
        """Registra un índice sin persistir (uso interno de restauración)."""
        schema = config if isinstance(config, IndexSchema) else IndexSchema(config)
        self._schemas[name] = schema
        self._indexes[name] = SqliteIndex(
            name, schema, self._db, None, db_path=self._db_path
        )

    def registered_schemas(self) -> dict[str, IndexSchema]:
        """Devuelve los schemas registrados (restaurados + explícitos).

        Permite a la facade `CognitiveGraph` sincronizar su mapa ligero de
        schemas con los índices que el backend restauró de la BD.
        """
        return dict(self._schemas)

    @property
    def _base_dir(self) -> str:
        return os.path.dirname(self._db_path)

    # ── Setup ──

    @staticmethod
    def _resolve_db_path(conn: MemoryConnection) -> str:
        uri = conn.uri
        if uri.startswith("sqlite://"):
            path = uri[len("sqlite://"):]
            if not path:
                # INV-016 (fail-fast, sin fallbacks): un uri "sqlite://" con
                # path vacío resolvería cognitive.sqlite3 en el CWD del
                # proceso y crearía una base parásita fuera del proyecto
                # (vector histórico del strays en la raíz del repo).
                raise ValueError(
                    "MemoryConnection.uri='sqlite://' con path vacío — fail-fast "
                    "(INV-016): resolvería cognitive.sqlite3 en el CWD y crearía "
                    "una base parásita. Ancla el uri a un directorio explícito "
                    "(p.ej. sqlite://<data_dir> via load_memory_connection(base_dir) "
                    "o WALKER_MEMORY_URI)."
                )
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

    def _ensure_fts_indexes(self) -> None:
        """F1 (§5.1/M-04): FTS por índice con fulltext.

        Idempotente vía marker `fts_config` en meta. Al crear: view +
        VIRTUAL TABLE + backfill por lotes + marker en UNA transacción
        (FS-2). Al existir: reconciliación count(docs)==count(fts) —
        drift → StorageStateError accionable (FS-1, nunca auto-heal
        silencioso que enmascare writers pre-F1).
        """
        for name, schema in list(self._schemas.items()):
            if not schema.has_fulltext():
                continue
            report = fts_mod.ensure_fts(self._db, name, schema.fulltext_fields)
            # FS-2 detection: conteos pre/post loggeados en la migración.
            print(f"[fts] {name}: {report['status']} docs={report['docs']} "
                  f"fields={report.get('fields', schema.fulltext_fields)}")
            idx = self._indexes.get(name)
            if idx is not None:
                idx._fts_fields = list(schema.fulltext_fields)

    def register_index(self, name: str, schema: Any, search_handler: Any = None) -> None:
        if isinstance(schema, dict):
            schema = IndexSchema(schema)
        self._schemas[name] = schema
        self._indexes[name] = SqliteIndex(
            name, schema, self._db, search_handler, db_path=self._db_path
        )
        if schema.has_fulltext():
            report = fts_mod.ensure_fts(self._db, name, schema.fulltext_fields)
            print(f"[fts] {name}: {report['status']} docs={report['docs']} "
                  f"fields={report.get('fields', schema.fulltext_fields)}")
            self._indexes[name]._fts_fields = list(schema.fulltext_fields)
        # Opción 2 — persiste el catálogo para que reabrir la BD restaure el
        # índice (el search_handler no es serializable → no se persiste).
        try:
            self._persist_index_config(name, schema)
        except sqlite3.DatabaseError:
            # No fallar el registro si la persistencia del catálogo falla;
            # el índice queda funcional en memoria (fail-open acotado al
            # catálogo, no al storage).
            pass

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

    def get_metadata_only(self, index: str) -> dict:
        return self._idx(index).get_metadata_only()

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