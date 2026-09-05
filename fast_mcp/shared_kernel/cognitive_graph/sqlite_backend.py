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
import logging
import os
import sqlite3
import time
from typing import Any

import networkx as nx
from returns.pipeline import is_successful
from returns.result import safe

from . import fts as fts_mod
from . import structured as structured_mod
from .connection import MemoryConnection
from .index import Index, IndexSchema
from .ports import StorageBackend
from .storage_errors import (
    StorageContractError,
    StorageIntegrityError,
    StorageReadError,
    StorageSchemaVersionError,
    StorageStateError,
    StorageWriteError,
)

logger = logging.getLogger("walker.cognitive_graph.sqlite_backend")

_SCHEMA_VERSION = "v3"
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
        # T-05 (M-07): cache de raw json por edge + dirty set — _save difiere
        # SOLO edges tocados, jamás re-serializa el grafo completo (O(E)).
        self._edge_raws: dict[tuple[str, str], str] = {}  # (u,v) -> raw json
        self._dirty_edges: set[tuple[str, str]] = set()  # tocados desde el último save
        self._metadata_hash: str = ""
        # F1 (ADR-WALKER-075 D2): columnas FTS activas para este índice
        # (schema.search.fields) o None si el índice no tiene FTS — lo setea
        # SqliteBackend tras ensure_fts (boot/register). Con FTS activo el
        # fulltext va por MATCH y el TokenIndex NO se reconstruye en load.
        self._fts_fields: list[str] | None = None
        # F3 (D4): read model LAZY — dos niveles de carga:
        #   _meta_loaded: diff-cache (_saved_docs desde columna doc_sha1,
        #     sin parsear data) + edges (grafo eager) + metadata.
        #   _loaded: read model COMPLETO (docs materializados + índices
        #     secundarios) — solo tras materialize_all()/ops que lo exigen.
        # Los writes en índice ligero NO materializan: diff por doc_sha1 +
        # tombstones (_deleted_docs) — jamás "saved - current" (con docs
        # no materializados eso borrará TODO lo no tocado).
        self._meta_loaded: bool = False
        self._deleted_docs: set[str] = set()
        # T-07 (M-09): el fallback eager es VISIBLE — warning + métrica con
        # los docs materializados; jamás degradación silenciosa de coste.
        self._fallback_materializations: int = 0
        self._fallback_last_docs: int = 0
        self._fallback_last_reason: str = ""

    # ── Locks: SQLite+WAL serializa; sin fcntl (evita ficheros .lock) ──

    def _acquire_write_lock(self) -> None:
        return None  # WAL: 1 escritor, serializado por sqlite (IV-2)

    def _release_write_lock(self) -> None:
        return None

    # ── T-05 (M-07): cache de edges — TODO mutador pasa por aquí ──

    def _cache_edge(self, u: str, v: str, data: dict) -> None:
        """Serializa UNA vez el edge (o reutiliza el raw existente) y lo marca dirty."""
        raw = json.dumps(
            dict(data), sort_keys=True, ensure_ascii=False, default=str
        )
        self._edge_raws[(u, v)] = raw
        self._dirty_edges.add((u, v))

    def _drop_edge_cache(self, key: tuple[str, str]) -> None:
        """Edge eliminado del grafo → sale del cache, entra al diff como DELETE."""
        self._edge_raws.pop(key, None)
        self._dirty_edges.add(key)

    # ── Persistencia: carga en dos niveles (F3, D4/M-09) ──

    def _load(self) -> None:  # type: ignore[override]
        """Compat del contrato base: light-load + materialización completa.

        Cualquier op de la clase base (search combinado, get_docs,
        get_related, ...) obtiene el read model completo — la lazy es el
        DEFAULT, no un modo especial (materialize_all() es la API explícita
        para consumidores full-graph).
        """
        self._ensure_light()
        if not self._loaded:
            self._materialize_docs()
            self._loaded = True

    def _ensure_light(self) -> None:
        """Nivel 1 (M-09): diff-cache + edges + metadata — SIN parsear docs."""
        if self._meta_loaded:
            return
        try:
            # diff-cache de docs DESDE LA COLUMNA doc_sha1 — cero json.loads
            # por fila (M-10: jamás recomputar hashes con dumps en el load)
            self._saved_docs = {}
            for raw_id, sha in self._db.execute(
                "SELECT id, doc_sha1 FROM docs WHERE `index`=?", (self.name,)
            ):
                self._saved_docs[raw_id] = sha or ""
            # edges eager (D4 solo hace lazy los docs: related/neighbors y
            # todo el write path de edges quedan intactos)
            self._saved_edges = {}
            self._edge_raws = {}
            self._dirty_edges = set()
            for src, dst, attrs_raw in self._db.execute(
                "SELECT src, dst, attributes FROM edges WHERE `index`=?",
                (self.name,),
            ):
                attrs = json.loads(attrs_raw) if attrs_raw else {}
                self._graph.add_edge(src, dst, **attrs)
                self._saved_edges[(src, dst)] = _sha1_raw(attrs_raw or "{}")
                # el raw almacenado es el json canónico del último save →
                # se cachea sin re-serializar (T-05/M-07)
                self._edge_raws[(src, dst)] = attrs_raw or "{}"
            self._metadata = {}
            self._metadata_hash = ""
            for key, value in self._db.execute(
                "SELECT key, value FROM meta WHERE `index`=? AND value IS NOT NULL",
                (self.name,),
            ):
                if key in _META_SYSTEM_KEYS:
                    continue
                try:
                    self._metadata[key] = json.loads(value)
                except json.JSONDecodeError:
                    self._metadata[key] = value
            if self._metadata:
                self._metadata_hash = _sha1(self._metadata)
            # NOTA: los índices secundarios/token index NO se reconstruyen
            # aquí — con _docs vacío serían PARCIALES (FS-4). materialize()
            # los levanta; el router F2 resuelve los filtros por SQL.
            self._meta_loaded = True
        except (sqlite3.Error, json.JSONDecodeError) as e:
            raise StorageReadError(
                f"SqliteIndex['{self.name}']: no se pudo cargar el estado "
                f"ligero ({type(e).__name__}: {e})"
            ) from e

    def _materialize_docs(self) -> None:
        """Nivel 2: materializa docs + índices secundarios (one-shot)."""
        try:
            docs: dict[str, dict] = {}
            for raw_id, raw_data in self._db.execute(
                "SELECT id, data FROM docs WHERE `index`=?", (self.name,)
            ):
                docs[raw_id] = json.loads(raw_data)
            self._docs = docs
            for doc_id, doc_data in docs.items():
                self._graph.add_node(doc_id, **doc_data)
            self._rebuild_secondary_indexes()
        except (sqlite3.Error, json.JSONDecodeError) as e:
            raise StorageReadError(
                f"SqliteIndex['{self.name}']: no se pudo materializar el "
                f"read model ({type(e).__name__}: {e})"
            ) from e

    def materialize_all(self) -> dict:
        """F3 (D4/M-09): materialización EXPLÍCITA del read model completo.

        CONTRATO para consumidores full-graph (scan_cfg, DFG, quality,
        agent_context — FS-4): llamar SIEMPRE antes de recorrer docs/grafo
        completos; el default lazy no garantiza materialización.
        """
        self._load()
        return {"ok": True, "docs": len(self._docs),
                "edges": self._graph.number_of_edges(), "_index": self.name}

    # ── F3: ops light — writes y lecturas puntuales SIN materializar ──

    def get(self, doc_id: str) -> dict | None:
        """F3 (D4): doc on-demand — 1 SELECT, sin materializar el índice."""
        self._ensure_light()
        doc = self._docs.get(doc_id)
        if doc is None:
            row = self._db.execute(
                "SELECT data FROM docs WHERE `index`=? AND id=?",
                (self.name, doc_id),
            ).fetchone()
            if row is None:
                return None
            doc = json.loads(row[0])
            self._docs[doc_id] = doc
            self._graph.add_node(doc_id, **doc)
        return {"_id": doc_id, "_index": self.name, "_source": doc}

    def keys(self) -> list[str]:
        """F3: inventario de ids desde el diff-cache (sin materializar)."""
        self._ensure_light()
        return list(self._saved_docs.keys())

    def max_key(self, prefix: str = "") -> tuple[str, int] | None:
        """F3: (id, num) máximo desde el diff-cache (sin materializar)."""
        self._ensure_light()
        best_id: str | None = None
        best_num = -1
        for doc_id in self._saved_docs:
            if prefix and not doc_id.startswith(prefix + "-"):
                continue
            tail = doc_id.rsplit("-", 1)[-1] if "-" in doc_id else ""
            if tail.isdigit():
                num = int(tail)
                if num > best_num:
                    best_num = num
                    best_id = doc_id
        if best_id is None:
            return None
        return best_id, best_num

    @safe
    def _store_core(self, doc_id: str, data: dict) -> dict:  # type: ignore[override]
        """F3: write SIN materializar (diff por doc_sha1 + tombstones).

        Los índices secundarios solo se mantienen si el índice está
        materializado; en estado ligero quedan vacíos hasta materialize()
        (el router F2 resuelve por SQL, el path base materializa antes de
        usarlos — FS-4 sin lecturas parciales).
        """
        self._ensure_light()
        old = self._docs.get(doc_id)
        if self._loaded:
            if old is not None:
                self._deindex_doc(doc_id)
            self._index_doc(doc_id, data)
        self._docs[doc_id] = data
        self._graph.add_node(doc_id, **data)
        self._save()
        return {"ok": True, "_id": doc_id, "_index": self.name}

    @safe
    def _delete_core(self, doc_id: str) -> dict:  # type: ignore[override]
        """F3: delete SIN materializar — tombstone explícito (el diff jamás
        asume que _docs contiene TODO lo commitado)."""
        self._ensure_light()
        existed = doc_id in self._saved_docs or doc_id in self._docs
        if existed and self._loaded and doc_id in self._docs:
            self._deindex_doc(doc_id)
        self._docs.pop(doc_id, None)
        if doc_id in self._graph:
            # remove_node elimina también los edges incidentes → al diff
            self._drop_incident_edges(doc_id)
            self._graph.remove_node(doc_id)
        if existed:
            self._deleted_docs.add(doc_id)
            self._save()
        return {"ok": True, "deleted": existed, "_id": doc_id, "_index": self.name}

    @safe
    def _store_many_light_core(self, docs: dict[str, dict]) -> dict:
        """Core F3 de store_many (mismo contrato que _store_many_core base)."""
        self._ensure_light()
        skipped = 0
        touched = 0
        for doc_id, data in docs.items():
            old = self._docs.get(doc_id)
            if old == data:
                skipped += 1
                continue
            touched += 1
            if self._loaded:
                if old is not None:
                    self._deindex_doc(doc_id)
                self._index_doc(doc_id, data)
            self._docs[doc_id] = data
            self._graph.add_node(doc_id, **data)
        self._save()
        return {"ok": True, "stored": len(docs), "_index": self.name,
                "skipped": skipped, "touched": touched}

    def store_many(self, docs: dict[str, dict]) -> dict:
        """Indexa muchos nodos en batch SIN materializar (F3)."""
        self._acquire_write_lock()
        self._write_locked = True
        try:
            result = self._store_many_light_core(docs)
            if is_successful(result):
                return result.value_or({"ok": False, "stored": 0})
            return {"ok": False, "stored": 0, "error": str(result.failure())}
        finally:
            self._write_locked = False
            self._release_write_lock()

    def delete_many(self, doc_ids: set[str]) -> dict:
        """Elimina varios nodos SIN materializar (F3, tombstones)."""
        if not doc_ids:
            return {"ok": True, "deleted": 0}
        self._acquire_write_lock()
        self._write_locked = True
        try:
            self._ensure_light()
            deleted = 0
            for doc_id in doc_ids:
                if doc_id in self._saved_docs or doc_id in self._docs:
                    if self._loaded and doc_id in self._docs:
                        self._deindex_doc(doc_id)
                    self._docs.pop(doc_id, None)
                    if doc_id in self._graph:
                        self._drop_incident_edges(doc_id)
                        self._graph.remove_node(doc_id)
                    self._deleted_docs.add(doc_id)
                    deleted += 1
            if deleted:
                self._save()
            return {"ok": True, "deleted": deleted, "_index": self.name}
        finally:
            self._write_locked = False
            self._release_write_lock()

    def _drop_incident_edges(self, doc_id: str) -> None:
        """Marca dirty los edges incidentes de un nodo antes de remove_node."""
        for u, v in list(self._graph.in_edges(doc_id)) + list(
            self._graph.out_edges(doc_id)
        ):
            self._drop_edge_cache((u, v))

    def add_edge(self, from_id: str, to_id: str, **attrs: Any) -> dict:
        """F3: edge op SIN materializar docs (grafo de edges es eager)."""
        self._ensure_light()
        self._graph.add_edge(from_id, to_id, **attrs)
        self._cache_edge(
            from_id, to_id, self._graph.get_edge_data(from_id, to_id)
        )
        self._save()
        return {"ok": True, "from": from_id, "to": to_id, "attrs": attrs}

    def remove_edge(self, from_id: str, to_id: str) -> dict:
        """F3: edge op SIN materializar docs."""
        self._ensure_light()
        if self._graph.has_edge(from_id, to_id):
            self._graph.remove_edge(from_id, to_id)
            self._drop_edge_cache((from_id, to_id))
            self._save()
        return {"ok": True, "from": from_id, "to": to_id}

    def sync_edges(self, edges: list[dict]) -> dict:
        """F3: reemplazo completo de edges SIN materializar docs (T-05).

        En índice ligero el grafo se reconstruye solo con los edges nuevos
        (los nodos se materializan aparte); materializado → base (re-add de
        nodos con payload, comportamiento histórico). El cache de raws se
        reinicia: los edges viejos entran al diff como DELETE si no
        reaparecen — jamás diff `saved - current` con cache stale.
        """
        self._acquire_write_lock()
        self._write_locked = True
        try:
            if self._loaded:
                self._load()
            else:
                self._ensure_light()
            old_keys = set(self._saved_edges)
            self._graph = nx.DiGraph()
            for doc_id, doc in self._docs.items():
                self._graph.add_node(doc_id, **doc)
            self._edge_raws = {}
            self._dirty_edges = set(old_keys)  # los que no reaparezcan → DELETE
            for edge in edges or []:
                u = edge.get("from", "")
                v = edge.get("to", "")
                if not u or not v:
                    continue
                attrs = {k: val for k, val in edge.items() if k not in ("from", "to")}
                self._graph.add_edge(u, v, **attrs)
                self._cache_edge(u, v, attrs)
            self._save()
            return {"ok": True, "edges": len(edges or []), "_index": self.name}
        finally:
            self._write_locked = False
            self._release_write_lock()

    def replace_blob(self, nodes: dict, edges: list, metadata: dict) -> dict:
        """F3: reemplazo total SIN materializar el estado VIEJO (tombstones).

        Versión light del ciclo base: reset → indexar los NUEVOS → save.
        El read model post-op = el estado nuevo completo (_loaded=True);
        lo commitado anterior se borra por tombstones (jamás diff
        `saved - current`, que con docs no materializados borra de más).
        """
        self._ensure_light()
        self._deleted_docs |= set(self._saved_docs) - set(nodes or {})
        self._acquire_write_lock()
        self._write_locked = True
        try:
            self._docs = {}
            self._graph = nx.DiGraph()
            # reemplazo total: los edges commitados entran al diff como
            # DELETE salvo que reaparezcan (T-05/M-07)
            self._edge_raws = {}
            self._dirty_edges = set(self._saved_edges)
            self._token_index = None  # FTS es el camino en sqlite (D2)
            self._type_index.clear()
            self._field_index.clear()
            self._metadata = dict(metadata or {})
            for doc_id, doc in (nodes or {}).items():
                if not isinstance(doc, dict):
                    raise TypeError(
                        f"nodo '{doc_id}' inválido (esperaba dict, obtuvo "
                        f"{type(doc).__name__})"
                    )
                self._index_doc(doc_id, doc)
                self._docs[doc_id] = doc
                self._graph.add_node(doc_id, **doc)
            for edge in edges or []:
                u = edge.get("from", "")
                v = edge.get("to", "")
                if not u or not v:
                    continue
                attrs = {k: val for k, val in edge.items() if k not in ("from", "to")}
                self._graph.add_edge(u, v, **attrs)
                self._cache_edge(u, v, attrs)
            self._save()
            self._loaded = True  # el read model = el estado nuevo completo
            return {"ok": True, "stored": len(nodes or {}), "edges": len(edges or [])}
        finally:
            self._write_locked = False
            self._release_write_lock()

    def _fetch_docs_bulk(self, doc_ids: list[str], chunk: int = 500) -> dict[str, dict]:
        """docs por lote de ids (página de search on-demand — M-07/M-09)."""
        out: dict[str, dict] = {}
        ids = list(doc_ids)
        for i in range(0, len(ids), chunk):
            part = ids[i:i + chunk]
            phs = ", ".join("?" * len(part))
            for doc_id, data in self._db.execute(
                f"SELECT id, data FROM docs WHERE `index`=? AND id IN ({phs})",
                (self.name, *part),
            ):
                out[doc_id] = json.loads(data)
        return out

    def _sql_neighbor_ids(self, doc_id: str) -> list[dict]:
        """_related vía SQL (ruta de search sin materializar): out por PK
        prefix, in por idx_edges_dst (F2), labels por fetch bulk."""
        out: list[dict] = []
        neighbor_ids: list[str] = []
        pairs_out: list[tuple[str, dict]] = []
        pairs_in: list[tuple[str, dict]] = []
        for dst, attrs_raw in self._db.execute(
            "SELECT dst, attributes FROM edges WHERE `index`=? AND src=?",
            (self.name, doc_id),
        ):
            attrs = json.loads(attrs_raw) if attrs_raw else {}
            pairs_out.append((dst, attrs))
            neighbor_ids.append(dst)
        for src, attrs_raw in self._db.execute(
            "SELECT src, attributes FROM edges WHERE `index`=? AND dst=?",
            (self.name, doc_id),
        ):
            attrs = json.loads(attrs_raw) if attrs_raw else {}
            pairs_in.append((src, attrs))
            neighbor_ids.append(src)
        labels: dict[str, str] = {}
        uniq = list(dict.fromkeys(neighbor_ids))
        if uniq:
            for nid, doc in self._fetch_docs_bulk(uniq).items():
                labels[nid] = doc.get("objective") or doc.get("name") or nid
        for dst, attrs in pairs_out:
            out.append({"id": dst, "rel": attrs.get("type", "related"),
                        "label": labels.get(dst, dst)})
        for src, attrs in pairs_in:
            out.append({"id": src, "rel": attrs.get("type", "related") + " (incoming)",
                        "label": labels.get(src, src)})
        return out

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
        """Router de search nativo de SqliteIndex (ADR-WALKER-075 D2/D3).

        - Texto puro → FTS5 MATCH con orden bm25 y total real (F1, M-01).
        - Estructurado puro → WHERE por columnas generadas e índices SQL,
          cargando SOLO los hits de la página (F2, M-05/M-07) — sin
          materializar el read model.
        - Combinado (texto + filtros), filtros no resolubles por SQL, o
          search_handler legacy → Index.search base (texto vía
          `_fulltext_ids` = FTS; filtros vía índices secundarios en RAM).

        Semántica idéntica entre rutas (M-08): exacto para `type` (como
        _type_index), lowered+COALESCE(metadata) para el resto (como
        _field_index + _match).
        """
        if self._search_handler is not None or not isinstance(query, dict):
            return super().search(query)
        filters = dict(query)
        raw_text = filters.pop("text", None)
        for _mc in ("max_results", "size", "max_events", "_index", "query",
                    "_from", "from", "text_fields"):
            filters.pop(_mc, None)
        if raw_text is not None:
            if filters or self._fts_fields is None:
                # combinado → base (materializa); texto puro sin FTS → base
                reason = ("query combinada text+filtros" if filters
                          else "texto sin FTS activo")
                return self._fallback_eager(query, reason)
            return self._search_text_only(raw_text, query)
        clauses = structured_mod.resolve_filters(self.schema, filters)
        if clauses is None:
            return self._fallback_eager(
                query, f"filtros no resolubles por SQL: {sorted(filters)}"
            )
        return self._search_structured(clauses, query)

    def _fallback_eager(self, query: dict, reason: str) -> dict:
        """T-07 (M-09): fallback eager DOCUMENTADO — warning + métrica con
        el nº de docs que la materialización va a tocar.

        El filtro no resuelve por columnas generadas/SQL (o el contrato lo
        exige) → Index.search base materializa el read model completo
        (O(N) en RAM). El coste queda visible: log + contadores en
        metrics(); el resultado es IDÉNTICO al path base (M-08 paridad).
        """
        self._ensure_light()
        materializing = len(self._saved_docs)
        result = super().search(query)
        self._fallback_materializations += 1
        self._fallback_last_docs = materializing
        self._fallback_last_reason = reason
        logger.warning(
            "search('%s'): fallback EAGER (%s) — materializa %d docs del "
            "read model (coste O(N) en RAM). Si el filtro es de alto "
            "tráfico, añadir columna generada al schema (F2) para "
            "resolverlo por SQL (M-09: fallback visible, no degradación "
            "silenciosa).",
            self.name, reason, materializing,
        )
        return result

    @staticmethod
    def _page_params(query: dict) -> tuple[int, int]:
        """max_results/_from con los defaults del contrato (G8)."""
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
        return max_results, _from

    def _search_structured(self, clauses: list, query: dict) -> dict:
        """F2: filtros estructurados resueltos por índice SQL (M-05/M-07).

        NO materializa el read model: COUNT por el índice + SELECT de la
        página con json.loads SOLO de los hits.
        """
        max_results, _from = self._page_params(query)
        took = time.time()
        res = structured_mod.search_structured(
            self._db, self.name, clauses,
            max_results=max_results, offset=_from,
        )
        took = int((time.time() - took) * 1000)
        return {
            "took": took,
            "index": self.name,
            "hits": {
                "total": res["total"],
                "hits": res["hits"],
                "from": res["from"],
                "size": res["size"],
            },
            "schema": self._schema_info(),
        }

    def _search_text_only(self, raw_text: Any, query: dict) -> dict:
        """F1 (D2/Enmienda 1): query pura-textual resuelve 100% vía FTS5
        MATCH con orden bm25 y total real — el contrato declara mismo SET
        de docs, orden por bm25 (mejora documentada, no orden idéntico)."""
        max_results, _from = self._page_params(query)
        if isinstance(raw_text, dict):
            text = raw_text.get("query", "")
            fields_scoped = raw_text.get("fields")
        else:
            text, fields_scoped = str(raw_text), None
        if fields_scoped is None:
            tf = query.get("text_fields")
            fields_scoped = tf if isinstance(tf, list) else ([tf] if tf else None)
        if fields_scoped and not set(fields_scoped) <= set(self._fts_fields or []):
            raise StorageContractError(
                f"field-scoped search sobre columna(s) sin FTS en "
                f"'{self.name}': {sorted(set(fields_scoped) - set(self._fts_fields or []))} "
                f"(columnas FTS: {self._fts_fields}) — añada el campo a "
                f"IndexSchema.search.fields y re-backfill (drop_fts + reabrir)."
            )

        took = time.time()
        self._ensure_light()  # F3/M-09: search NO materializa el read model
        ranked, total = fts_mod.search_fts(
            self._db, self.name, text,
            max_results=max_results, fields_scoped=fields_scoped,
            offset=_from,
        )
        # F3: los _source de la PÁGINA llegan por SQL (M-07/M-09) — jamás
        # materializar el índice completo para armar hits.
        page_docs = self._fetch_docs_bulk([doc_id for doc_id, _ in ranked])
        results = []
        for doc_id, rank in ranked:
            doc = page_docs.get(doc_id)
            if doc is None:  # FTS-drift no debería pasar (reconcile en boot)
                continue
            entry = {
                "_id": doc_id,
                "_index": self.name,
                "_score": round(-rank, 6),  # bm25 es costo; _score = relevancia
                "_source": doc,
            }
            related = self._sql_neighbor_ids(doc_id)
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
                # F3: tombstones EXPLÍCITOS — jamás `saved - current` (con
                # docs no materializados eso borraría TODO lo no tocado).
                deleted = [
                    d for d in self._deleted_docs
                    if d in self._saved_docs and d not in current
                ]

                # ── FTS: valores VIEJOS de los afectados (ANTES de tocar
                #    docs — el 'delete' de external content los exige) ──
                old_fts: dict[str, tuple] = {}
                if self._fts_fields and (changed or deleted):
                    old_fts = fts_mod.fetch_view_rows(
                        cur, self.name, self._fts_fields,
                        [*changed, *deleted],
                    )

                # ── docs (upsert rowid-preserving + doc_sha1: D2/F3) ──
                for doc_id in changed:
                    cur.execute(
                        "INSERT INTO docs (`index`, id, data, updated_at, doc_sha1) "
                        "VALUES (?,?,?,?,?) "
                        "ON CONFLICT(`index`, id) DO UPDATE SET "
                        "data=excluded.data, updated_at=excluded.updated_at, "
                        "doc_sha1=excluded.doc_sha1",
                        (self.name, doc_id, raws[doc_id], _ts, current[doc_id]),
                    )
                for doc_id in deleted:
                    cur.execute(
                        "DELETE FROM docs WHERE `index`=? AND id=?",
                        (self.name, doc_id),
                    )
                # F3: el committed-state incluye lo NO tocado (light) —
                # jamás se sustituye por `current` (perdería lo no tocado).
                self._saved_docs = {
                    **{d: h for d, h in self._saved_docs.items()
                       if d not in set(deleted)},
                    **current,
                }

                # ── FTS: deletes con valores indexados originales ──
                if self._fts_fields:
                    for doc_id in (*deleted, *changed):
                        row = old_fts.get(doc_id)
                        if row is not None:
                            fts_mod.apply_delete(
                                cur, self.name, self._fts_fields, row
                            )

                # ── edges (T-05/M-07): diff SOLO de edges tocados — el raw
                #    json por edge vive en _edge_raws y los mutadores marcan
                #    dirty; jamás re-serialización O(E) del grafo completo ──
                if len(self._edge_raws) != self._graph.number_of_edges():
                    # Red de seguridad: una mutación no ruteada por el cache
                    # (edge presente sin raw) entra igualmente al diff.
                    for u, v in self._graph.edges():
                        if (u, v) not in self._edge_raws:
                            self._cache_edge(u, v, self._graph.get_edge_data(u, v))
                for key in list(self._dirty_edges):
                    raw = self._edge_raws.get(key)
                    if raw is None:
                        # edge eliminado del grafo → DELETE
                        if key in self._saved_edges:
                            cur.execute(
                                "DELETE FROM edges WHERE `index`=? AND src=? AND dst=?",
                                (self.name, key[0], key[1]),
                            )
                            del self._saved_edges[key]
                        continue
                    h = _sha1_raw(raw)
                    if self._saved_edges.get(key) == h:
                        continue  # sin cambios — 0 writes, 0 dumps
                    if key in self._saved_edges:
                        cur.execute(
                            "UPDATE edges SET attributes=? "
                            "WHERE `index`=? AND src=? AND dst=?",
                            (raw, self.name, key[0], key[1]),
                        )
                    else:
                        cur.execute(
                            "INSERT OR REPLACE INTO edges (`index`, src, dst, attributes) "
                            "VALUES (?,?,?,?)",
                            (self.name, key[0], key[1], raw),
                        )
                    self._saved_edges[key] = h
                self._dirty_edges = set()

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
                # F3: tombstones consumidos — SOLO tras éxito (un rollback
                # los conserva y el próximo _save re-intenta el delete).
                self._deleted_docs = set()
        except sqlite3.Error as e:
            raise StorageWriteError(
                f"SqliteIndex['{self.name}']: persistencia falló "
                f"({type(e).__name__}: {e}) — sin cambio parcial (rollback)."
            ) from e

    # ── metrics sin path de blob (IV-10: layout sqlite) ──

    def metrics(self) -> dict:  # type: ignore[override]
        self._ensure_light()
        self._verify_sha_sample()  # M-10/FS-3: doc_sha1 stale → fail-fast
        # T-09 (M-5/M-11): métricas TRUTHFUL — page_size REAL via pragma
        # (jamás la constante 4096) y WAL medido del archivo (FS-8). Un error
        # de lectura propaga fail-loud; nunca 0/constante silenciosa.
        try:
            page_count = int(self._db.execute(
                "SELECT page_count FROM pragma_page_count()"
            ).fetchone()[0])
            page_size = int(self._db.execute(
                "SELECT page_size FROM pragma_page_size()"
            ).fetchone()[0])
        except (sqlite3.Error, TypeError, IndexError) as exc:
            raise StorageReadError(
                f"SqliteIndex['{self.name}']: metrics() no pudo leer "
                f"page_count/page_size ({type(exc).__name__}: {exc}) — "
                "fail-loud (M-11), jamás métrica inventada."
            ) from exc
        size = page_count * page_size
        wal_path = self._db_path + "-wal"
        wal_bytes = os.path.getsize(wal_path) if os.path.exists(wal_path) else 0
        last_updated = "never"
        row = self._db.execute(
            "SELECT MAX(updated_at) FROM docs WHERE `index`=?", (self.name,)
        ).fetchone()
        if row and row[0]:
            last_updated = row[0]
        return {
            # F3: el truth de conteo es el diff-cache (light-safe) —
            # len(self._docs) en estado lazy es SOLO los docs tocados.
            "total_docs": len(self._saved_docs),
            "total_edges": self._graph.number_of_edges(),
            "storage_size": f"{size / 1024:.1f}KB",
            "storage_path": self._db_path,
            "last_updated": last_updated,
            # T-09 (FS-8): WAL observable — tamaño y páginas medidos, no estimados.
            "page_size": page_size,
            "wal_bytes": wal_bytes,
            "wal_pages": wal_bytes // page_size if page_size else 0,
            # T-07 (M-09): el coste del fallback eager es medible.
            "fallback_eager": {
                "materializations": self._fallback_materializations,
                "last_docs": self._fallback_last_docs,
                "last_reason": self._fallback_last_reason,
            },
        }

    def _verify_sha_sample(self, sample_size: int = 5) -> None:
        """M-10/FS-3: sample-verify determinista de doc_sha1 vs payload.

        Recomputar el sha del payload crudo (sin re-serializar) de N filas
        y compararlo contra la columna — un writer pre-v3 o una corrupción
        se detectan en el primer status, no silenciosamente.
        """
        ids = sorted(self._saved_docs)
        if not ids:
            return
        stride = max(1, len(ids) // sample_size)
        sampled = ids[::stride][:sample_size]
        phs = ", ".join("?" * len(sampled))
        for doc_id, data, sha in self._db.execute(
            f"SELECT id, data, doc_sha1 FROM docs "
            f"WHERE `index`=? AND id IN ({phs})",
            (self.name, *sampled),
        ):
            if (sha or "") != _sha1_raw(data):
                raise StorageStateError(
                    f"doc_sha1 stale en '{self.name}' para '{doc_id}' (FS-3): "
                    f"la columna no coincide con el payload — writer pre-v3 "
                    f"o corrupción. Remediación: restaurar backup o "
                    f"re-backfill (UPDATE docs SET doc_sha1='')."
                )


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
        # F3/M-12: guard de versión — v2 pendiente de migrar se resuelve al
        # final del boot (los pasos DDL/backfill son idempotentes); versión
        # desconocida → fail-loud (INV-016: jamás writers mixtos).
        self._pending_v2_migration = self._ensure_schema_version()
        self._schemas: dict[str, IndexSchema] = {}
        self._indexes: dict[str, SqliteIndex] = {}
        # Restaura el catálogo de índices persistido (opción 2 — adr).
        # Al reabrir una BD existente los índices de dominio ya registrados
        # quedan disponibles sin depender de que cada caller recuerde
        # volver a registrarlos (resuelve de raíz el "Índice 'X' no
        # registrado. Disponibles: []").
        self._restore_persisted_indexes()
        # F2 (§11): columnas generadas VIRTUAL + índices SQL — DDL
        # idempotente por inspección (PRAGMA/IF NOT EXISTS), instantáneo.
        self._ensure_structured_indexes()
        # F3 (D4/M-10): columna doc_sha1 + backfill one-time por lotes —
        # ANTES de FTS (los light-loads leen la columna).
        self._ensure_doc_sha1()
        # F1 (§5.1): FTS para índices con fulltext — crea+backfill si falta,
        # verifica conteos si existe (fail-fast en drift FS-1). ANTES de
        # cualquier acceso: todo write posterior sincroniza FTS en su
        # transacción (M-02).
        self._ensure_fts_indexes()
        # §10.1: v3 SOLO se marca tras éxito de todos los pasos (FS-2 —
        # crash a mitad = v2 persistente → próximo boot re-intenta, todo
        # idempotente).
        if self._pending_v2_migration:
            with self._db:
                self._db.execute(
                    "INSERT OR REPLACE INTO meta (`index`, key, value) "
                    "VALUES ('__db__','__schema__',?)", (_SCHEMA_VERSION,)
                )
            logger.info(
                "[migrate] schema %s marcado — migración v2→v3 completada "
                "(lazy in-situ, idempotente)", _SCHEMA_VERSION,
            )

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

    def _ensure_schema_version(self) -> str | None:
        """Guard fail-loud (M-12/INV-016).

        Returns:
            None: versión OK (v3 o BD nueva, ya marcada).
            "v2": migración v2→v3 PENDIENTE — el boot ejecuta los pasos
            DDL/backfill idempotentes y marca v3 al final (§10.1).

        Raises:
            StorageSchemaVersionError: versión desconocida (p.ej. BD v3
            abierta por código v2, o v1/legacy) — jamás datos stale.
        """
        row = self._db.execute(
            "SELECT value FROM meta WHERE `index`='__db__' AND key='__schema__'"
        ).fetchone()
        existing = row["value"] if row else None
        if existing is None:
            with self._db:
                self._db.execute(
                    "INSERT OR REPLACE INTO meta (`index`, key, value) "
                    "VALUES ('__db__','__schema__',?)",
                    (_SCHEMA_VERSION,),
                )
            return None
        if existing == _SCHEMA_VERSION:
            return None
        if existing == "v2":
            return "v2"
        raise StorageSchemaVersionError(
            f"schema_version '{existing}' no soportado (esperado "
            f"'{_SCHEMA_VERSION}'). Guarda de lector §6.4/INV-016 — fail "
            f"ruidoso, nunca datos stale. Si la BD es v3 y este proceso es "
            f"v2: reiniciar el proceso (guard fail-loud intencional)."
        )

    def _ensure_doc_sha1(self) -> None:
        """F3 (D4/M-10): columna `doc_sha1` + backfill one-time por lotes.

        El light-load y el diff del save leen/comparan ESTA columna —
        jamás recomputan dumps por fila en el load (anti-patrón M-10).
        Crash a mitad del backfill = filas pendientes ('') → el próximo
        boot re-anuda (idempotente, sin marker).
        """
        cols = {r[1] for r in self._db.execute("PRAGMA table_xinfo(docs)")}
        if "doc_sha1" not in cols:
            with self._db:
                self._db.execute(
                    "ALTER TABLE docs ADD COLUMN doc_sha1 TEXT NOT NULL DEFAULT ''"
                )
            logger.info("[f3] docs: +columna doc_sha1 (backfill a continuación)")
        pending = self._db.execute(
            "SELECT COUNT(*) FROM docs WHERE doc_sha1=''"
        ).fetchone()[0]
        if not pending:
            return
        backfilled = 0
        while True:
            rows = self._db.execute(
                "SELECT rowid, data FROM docs WHERE doc_sha1='' LIMIT 2000"
            ).fetchall()
            if not rows:
                break
            with self._db:
                self._db.executemany(
                    "UPDATE docs SET doc_sha1=? WHERE rowid=?",
                    [(_sha1_raw(data), rowid) for rowid, data in rows],
                )
            backfilled += len(rows)
        logger.info("[f3] doc_sha1 backfill: %d filas (lotes de 2000)", backfilled)

    def materialize_all(self, index: str) -> dict:
        """F3 (D4): materialización explícita del read model completo.

        Obligatorio para consumidores full-graph (scan_cfg, DFG, quality,
        agent_context) — el default lazy no garantiza materialización.
        """
        return self._idx(index).materialize_all()

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

    def _ensure_structured_indexes(self) -> None:
        """F2 (D3/M-06): columnas generadas VIRTUAL + índices SQL.

        Idempotente por inspección — solo reporta cuando hay DDL nuevo.
        """
        report = structured_mod.ensure_structured_indexes(self._db)
        if report["added_columns"]:
            logger.info(
                "[f2] docs: +columnas VIRTUAL %s + índices %s",
                report["added_columns"], report["indexes"],
            )

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
            logger.info(
                "[fts] %s: %s docs=%s fields=%s", name, report["status"],
                report["docs"],
                report.get("fields", schema.fulltext_fields),
            )
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
            logger.info(
                "[fts] %s: %s docs=%s fields=%s", name, report["status"],
                report["docs"],
                report.get("fields", schema.fulltext_fields),
            )
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