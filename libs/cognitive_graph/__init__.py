"""CognitiveGraph — Capa de infraestructura tipo ODM para memorias del walker.

Cada memoria (CPG, CKG, Delta, Trace) se registra como un indice.
CognitiveGraph unifica: search (textual + estructurada), store, get, delete,
schema, metrics, y navegacion de relaciones entre nodos del grafo.

Almacenamiento: cada indice es un nx.DiGraph (nodos + edges) con un
TokenIndex opcional para busqueda full-text sobre campos especificos.

Uso:
    cg = CognitiveGraph(chain_dir)
    cg.search("ckg", {"type": "refactor"})
    cg.search("delta", {"text": "autenticacion"})
    cg.search_many({"delta": {"text": "auth"}, "cpg": {"file": "src/auth.py"}})
    cg.store("delta", "TASK-042", {"type": "task", "objective": "Fix auth bug", "files": ["src/auth.py"]})
    cg.add_edge("delta:TASK-042", "cpg:src/auth.py", "impacts_file")
    cg.get_related("delta:TASK-042")  # navega edges desde un nodo
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
        self._write_locked = False  # guarda reentrada de _save dentro de _load

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
        if self._loaded:
            return
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

        # Extraer query de texto
        text_query = ""
        remaining = dict(filters)
        for _mc in (
            "max_results", "size", "max_events", "_index", "query",
            "_from", "from",
        ):
            remaining.pop(_mc, None)
        if "text" in remaining:
            text_query = remaining.pop("text", "")

        # 1. Busqueda full-text via TokenIndex
        text_matches: set[str] = set()
        if text_query and self._token_index is not None:
            for entry in self._token_index.search(text_query, max_results=max_results):
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


# ═══════════════════════════════════════════════════════════════════
#  CognitiveGraph — Facade
# ═══════════════════════════════════════════════════════════════════

class CognitiveGraph:
    """Facade unificada. Los dominios registran sus indices aqui."""

    def __init__(self, chain_dir: str, auto_register: bool = True):
        self.chain_dir = chain_dir
        self._indexes: dict[str, Index] = {}
        if auto_register:
            self.register_defaults()

    def register_defaults(self) -> None:
        """Registra los 4 indices por defecto: ckg, cpg, delta, trace.

        CKG usa storage interno nativo (con CognitiveGraph.store()).
        CPG usa search_handler que lee CpgFileRepository (legacy).
        Delta usa storage interno nativo + fulltext search.
        Trace usa storage interno nativo.
        """
        self._register_ckg_index()
        self._register_cpg_index()
        self._register_ref_indexes()
        self._register_delta_index()
        self._register_trace_index()

    def _register_ckg_index(self) -> None:
        """Registra el indice CKG (Knowledge Graph)."""
        self.register_index(
            "ckg",
            {
                "fields": {
                    "type": "type",
                    "scope": "scope",
                    "label": "statement",
                    "task": "metadata.task_id",
                    "description": "rationale",
                    "tag": "tags",
                },
                "types": {
                    "decision": {"description": "Decision de arquitectura"},
                    "refactor": {"description": "Refactorizacion aplicada"},
                    "convention": {"description": "Convencion de estilo"},
                },
                "storage": {"path": "data/ckg.json.gz", "format": "json.gz"},
                "description": "CKG: Knowledge Graph - decisiones, convenciones, patrones",
            },
        )

    def _register_cpg_index(self) -> None:
        """Registra el indice CPG con search_handler que lee CpgFileRepository."""
        self.register_index(
            "cpg",
            {
                "fields": {"function": "name", "file": "path", "type": "type"},
                "types": {"function": {}, "class": {}, "file": {}, "module": {}},
                "storage": {"path": "data/cpg.json.gz", "format": "json.gz"},
                "description": "CPG: Code Property Graph - estructura del codigo fuente",
            },
            search_handler=self._make_cpg_search_handler(),
        )

    def _make_cpg_search_handler(self):
        """Crea el handler de busqueda del indice CPG (legacy CpgFileRepository)."""
        from features.code_analysis.infrastructure.repository import CpgFileRepository

        def _cpg_search(query):
            repo = CpgFileRepository(os.path.join(self.chain_dir, "data"))
            g = repo.load_graph()
            results = []
            filters = query or {}
            # Metacampos de control (max_results/size): no son filtros de datos.
            max_results = (
                filters.get("max_results")
                or filters.get("size")
                or filters.get("max_events")
            )
            try:
                max_results = int(max_results) if max_results else 50
            except (TypeError, ValueError):
                max_results = 50
            skip_keys = {"max_results", "size", "max_events", "_index", "query", "text"}
            for nid, node in g.nodes.items():
                match = True
                for k, v in filters.items():
                    if k in skip_keys:
                        continue
                    if k == "function" and v not in node.name:
                        match = False
                    elif k == "file" and v not in node.path:
                        match = False
                    elif k == "type" and node.type != v:
                        match = False
                    elif k == "signature" and v not in (node.signature or ""):
                        match = False
                    elif k == "kind" and v not in (node.kind or ""):
                        match = False
                if match:
                    results.append(
                        {
                            "_id": nid,
                            "_source": {
                                "name": node.name,
                                "type": node.type,
                                "file": node.path,
                                "line": node.line,
                                "signature": node.signature,
                                "kind": node.kind,
                            },
                        }
                    )
            return {
                "took": 0,
                "index": "cpg",
                "hits": {"total": len(results), "hits": results[:max_results]},
                "schema": {},
            }

        return _cpg_search

    def _register_delta_index(self) -> None:
        """Registra el indice Delta (tareas y sprints) con fulltext search."""
        self.register_index(
            "delta",
            {
                "fields": {"task": "task_id", "domain": "domain", "type": "type"},
                "types": {"task": {}, "sprint": {}},
                "storage": {"path": "data/delta.json.gz", "format": "json.gz"},
                "description": "DeltaGraph: tareas y sprints del proyecto",
                "search": {
                    "type": "fulltext",
                    "fields": ["objective", "title", "description", "domain", "result_state"],
                },
            },
        )

    def _register_trace_index(self) -> None:
        """Registra el indice Trace (memoria de ejecucion)."""
        self.register_index(
            "trace",
            {
                "fields": {
                    "task": "task_id",
                    "tool": "tool",
                    "type": "event_type",
                    "agent": "agent_id",
                    "session": "session_id",
                    "sprint": "sprint_id",
                    "node": "node",
                    "sub_state": "sub_state",
                    "source": "source",
                    "file": "files",
                },
                "types": {
                    "workflow_state": {},
                    "task_execute": {},
                    "agent_message": {},
                    "tool_call": {},
                    "file_impact": {},
                    "kg_query": {},
                },
                "storage": {"path": "data/trace.json.gz", "format": "json.gz"},
                "description": "TraceGraph: memoria de ejecucion (workflow + agente + tools)",
            },
            search_handler=self._make_trace_search_handler(),
        )

    def _make_trace_search_handler(self):
        """Crea el handler de busqueda del indice Trace (nativo + NDJSON legacy)."""

        def _trace_search(query):
            """Busca en trace: storage nativo (trace.json.gz) + NDJSON legacy (data/traces/)."""
            filters = query or {}
            events, seen_ids = self._collect_trace_events()
            text_query, remaining, max_results = self._prepare_trace_filters(filters)
            results = self._filter_trace_events(events, text_query, remaining)
            if max_results:
                results = results[: int(max_results)]

            return {
                "took": 0,
                "index": "trace",
                "hits": {
                    "total": len(results),
                    "hits": results[: max_results or 50],
                },
                "schema": {
                    "fields": {
                        "task": "task_id", "tool": "tool",
                        "type": "event_type", "agent": "agent_id",
                        "session": "session_id", "sprint": "sprint_id",
                        "node": "node", "sub_state": "sub_state",
                        "source": "source", "file": "files",
                    },
                    "types": {
                        "workflow_state": {}, "task_execute": {},
                        "agent_message": {}, "tool_call": {},
                        "file_impact": {}, "kg_query": {},
                    },
                    "description": "TraceGraph: memoria de ejecucion (workflow + agente + tools)",
                },
            }

        return _trace_search

    def _collect_trace_events(self) -> tuple[list[dict], set[str]]:
        """Recolecta eventos de trace desde: storage nativo, NDJSON legacy
        (data/traces/) y storage del proyecto real (descentralizado).

        Dedupe global por _id: el mismo evento puede vivir en el storage
        nativo Y en el registry global — cuando chain_dir == project_root,
        ambas fuentes apuntan al MISMO archivo.
        """
        import gzip as _gzip

        events = []
        seen_ids: set[str] = set()

        # 1. Storage nativo (trace.json.gz via Index._load)
        idx = self._get_index("trace")
        idx._load()
        for doc_id, doc in idx._docs.items():
            if doc_id and doc_id in seen_ids:
                continue
            if doc_id:
                seen_ids.add(doc_id)
            events.append({"_id": doc_id, "_source": doc})

        # 2. NDJSON legacy en data/traces/*.json.gz
        self._collect_ndjson_traces(events, seen_ids)

        # 3. Storage nativo del proyecto real (descentralizado)
        self._collect_project_native_trace(events, seen_ids)

        return events, seen_ids

    def _collect_ndjson_traces(self, events: list, seen_ids: set) -> None:
        """Recolecta eventos NDJSON legacy de data/traces/*.json.gz."""
        import gzip as _gzip

        traces_dirs = [os.path.join(self.chain_dir, "data", "traces")]
        try:
            from libs.path_resolver import get_project_dir
            project_id = os.path.basename(self.chain_dir)
            proj_dir = get_project_dir(project_id)
            if proj_dir:
                traces_dirs.append(str(proj_dir / "data" / "traces"))
        except Exception:
            pass

        seen_events = set()
        for traces_dir in traces_dirs:
            if not os.path.isdir(traces_dir):
                continue
            for fname in sorted(os.listdir(traces_dir)):
                if not fname.endswith(".gz"):
                    continue
                try:
                    with _gzip.open(os.path.join(traces_dir, fname), "rt") as f:
                        content = f.read()
                    for line in content.strip().split("\n"):
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            doc = json.loads(line)
                        except Exception:
                            continue
                        if not isinstance(doc, dict):
                            continue
                        ndjson_id = doc.get("id", doc.get("_id", ""))
                        if ndjson_id and ndjson_id in seen_ids:
                            continue
                        if ndjson_id:
                            seen_ids.add(ndjson_id)
                        dedupe_key = (
                            doc.get("timestamp", ""),
                            doc.get("event_type", ""),
                            doc.get("tool", ""),
                        )
                        if dedupe_key in seen_events:
                            continue
                        seen_events.add(dedupe_key)
                        events.append({"_id": doc.get("id", ""), "_source": doc})
                except Exception:
                    continue

    def _collect_project_native_trace(self, events: list, seen_ids: set) -> None:
        """Recolecta eventos del storage nativo del proyecto real (descentralizado).

        La fuente de verdad del trace es <repo_path>/.cognitive/projects/
        {pid}/data/trace.json.gz, donde repo_path es la referencia que el
        walker central mantiene en data/projects.json. get_project_dir()
        resuelve esa ruta (registry → repo_path), con fallback al .cognitive
        global del walker si el proyecto no tiene repo registrado.
        """
        import gzip as _gzip

        try:
            from libs.path_resolver import get_project_dir
            project_id = os.path.basename(self.chain_dir)
            proj_dir = get_project_dir(project_id)
            if proj_dir:
                idx_path = proj_dir / "data" / "trace.json.gz"
                if idx_path.is_file():
                    try:
                        with _gzip.open(idx_path, "rt") as f:
                            raw_idx = json.load(f)
                        docs_idx = raw_idx.get("docs", []) if isinstance(raw_idx, dict) else raw_idx
                        for doc in docs_idx:
                            if isinstance(doc, dict):
                                doc_id = doc.get("_id", "")
                                if doc_id and doc_id in seen_ids:
                                    continue
                                if doc_id:
                                    seen_ids.add(doc_id)
                                events.append({"_id": doc_id, "_source": doc})
                    except Exception:
                        pass
        except Exception:
            pass

        return events, seen_ids

    def _prepare_trace_filters(
        self, filters: dict
    ) -> tuple[str, dict, int | None]:
        """Separa text_query / metacampos de control / filtros estructurados."""
        text_query = ""
        remaining = dict(filters)
        if "text" in remaining:
            text_query = remaining.pop("text", "").lower()
        # Metacampos de control: NO son filtros de datos. El searcher soporta
        # paginación/limites via "max_results"/"size"/"max_events"; si quedan
        # en remaining, el filtro estructurado los compara contra cada doc y
        # devuelve 0 hits (ningún doc tiene esos campos). Extraerlos antes.
        max_results = (
            remaining.pop("max_results", None)
            or remaining.pop("size", None)
            or remaining.pop("max_events", None)
        )
        # "_index" es un selector de índice del protocolo CLI, no un filtro.
        remaining.pop("_index", None)
        # "query" es el wrapper ES-like {"match_all": {}} (o full-text):
        # si su valor es un dict (ES semantics) no es un campo de datos.
        es_query = remaining.pop("query", None)
        if isinstance(es_query, dict) and text_query is None:
            # {"match_all": {}} → sin filtro de texto
            _ = es_query.get("match_all") if isinstance(es_query, dict) else None
        return text_query, remaining, max_results

    def _filter_trace_events(
        self, events: list[dict], text_query: str, remaining: dict
    ) -> list[dict]:
        """Aplica filtros de texto y estructurados sobre los eventos."""
        results = []
        for item in events:
            doc = item.get("_source", {})
            match = True
            if text_query:
                haystack = " ".join(str(v) for v in doc.values()).lower()
                if text_query not in haystack:
                    match = False
            if match and remaining:
                for k, v in remaining.items():
                    # Mapear aliases del schema
                    alias_map = {
                        "task": "task_id", "tool": "tool",
                        "type": "event_type", "agent": "agent_id",
                        "session": "session_id", "sprint": "sprint_id",
                        "node": "node", "sub_state": "sub_state",
                        "source": "source", "file": "files",
                    }
                    field = alias_map.get(k, k)
                    field_val = doc.get(field, "")
                    needle = str(v)
                    if isinstance(field_val, list):
                        if not any(needle in str(item) for item in field_val):
                            match = False
                    else:
                        if needle not in str(field_val):
                            match = False
            if match:
                results.append({
                    "_id": item.get("_id", ""),
                    "_index": "trace",
                    "_score": 1.0,
                    "_source": doc,
                })
        return results

    def _register_ref_indexes(self) -> None:
        """Descubre y registra CPGs de referencia (data/cpg.ref.*.json.gz)."""
        refs_dir = os.path.join(self.chain_dir, "data")
        if not os.path.isdir(refs_dir):
            return
        for fname in os.listdir(refs_dir):
            if not fname.startswith("cpg.ref.") or not fname.endswith(".json.gz"):
                continue
            ref_name = fname.replace("cpg.ref.", "").replace(".json.gz", "")
            index_name = f"cpg-ref-{ref_name}"
            if index_name in self._indexes:
                continue

            ref_path = os.path.join(refs_dir, fname)
            ref_search = self._make_ref_search(ref_path)
            self.register_index(
                index_name,
                {
                    "fields": {"function": "name", "file": "path", "type": "type"},
                    "types": {"function": {}, "class": {}, "file": {}, "module": {}},
                    "storage": {"path": fname, "format": "json.gz"},
                    "description": f"CPG Referencia: {ref_name}",
                },
                search_handler=ref_search,
            )

    @staticmethod
    def _make_ref_search(ref_path: str):
        """Crea un search_handler para un CPG de referencia."""

        def search(query):
            import gzip
            import json

            try:
                raw = gzip.decompress(open(ref_path, "rb").read()).decode()
                data = json.loads(raw)
            except Exception:
                return {"took": 0, "hits": {"total": 0, "hits": []}, "schema": {}}

            nodes = data.get("nodes", {})
            filters = query or {}
            # Metacampos de control (max_results/size): no son filtros.
            max_results = (
                filters.get("max_results")
                or filters.get("size")
                or filters.get("max_events")
            )
            try:
                max_results = int(max_results) if max_results else 50
            except (TypeError, ValueError):
                max_results = 50
            skip_keys = {"max_results", "size", "max_events", "_index", "query", "text"}
            results = []
            for nid, n in nodes.items():
                match = True
                for k, v in filters.items():
                    if k in skip_keys:
                        continue
                    if k == "function" and v not in n.get("name", ""):
                        match = False
                    elif k == "file" and v not in n.get("path", ""):
                        match = False
                    elif k == "type" and n.get("type") != v:
                        match = False
                    elif k == "class" and v not in n.get("name", ""):
                        match = False
                if match:
                    results.append(
                        {
                            "_id": nid,
                            "_source": {
                                "name": n.get("name"),
                                "type": n.get("type"),
                                "file": n.get("path"),
                                "line": n.get("line", 0),
                                "signature": n.get("signature", ""),
                                "kind": n.get("kind", ""),
                            },
                        }
                    )
            return {
                "took": 0,
                "hits": {"total": len(results), "hits": results[:max_results]},
                "schema": {},
            }

        return search

    def register_index(
        self, name: str, config: dict, search_handler: callable | None = None
    ) -> Index:
        """Registra un indice con su schema.

        Args:
            name: nombre del indice (ckg, cpg, delta, trace)
            config: schema del indice (fields, types, storage, description, search)
            search_handler: funcion opcional para busqueda custom.
                           Si se provee, reemplaza la busqueda default.
                           Util para CPG legacy que tiene su propio backend.

        Returns:
            El indice registrado.
        """
        schema = IndexSchema(config)
        index = Index(name, schema, self.chain_dir, search_handler)
        self._indexes[name] = index
        return index

    def _get_index(self, name: str) -> Index:
        idx = self._indexes.get(name)
        if not idx:
            raise KeyError(
                f"Indice '{name}' no registrado. Disponibles: {list(self._indexes.keys())}"
            )
        return idx

    # ── Queries ──

    def search(self, index: str, query: Any = None) -> dict:
        """Consulta un indice."""
        return self._get_index(index).search(query or {})

    def search_many(self, queries: dict[str, Any]) -> dict:
        """Consulta multiples indices.

        Args:
            queries: {index_name: query_dict, ...}. Soporta metacampos de
                     control con prefijo "_" (no se envían como filtros):
                     - _top_k: límite de hits por capa (default 15)
                     - _max_chars: truncado de strings largos (default 400)
                     - _compact: bool — proyección de campos + ranking
                       (default True)

        Returns:
            {took, responses: {index_name: result, ...}} con respuestas
            compactadas: campos proyectados por índice, strings truncados,
            ordenadas por relevancia y limitadas a top_k.
        """
        took = time.time()
        control = {k: v for k, v in (queries or {}).items() if k.startswith("_")}
        layer_queries = {k: v for k, v in (queries or {}).items() if not k.startswith("_")}
        top_k = int(control.get("_top_k", 15))
        max_chars = int(control.get("_max_chars", 400))
        compact = bool(control.get("_compact", True))

        responses = {}
        for name, query in layer_queries.items():
            try:
                result = self.search(name, query)
                if compact:
                    result = self._compact_result(name, result, top_k=top_k, max_chars=max_chars)
                responses[name] = result
            except Exception as e:
                responses[name] = {"ok": False, "error": str(e), "index": name}
        return {
            "took": int((time.time() - took) * 1000),
            "responses": responses,
        }

    # ═══════════════════════════════════════════════════════════════
    # Poda de respuestas (A+B+C): proyección, ranking y presupuesto
    # ═══════════════════════════════════════════════════════════════

    # Campos relevantes por índice (proyección). El LLM recibe solo estos.
    COMPACT_FIELDS = {
        "ckg": ["_id", "type", "subject", "statement", "scope", "severity", "status"],
        "delta": ["_id", "task_id", "title", "objective", "status", "domain", "result_state"],
        "trace": [
            "_id",
            "event_type",
            "timestamp",
            "task_id",
            "session_id",
            "tool",
            "data",
        ],
        "cpg": ["_id", "name", "type", "file", "line", "signature", "kind"],
    }

    # Orden de severidad para ranking CKG (obligatoria primero).
    SEVERITY_ORDER = {"obligatoria": 0, "recomendada": 1, "informativa": 2}

    def _compact_result(self, index: str, result: dict, top_k: int, max_chars: int) -> dict:
        """Proyecta, rankea y limita los hits de una capa.

        Conserva la estructura ES-like ({took, index, hits: {total, hits},
        schema}) para no romper consumers, pero:
          A) Proyecta _source a COMPACT_FIELDS (sin rationale/metadata/files).
          B) Ordena por relevancia: CKG por severidad, trace/delta por
             timestamp reciente.
          C) Limita hits a top_k; total conserva el conteo real del índice.
        """
        hits = result.get("hits") or {}
        hit_list = hits.get("hits", []) if isinstance(hits, dict) else []
        total = hits.get("total", len(hit_list)) if isinstance(hits, dict) else len(hit_list)

        projected = []
        for h in hit_list:
            if not isinstance(h, dict):
                continue
            src = h.get("_source") or {}
            if not isinstance(src, dict):
                continue
            compact_src = self._project_source(index, src, max_chars)
            projected.append({
                "_id": h.get("_id", ""),
                "_index": h.get("_index", index),
                "_source": compact_src,
            })

        # B) Ranking por relevancia de capa
        if index == "ckg":
            projected.sort(
                key=lambda h: self.SEVERITY_ORDER.get(
                    (h["_source"].get("severity") or "").lower(), 9
                )
            )
        elif index in ("trace", "delta"):
            projected.sort(
                key=lambda h: h["_source"].get("timestamp", ""),
                reverse=True,
            )

        # C) Presupuesto
        projected = projected[:top_k]
        return {
            "took": result.get("took", 0),
            "index": index,
            "hits": {"total": total, "hits": projected},
            "schema": result.get("schema", {}),
        }

    def _project_source(self, index: str, src: dict, max_chars: int) -> dict:
        """Proyecta un documento a los campos relevantes del índice.

        Los campos de la whitelist se truncan a max_chars. El campo 'data'
        de trace se resume (index + query) para evitar traer payloads grandes.
        """
        fields = self.COMPACT_FIELDS.get(index)
        if not fields:
            # Índice sin whitelist: truncar strings largos conservando todo
            return {
                k: (v[:max_chars] + "..." if isinstance(v, str) and len(v) > max_chars else v)
                for k, v in src.items()
            }

        out = {}
        for f in fields:
            if f not in src:
                continue
            v = src[f]
            if isinstance(v, str) and len(v) > max_chars:
                v = v[:max_chars] + "..."
            elif f == "data" and isinstance(v, dict):
                # Trace: resumir payload (index + query acotada)
                q = v.get("query") or {}
                out["data"] = {
                    "index": v.get("index", ""),
                    "query": self._truncate_dict(q, max_chars=160),
                }
                continue
            out[f] = v
        return out

    @staticmethod
    def _truncate_dict(d: dict, max_chars: int) -> dict:
        return {
            k: (v[:max_chars] + "..." if isinstance(v, str) and len(v) > max_chars else v)
            for k, v in d.items()
        }

    # ── CRUD ──

    def store(self, index: str, doc_id: str, data: dict) -> dict:
        return self._get_index(index).store(doc_id, data)

    def store_many(self, index: str, docs: dict[str, dict]) -> dict:
        """Indexa muchos nodos en batch (una sola escritura)."""
        return self._get_index(index).store_many(docs)

    def get(self, index: str, doc_id: str) -> dict | None:
        return self._get_index(index).get(doc_id)

    def keys(self, index: str) -> list[str]:
        """Devuelve TODOS los IDs de un indice (sin limites de search)."""
        return self._get_index(index).keys()

    def max_key(self, index: str, prefix: str = "") -> tuple[str, int] | None:
        """Devuelve (id, numero) del ID con mayor numero para un prefijo."""
        return self._get_index(index).max_key(prefix)

    def delete(self, index: str, doc_id: str) -> dict:
        return self._get_index(index).delete(doc_id)

    def upsert(self, index: str, key: str, data: dict) -> dict:
        """Upsert por key (primitiva reusable de infraestructura).

        Distingue create de update: si el nodo ya existe, hace merge de
        campos (los existentes se preservan salvo que data los pise);
        si no existe, lo crea. Retorna {'created': bool, 'updated': bool,
        'result': ...}.

        Base para amplificaciones de dominio como upsert_spec del
        DeltaGraph (que matchea por campo de negocio ademas de key).
        """
        existing = self.get(index, key)
        if existing:
            merged = dict(existing.get("_source") or {})
            merged.update(data)
            result = self.store(index, key, merged)
            return {"created": False, "updated": True, "result": result}
        result = self.store(index, key, data)
        return {"created": True, "updated": False, "result": result}

    # ── Edges ──

    def add_edge(self, from_id: str, to_id: str, **attrs) -> dict:
        """Agrega un edge entre dos nodos.

        Busca en todos los indices cual contiene el nodo origen y
        guarda el edge ahi. Si no lo encuentra, lo guarda en delta.

        Args:
            from_id: ID del nodo origen (ej: "sprintX:TASK-042")
            to_id: ID del nodo destino (ej: "src/auth_middleware.py")
            **attrs: atributos del edge (type="impacts_file", etc.)
        """
        # Encontrar indice que contiene el nodo origen
        source_idx_name = self._find_node_index(from_id)
        if not source_idx_name:
            source_idx_name = "delta"  # fallback

        idx = self._indexes.get(source_idx_name)
        if idx is None:
            return {"ok": False, "error": f"Index '{source_idx_name}' not found",
                    "from": from_id, "to": to_id}

        result = idx.add_edge(from_id, to_id, **attrs)
        return {
            "ok": True,
            "from": from_id,
            "to": to_id,
            "attrs": attrs,
            "index": source_idx_name,
            "result": result,
        }

    def _find_node_index(self, node_id: str) -> str | None:
        """Busca en que indice esta almacenado un nodo por su ID."""
        for name, idx in self._indexes.items():
            try:
                if not idx._loaded:
                    idx._load()
                if node_id in idx._docs:
                    return name
            except Exception:
                continue
        return None

    def get_related(self, node_id: str, depth: int = 1) -> dict:
        """Obtiene el subgrafo de contexto alrededor de un nodo.

        Busca en todos los indices hasta encontrar el nodo.
        """
        source_idx_name = self._find_node_index(node_id)
        if source_idx_name:
            return self._indexes[source_idx_name].get_related(node_id, depth)

        # Si no se encuentra, buscar en todos los grafos
        for name, idx in self._indexes.items():
            if node_id in idx._graph:
                return idx.get_related(node_id, depth)
        return {"center": node_id, "nodes": [], "edges": [], "total": 0}

    # ── Schema / Metrics ──

    def schema(self, index: str) -> dict:
        return self._get_index(index).schema_info()

    def metrics(self, index: str) -> dict:
        return self._get_index(index).metrics()

    def list_indexes(self) -> list[str]:
        return list(self._indexes.keys())
