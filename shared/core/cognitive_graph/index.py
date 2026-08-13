"""CognicGraph storage engine — Indices (IndexSchema + Index).

Capa de almacenamiento pura: cada Index es un nx.DiGraph (nodos + edges) con
TokenIndex opcional para full-text. Sin logica de negocio ni conocimiento de
los dominios (CPG/CKG/Delta/Trace).

Importado por shared/cognitive_graph/__init__.py (CognitiveGraph facade).
"""

from __future__ import annotations
import gzip
import json
import os
import time
from typing import Any

from returns.result import safe
from returns.pipeline import is_successful

import networkx as nx

from .models import TokenIndex



class IndexSchema:
    """Schema de un indice: fields, types, storage, search config."""

    def __init__(self, config: dict):
        self.fields: dict[str, str] = config.get("fields", {})
        self.types: dict[str, dict] = config.get("types", {})
        storage = config.get("storage", {})
        self.storage_path: str = storage.get("path", "data/index.json.gz")
        self.storage_format: str = storage.get("format", "json.gz")
        self.description: str = config.get("description", "")
        # Config de busqueda full-text
        search_cfg = config.get("search", {})
        self.search_type: str = search_cfg.get("type", "structured")
        self.fulltext_fields: list[str] = search_cfg.get("fields", [])

    def resolve_field(self, query_field: str) -> str:
        return self.fields.get(query_field, query_field)

    def has_fulltext(self) -> bool:
        return self.search_type == "fulltext" and bool(self.fulltext_fields)


class Index:
    """Indice individual: grafo (nx.DiGraph) + schema + storage + searcher opcional.

    Almacena:
      - Nodos: _docs (dict id -> data) + _graph (nx.DiGraph)
      - Edges: _graph edges con atributos
      - Busqueda textual: _token_index (TokenIndex) si el schema lo configura
      - Indices secundarios O(1): _type_index (type -> set[id]),
        _field_index ((field, value) -> set[id]) para busqueda estructurada
        sin iterar todos los docs.
    """

    def __init__(
        self,
        name: str,
        schema: IndexSchema,
        chain_dir: str,
        search_handler: callable | None = None,
    ):
        self.name = name
        self.schema = schema
        self.chain_dir = chain_dir
        self._search_handler = search_handler
        self._docs: dict[str, dict] = {}
        self._graph: nx.DiGraph = nx.DiGraph()
        self._token_index: TokenIndex | None = (
            TokenIndex() if schema.has_fulltext() else None
        )
        # Indices secundarios: O(1) lookup por type y por field=value.
        # self._type_index[type] = set of doc_ids
        # self._field_index[(field, value)] = set of doc_ids
        self._type_index: dict[str, set[str]] = {}
        self._field_index: dict[tuple[str, str], set[str]] = {}
        self._loaded = False
        self._write_locked = False
        self._file_mtime: float = 0  # mtime del archivo al cargar (E3: stale detection)

    # ── Helpers de indices secundarios ──────────────────────────────

    def _index_doc(self, doc_id: str, data: dict) -> None:
        """Registra doc en indices secundarios (type + fields del schema)."""
        dtype = data.get("type", "")
        if dtype:
            self._type_index.setdefault(dtype, set()).add(doc_id)
        for qfield, rfield in self.schema.fields.items():
            if rfield == "type":
                continue  # ya indexado arriba
            val = self._resolve_field(data, rfield)
            if isinstance(val, str) and val:
                key = (rfield, val.lower() if isinstance(val, str) else val)
                self._field_index.setdefault(key, set()).add(doc_id)

    def _deindex_doc(self, doc_id: str, data: dict | None = None) -> None:
        """Remueve doc de indices secundarios. Si data=None, busca en _docs."""
        src = data or self._docs.get(doc_id)
        if not src:
            return
        dtype = src.get("type", "")
        if dtype and dtype in self._type_index:
            self._type_index[dtype].discard(doc_id)
            if not self._type_index[dtype]:
                del self._type_index[dtype]
        for qfield, rfield in self.schema.fields.items():
            if rfield == "type":
                continue
            val = self._resolve_field(src, rfield)
            if isinstance(val, str) and val:
                key = (rfield, val.lower())
                if key in self._field_index:
                    self._field_index[key].discard(doc_id)
                    if not self._field_index[key]:
                        del self._field_index[key]

    def _rebuild_secondary_indexes(self) -> None:
        """Reconstruye _type_index y _field_index desde _docs."""
        self._type_index.clear()
        self._field_index.clear()
        for doc_id, data in self._docs.items():
            self._index_doc(doc_id, data)

    @property
    def _path(self) -> str:
        if os.path.isabs(self.schema.storage_path):
            return self.schema.storage_path
        return os.path.join(self.chain_dir, self.schema.storage_path)

    # ── Persistencia ──

    def _read_gz(self, path: str) -> str:
        """Lee archivo .gz con fallback manual si gzip.decompress falla."""
        raw_bytes = open(path, "rb").read()
        try:
            return gzip.decompress(raw_bytes).decode()
        except Exception:
            pass
        # Fallback: skip 10-byte gzip header, decompress raw deflate
        try:
            import zlib
            return zlib.decompress(raw_bytes[10:], -zlib.MAX_WBITS).decode()
        except Exception:
            pass
        # Ultimo fallback: leer como JSON plano
        try:
            return raw_bytes.decode("utf-8")
        except Exception:
            raise

    def _load(self) -> None:
        # E3: si ya cargado, verificar si otro proceso escribio (stale
        # detection via mtime). Si el archivo cambio, recargar para no
        # leer datos viejos. Las escrituras son atomicas (tmp+replace)
        # asi que el archivo nunca esta corrupto a medio escribir.
        if self._loaded:
            path = self._path
            if os.path.exists(path):
                current_mtime = os.path.getmtime(path)
                if current_mtime > self._file_mtime:
                    self._loaded = False  # forzar recarga
            return
        if not self._loaded:
            self._do_load()

    def _do_load(self) -> None:
        """Carga real del indice desde disco (sin check de stale)."""
        path = self._path
        if os.path.exists(path):
            try:
                if path.endswith(".gz"):
                    raw = self._read_gz(path)
                else:
                    raw = open(path).read()
                data = json.loads(raw)
                docs = data.get("nodes", data.get("docs", data.get("hits", [])))
                if isinstance(docs, list):
                    self._docs = {
                        d.get("id", d.get("_id", f"doc-{i}")): d
                        for i, d in enumerate(docs)
                    }
                elif isinstance(docs, dict):
                    self._docs = docs
                # Restaurar nodos docs en el grafo (con atributos)
                for doc_id, doc_data in self._docs.items():
                    self._graph.add_node(doc_id, **doc_data)
                # Restaurar edges en el grafo
                for edge in data.get("edges", []):
                    u = edge["from"]
                    v = edge["to"]
                    attrs = {k: v for k, v in edge.items() if k not in ("from", "to")}
                    self._graph.add_edge(u, v, **attrs)
                # Restaurar token index
                ti_data = data.get("token_index")
                if ti_data and self._token_index is not None:
                    self._token_index = TokenIndex.from_dict(ti_data)

                # Reconstruir indices secundarios (type + field) para
                # busqueda O(1) sin iterar todos los docs.
                # E6: cargar indices secundarios desde disco si existen.
                # Si no (legacy), reconstruir desde _docs (backward-compat).
                ti_raw = data.get("_type_index")
                fi_raw = data.get("_field_index")
                if ti_raw is not None and fi_raw is not None:
                    self._type_index = {k: set(v) for k, v in ti_raw.items()}
                    self._field_index = {}
                    for k, v in fi_raw.items():
                        field, val = k.split("\x00", 1)
                        self._field_index[(field, val)] = set(v)
                else:
                    self._rebuild_secondary_indexes()

                # G3: schema migration — si los campos fulltext cambiaron
                # desde que se guardo el indice, el TokenIndex esta stale
                # (faltan/sobran tokens). Reconstruir inline (sin llamar a
                # rebuild_token_index() que llama a _load() → recursion).
                stored_hash = data.get("schema_hash", "")
                current_hash = self._schema_hash
                if stored_hash != current_hash and self._token_index is not None:
                    logger = __import__("logging").getLogger(__name__)
                    logger.warning(
                        "CognitiveGraph: schema del indice '%s' cambio "
                        "(hash %s -> %s). Reconstruyendo TokenIndex...",
                        self.name,
                        stored_hash[:8],
                        current_hash[:8],
                    )
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
                    # Persistir inmediatamente para que el schema_hash
                    # quede guardado y no se re-migre en cada _load()
                    self._save()
                    logger.info(
                        "CognitiveGraph: TokenIndex de '%s' reconstruido "
                        "(%d docs indexados) tras migracion de schema",
                        self.name,
                        len(self._docs),
                    )
            except Exception as e:
                # G2: error no-silencioso — un indice corrupto se reporta en vez
                # de resetear a vacio silenciosamente (convention-001: nunca
                # 'except Exception: pass'). El agente debe poder diagnosticar.
                logger = __import__("logging").getLogger(__name__)
                logger.error(
                    "CognitiveGraph: indice '%s' corrupto o ilegible (%s: %s). "
                    "Se carga vacio para no romper el flujo, pero se pierde data.",
                    self.name,
                    type(e).__name__,
                    e,
                )
                self._docs = {}
                self._graph = nx.DiGraph()
                self._token_index = TokenIndex() if self.schema.has_fulltext() else None
                self._type_index.clear()
                self._field_index.clear()
        self._loaded = True
        # E3: track mtime para stale detection en multi-proceso
        if os.path.exists(self._path):
            self._file_mtime = os.path.getmtime(self._path)

    def _save(self) -> None:
        path = self._path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = {
            "index": self.name,
            "schema_hash": self._schema_hash,
            "docs": [{"_id": doc_id, **doc} for doc_id, doc in self._docs.items()],
            "edges": [
                {"from": u, "to": v, **data}
                for u, v, data in self._graph.edges(data=True)
            ],
        }
        if self._token_index is not None:
            data["token_index"] = self._token_index.to_dict()
        # E6: persistir indices secundarios para no reconstruir en _load()
        if self._type_index:
            data["_type_index"] = {k: sorted(v) for k, v in self._type_index.items()}
        if self._field_index:
            data["_field_index"] = {f"{k[0]}\x00{k[1]}": sorted(v)
                                     for k, v in self._field_index.items()}
        payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        # G1: escritura atomica (temp + rename) + lock exclusivo.
        # Si el lock ya fue adquirido por store/store_many/delete (ciclo
        # completo read-modify-write), no re-adquirir — el fd seria otro
        # y el lock no serializaria (Unix: flock por fd, no por proceso).
        if not self._write_locked:
            self._acquire_write_lock()
        try:
            tmp_path = path + ".tmp"
            if path.endswith(".gz"):
                with open(tmp_path, "wb") as f:
                    f.write(gzip.compress(payload.encode()))
                    f.flush()
                    os.fsync(f.fileno())
            else:
                with open(tmp_path, "w") as f:
                    f.write(payload)
                    f.flush()
                    os.fsync(f.fileno())
            os.replace(tmp_path, path)
            # E3: actualizar mtime tras escritura (consistencia multi-proceso)
            self._file_mtime = os.path.getmtime(path)
        finally:
            if not self._write_locked:
                self._release_write_lock()

    @staticmethod
    def _resolve_field(doc: dict, field: str):
        """Resuelve un campo top-level o anidado (ruta con puntos).

        G9: los campos fulltext/schema pueden apuntar a dicts anidados
        (ej: "metadata.task_id", "data.summary"). En vez de doc.get("a.b"),
        que no existe, navega la ruta nivel por nivel.
        """
        if not isinstance(doc, dict) or not field:
            return ""
        value = doc
        for part in field.split("."):
            if isinstance(value, dict):
                value = value.get(part, {})
            else:
                return ""
        if isinstance(value, (dict, list)):
            return value
        return value

    @property
    def _schema_hash(self) -> str:
        """Hash del schema relevante para la estructura de datos (fulltext).

        G3: cuando los campos fulltext cambian (se agregan/quitan fields
        en search.fields del config), el hash difiere del almacenado y se
        dispara una migracion automatica (rebuild del TokenIndex).
        """
        import hashlib

        ft = sorted(self.schema.fulltext_fields)
        return hashlib.md5(
            json.dumps(ft, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()[:16]

    def _acquire_write_lock(self) -> None:
        """Adquiere un lock exclusivo por indice (evita corrupcion concurrente).

        Usa un archivo .lock separado del indice. Best-effort: si el sistema
        no soporta fcntl (Windows) o falla el lock, se continua sin el —
        la escritura atomica (temp+rename) sigue protegiendo la integridad.
        """
        import fcntl

        lock_path = self._path + ".lock"
        try:
            os.makedirs(os.path.dirname(lock_path), exist_ok=True)
            self._lock_fd = open(lock_path, "a")
            fcntl.flock(self._lock_fd.fileno(), fcntl.LOCK_EX)
        except Exception:
            self._lock_fd = None

    def _release_write_lock(self) -> None:
        """Libera el lock de escritura (si se adquirio)."""
        import fcntl

        fd = getattr(self, "_lock_fd", None)
        if fd is None:
            return
        try:
            fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
            fd.close()
        except Exception:
            pass
        self._lock_fd = None

    # ── CRUD ──

    def store(self, doc_id: str, data: dict) -> dict:
        """Indexa o actualiza un nodo con lock de ciclo completo.

        Result pattern: el core lockeado convierte excepciones en Failure.
        """
        self._acquire_write_lock()
        self._write_locked = True
        try:
            result = self._store_core(doc_id, data)
            if is_successful(result):
                return result.value_or({"ok": False, "_id": doc_id})
            return {"ok": False, "_id": doc_id, "error": str(result.failure())}
        finally:
            self._write_locked = False
            self._release_write_lock()

    @safe
    def _store_core(self, doc_id: str, data: dict) -> dict:
        """Core puro del store (puede lanzar)."""
        self._load()
        old = self._docs.get(doc_id)
        changed = old != data if old is not None else True
        if old is not None:
            self._deindex_doc(doc_id)
        self._index_doc(doc_id, data)
        if changed and self._token_index is not None and old is not None:
            self._token_index.remove_node(doc_id)
        self._docs[doc_id] = data
        self._graph.add_node(doc_id, **data)
        if changed and self._token_index is not None:
            for field in self.schema.fulltext_fields:
                value = self._resolve_field(data, field)
                if isinstance(value, str) and value:
                    self._token_index.index_node(doc_id, value, field)
                elif isinstance(value, list):
                    for item in value:
                        if isinstance(item, str):
                            self._token_index.index_node(doc_id, item, field)
        self._save()
        return {"ok": True, "_id": doc_id, "_index": self.name}

    def store_many(self, docs: dict[str, dict]) -> dict:
        """Indexa muchos nodos en batch con lock de ciclo completo.

        Result pattern: el core lockeado convierte excepciones en Failure.
        """
        self._acquire_write_lock()
        self._write_locked = True
        try:
            result = self._store_many_core(docs)
            if is_successful(result):
                return result.value_or({"ok": False, "stored": 0})
            return {"ok": False, "stored": 0, "error": str(result.failure())}
        finally:
            self._write_locked = False
            self._release_write_lock()

    @safe
    def _store_many_core(self, docs: dict[str, dict]) -> dict:
        """Core puro de store_many (puede lanzar)."""
        self._load()
        skipped = 0
        touched = 0
        for doc_id, data in docs.items():
            old = self._docs.get(doc_id)
            if old == data:
                skipped += 1
                continue
            touched += 1
            if old is not None:
                self._deindex_doc(doc_id)
            self._index_doc(doc_id, data)
            if old is not None and self._token_index is not None:
                self._token_index.remove_node(doc_id)
            self._docs[doc_id] = data
            self._graph.add_node(doc_id, **data)
            if self._token_index is not None:
                for field in self.schema.fulltext_fields:
                    value = self._resolve_field(data, field)
                    if isinstance(value, str) and value:
                        self._token_index.index_node(doc_id, value, field)
                    elif isinstance(value, list):
                        for item in value:
                            if isinstance(item, str):
                                self._token_index.index_node(doc_id, item, field)
        if skipped:
            logger = __import__("logging").getLogger(__name__)
            logger.debug(
                "store_many('%s'): %d docs skipped, %d touched",
                self.name, skipped, touched,
            )
        self._save()
        return {"ok": True, "stored": len(docs), "_index": self.name,
                "skipped": skipped, "touched": touched}

    def get(self, doc_id: str) -> dict | None:
        self._load()
        doc = self._docs.get(doc_id)
        if doc is None:
            return None
        return {"_id": doc_id, "_index": self.name, "_source": doc}

    def keys(self) -> list[str]:
        """Devuelve TODOS los IDs del indice (sin limites de search).

        Primitiva de infra para casos que necesitan inventario completo
        (ej: generar IDs secuenciales sin colision, backup, auditorias).
        Reemplaza el workaround de parsear el storage (.json.gz) a mano.
        """
        self._load()
        return list(self._docs.keys())

    def max_key(self, prefix: str = "") -> tuple[str, int] | None:
        """Devuelve (id, numero) del ID con mayor numero para un prefijo.

        Ej: prefix="decision" sobre IDs "decision-012" -> ("decision-012", 12).
        Los IDs que no matchean el prefijo o no terminan en numero se ignoran.
        """
        self._load()
        best_id: str | None = None
        best_num = -1
        for doc_id in self._docs:
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


    def delete(self, doc_id: str) -> dict:
        """Elimina un nodo con lock de ciclo completo (Result pattern)."""
        self._acquire_write_lock()
        self._write_locked = True
        try:
            result = self._delete_core(doc_id)
            if is_successful(result):
                return result.value_or({"ok": False, "deleted": False})
            return {"ok": False, "deleted": False, "error": str(result.failure())}
        finally:
            self._write_locked = False
            self._release_write_lock()

    @safe
    def _delete_core(self, doc_id: str) -> dict:
        """Core puro de delete (puede lanzar)."""
        self._load()
        existed = doc_id in self._docs
        if existed:
            self._deindex_doc(doc_id)
        self._docs.pop(doc_id, None)
        if doc_id in self._graph:
            self._graph.remove_node(doc_id)
        if self._token_index is not None:
            self._token_index.remove_node(doc_id)
        if existed:
            self._save()
        return {"ok": True, "deleted": existed, "_id": doc_id, "_index": self.name}

    def delete_many(self, doc_ids: set[str]) -> dict:
        """Elimina varios nodos en batch (1 lock + 1 save)."""
        if not doc_ids:
            return {"ok": True, "deleted": 0}
        self._acquire_write_lock()
        self._write_locked = True
        try:
            self._load()
            deleted = 0
            for doc_id in doc_ids:
                if doc_id in self._docs:
                    self._deindex_doc(doc_id)
                    self._docs.pop(doc_id)
                    if doc_id in self._graph:
                        self._graph.remove_node(doc_id)
                    if self._token_index is not None:
                        self._token_index.remove_node(doc_id)
                    deleted += 1
            if deleted:
                self._save()
            return {"ok": True, "deleted": deleted, "_index": self.name}
        finally:
            self._write_locked = False
            self._release_write_lock()

    # ── Edges ──

    def add_edge(self, from_id: str, to_id: str, **attrs) -> dict:
        """Agrega un edge entre dos nodos.

        Args:
            from_id: ID del nodo origen
            to_id: ID del nodo destino
            **attrs: atributos del edge (type, weight, etc.)

        Returns:
            dict con resultado
        """
        self._load()
        self._graph.add_edge(from_id, to_id, **attrs)
        self._save()
        return {"ok": True, "from": from_id, "to": to_id, "attrs": attrs}

    def remove_edge(self, from_id: str, to_id: str) -> dict:
        """Elimina un edge."""
        self._load()
        if self._graph.has_edge(from_id, to_id):
            self._graph.remove_edge(from_id, to_id)
            self._save()
        return {"ok": True, "from": from_id, "to": to_id}

    def get_edges(self, node_id: str, direction: str = "both") -> list[dict]:
        """Retorna todos los edges de un nodo.

        Args:
            node_id: ID del nodo
            direction: "out" (salientes), "in" (entrantes), "both"

        Returns:
            Lista de edges: {"from": ..., "to": ..., "type": ..., ...}
        """
        self._load()
        edges = []
        if direction in ("out", "both"):
            for _, v, data in self._graph.out_edges(node_id, data=True):
                edges.append({"from": node_id, "to": v, **data})
        if direction in ("in", "both"):
            for u, _, data in self._graph.in_edges(node_id, data=True):
                edges.append({"from": u, "to": node_id, **data})
        return edges

    def get_related(self, node_id: str, depth: int = 1) -> dict:
        """Obtiene el subgrafo de contexto alrededor de un nodo.

        Args:
            node_id: Nodo central
            depth: Profundidad de navegacion (0 = solo el nodo)

        Returns:
            dict con nodos y edges del subgrafo
        """
        self._load()
        if node_id not in self._graph:
            return {"center": node_id, "nodes": [], "edges": []}

        visited: set[str] = {node_id}
        current: set[str] = {node_id}
        for _ in range(depth):
            neighbors: set[str] = set()
            for n in current:
                for _, v in self._graph.out_edges(n):
                    if v not in visited:
                        neighbors.add(v)
                for u, _ in self._graph.in_edges(n):
                    if u not in visited:
                        neighbors.add(u)
            visited.update(neighbors)
            current = neighbors

        nodes = []
        for n in visited:
            data = dict(self._graph.nodes.get(n, {}))
            data["_id"] = n
            if ":" in n:
                data["_index"] = n.split(":")[0]
            nodes.append(data)

        edges = []
        for u, v, data in self._graph.edges(data=True):
            if u in visited and v in visited:
                edges.append({"from": u, "to": v, **data})

        return {"center": node_id, "nodes": nodes, "edges": edges, "total": len(nodes)}

    # ── Search ──

    def search(self, query: Any) -> dict:
        """Busca en el indice.

        Soporta:
          - structured: {"type": "refactor", "file": "src/auth.py"}
          - fulltext:   {"text": "autenticacion"}
          - combinado:  {"text": "auth", "type": "task"}

        Si el indice tiene search_handler (CPG legacy), delega en el.
        Si no, busca nativamente usando TokenIndex + grafo.
        """
        if self._search_handler:
            return self._search_handler(query)
        self._load()
        took = time.time()
        filters = query if isinstance(query, dict) else {}

        # Metacampos de control: "max_results"/"size"/"_from" NO son filtros
        # de datos (el filtro estructurado los compararia contra cada doc y
        # daria 0 hits). Extraerlos antes de filtrar y aplicarlos al slice.
        max_results = (
            filters.get("max_results")
            or filters.get("size")
            or filters.get("max_events")
        )
        try:
            max_results = int(max_results) if max_results else 50
        except (TypeError, ValueError):
            max_results = 50

        # Paginacion real (G8): offset _from/from para recorrer resultados
        # sin depender del slice hardcodeado. total reporta TODOS los matches.
        _from_raw = filters.get("_from") or filters.get("from") or 0
        try:
            _from = max(int(_from_raw), 0)
        except (TypeError, ValueError):
            _from = 0

        # Extraer query de texto (E1: soporta field-scoped search)
        #   {"text": "auth"}                          → todos los campos
        #   {"text": {"query": "auth", "fields": ["rationale"]}} → solo rationale
        text_query = ""
        text_fields: list[str] | None = None
        remaining = dict(filters)
        for _mc in (
            "max_results", "size", "max_events", "_index", "query",
            "_from", "from", "text_fields",
        ):
            remaining.pop(_mc, None)
        if "text" in remaining:
            raw = remaining.pop("text", "")
            if isinstance(raw, dict):
                text_query = raw.get("query", "")
                text_fields = raw.get("fields")
            else:
                text_query = str(raw)
        # text_fields tambien puede venir como clave separada
        tf_raw = filters.get("text_fields")
        if tf_raw and text_fields is None:
            text_fields = tf_raw if isinstance(tf_raw, list) else [tf_raw]

        # 1. Busqueda full-text via TokenIndex
        text_matches: set[str] = set()
        if text_query and self._token_index is not None:
            for entry in self._token_index.search(
                text_query, max_results=max_results, fields=text_fields,
            ):
                text_matches.add(entry.node_id)

        # 2. Filtro estructurado usando indices secundarios O(1) en vez
        #    de iterar todos los docs O(n). Cada filtro resuelto por
        #    indice reduce el conjunto de candidatos (interseccion).
        candidates: set[str] | None = None  # None = potencialmente todos
        unresolved: dict = {}               # filtros no resueltos por indice

        for qfield, qvalue in remaining.items():
            rfield = self.schema.resolve_field(qfield)
            if rfield == "_id" or qfield == "_id":
                # Primary key: O(1) directo, no necesita indices
                doc = self._docs.get(qvalue)
                if doc and (not text_query or qvalue in text_matches):
                    entry = {"_id": qvalue, "_index": self.name,
                             "_score": 1.0, "_source": doc}
                    results = [entry]
                else:
                    results = []
                # Shortcut: si pidieron _id, no hay mas filtros que aplicar
                took = int((time.time() - took) * 1000)
                return {"took": took, "index": self.name,
                        "hits": {"total": len(results), "hits": results,
                                 "from": 0, "size": 1},
                        "schema": self._schema_info()}

            qv_lower = str(qvalue).lower() if isinstance(qvalue, str) else qvalue

            if rfield == "type":
                idx_set = self._type_index.get(qvalue)
            elif not isinstance(qvalue, (dict, list)):
                idx_set = self._field_index.get((rfield, qv_lower))
            else:
                idx_set = None  # operadores ($contains, etc.) no indexados

            if idx_set is not None:
                if candidates is None:
                    candidates = set(idx_set)
                else:
                    candidates &= idx_set
            else:
                # Este filtro no se pudo resolver por indice — aplicar con _match
                unresolved[qfield] = qvalue

        # Si ningun filtro se resolvio por indice, escanear todos los docs
        if candidates is None:
            candidates = set(self._docs.keys())

        # Si hay text_query, intersectar con matches fulltext
        if text_query:
            candidates &= text_matches
            if not unresolved:
                # Sin filtros adicionales: todos los candidates son hits
                unresolved = None

        results = []
        for doc_id in candidates:
            doc = self._docs.get(doc_id)
            if doc is None:
                continue
            # Filtros no resueltos: check con _match
            if unresolved and not self._match(doc, unresolved):
                continue
            # Obtener vecinos del grafo para contexto
            related = self._get_neighbor_ids(doc_id, depth=1) if text_query else []
            entry = {
                "_id": doc_id,
                "_index": self.name,
                "_score": 1.0,
                "_source": doc,
            }
            if related:
                entry["_related"] = related
            results.append(entry)

        took = int((time.time() - took) * 1000)
        return {
            "took": took,
            "index": self.name,
            "hits": {
                "total": len(results),
                "hits": results[_from : _from + max_results],
                "from": _from,
                "size": max_results,
            },
            "schema": self._schema_info(),
        }

    def _get_neighbor_ids(self, node_id: str, depth: int = 1) -> list[dict]:
        """Obtiene IDs de vecinos para incluir como contexto en resultados."""
        neighbors = []
        if node_id not in self._graph:
            return neighbors
        for _, v, data in self._graph.out_edges(node_id, data=True):
            vdata = self._graph.nodes.get(v, {})
            neighbors.append({
                "id": v,
                "rel": data.get("type", "related"),
                "label": vdata.get("objective") or vdata.get("name") or v,
            })
        for u, _, data in self._graph.in_edges(node_id, data=True):
            udata = self._graph.nodes.get(u, {})
            neighbors.append({
                "id": u,
                "rel": data.get("type", "related") + " (incoming)",
                "label": udata.get("objective") or udata.get("name") or u,
            })
        return neighbors

    def _match(self, doc: dict, filters: dict) -> bool:
        """Verifica si un documento coincide con los filtros estructurados.

        RESOLUCIÓN DE CAMPOS: si el valor top-level no matchea, intenta
        con metadata.<campo>. Esto cubre datos legados donde el mismo
        campo (ej. scope) vive en top-level en unos docs y en metadata
        en otros — sin esta normalización, esas convenciones/decisiones
        son inconsultables por filtro estructurado.
        """
        if not filters:
            return True
        for qfield, qvalue in filters.items():
            rfield = self.schema.resolve_field(qfield)
            if rfield == "type":
                if doc.get("type") != qvalue:
                    return False
            elif "." in rfield:
                parts = rfield.split(".")
                val = doc
                for p in parts:
                    val = val.get(p, {}) if isinstance(val, dict) else {}
                if not self._val_contains(val, qvalue):
                    return False
            else:
                doc_val = doc.get(rfield, "")
                if not self._val_contains(doc_val, qvalue):
                    # Fallback: campo legado viviendo en metadata
                    meta_val = (doc.get("metadata") or {}).get(rfield)
                    if not self._val_contains(meta_val, qvalue):
                        return False
        return True

    @staticmethod
    def _val_contains(val, qvalue) -> bool:
        """¿El valor matchea el query?

        SEMÁNTICA (G7): match EXACTO por defecto (case-insensitive) para
        filtros estructurados — {"status": "active"} NO debe matchear
        "inactive" ni "active-pending". Para substring explícito se usan
        operadores de query:
          - {"$contains": "auth"} → substring case-insensitive
          - {"$prefix": "src/"}   → prefijo case-insensitive
          - {"$eq": "auth"}       → exacto case-insensitive (default)
        El fulltext ({"text": ...}) sigue cubriendo substring por tokens.
        """
        # Operadores de query
        if isinstance(qvalue, dict):
            if "$contains" in qvalue:
                needle = str(qvalue["$contains"]).lower()
                return Index._val_contains(val, {"$substr": needle})
            if "$prefix" in qvalue:
                needle = str(qvalue["$prefix"]).lower()
                if isinstance(val, str):
                    return val.lower().startswith(needle)
                if isinstance(val, list):
                    return any(
                        str(item).lower().startswith(needle) for item in val
                    )
                return False
            if "$eq" in qvalue:
                return Index._val_contains(val, qvalue["$eq"])
            # Operador interno $substr (substring case-insensitive)
            if "$substr" in qvalue:
                needle = str(qvalue["$substr"]).lower()
                if isinstance(val, str):
                    return needle in val.lower()
                if isinstance(val, list):
                    return any(needle in str(item).lower() for item in val)
                return False
            # Dict desconocido: comparar como exacto
            return val == qvalue

        if val is None:
            return False
        if isinstance(val, str):
            # Exacto case-insensitive (G7)
            return val.lower() == str(qvalue).lower()
        if isinstance(val, list):
            return any(
                Index._val_contains(item, qvalue)
                for item in val
            )
        return val == qvalue

    def schema_info(self) -> dict:
        self._load()
        return self._schema_info()

    def _schema_info(self) -> dict:
        types = {}
        for doc in self._docs.values():
            t = doc.get("type", "unknown")
            types[t] = types.get(t, 0) + 1
        return {
            "fields": self.schema.fields,
            "types": types,
            "total_docs": len(self._docs),
            "total_edges": self._graph.number_of_edges(),
            "description": self.schema.description,
        }

    def metrics(self) -> dict:
        self._load()
        path = self._path
        size = os.path.getsize(path) if os.path.exists(path) else 0
        return {
            "total_docs": len(self._docs),
            "total_edges": self._graph.number_of_edges(),
            "storage_size": f"{size / 1024:.1f}KB",
            "storage_path": path,
            "last_updated": time.ctime(os.path.getmtime(path))
            if os.path.exists(path)
            else "never",
        }

    def rebuild_token_index(self) -> None:
        """Limpia y reconstruye el TokenIndex desde los docs actuales."""
        if self._token_index is None:
            return
        self._load()
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
        self._save()


