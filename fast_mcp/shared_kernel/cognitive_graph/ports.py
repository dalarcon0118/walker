"""cognitive_graph/ports.py — StorageBackend (puerto de almacenamiento).

ADR-WALKER-061 (F3 corregido): puerto de negocio MULTI-INDICE para la capa
de memorias (CPG/CKG/Delta/Trace). Es la abstracción que debe funcionar
IGUAL para archivos (.json.gz) hoy y para una BD mañana — sin reescribir
los consumidores (repos, CognitiveGraph, tasks).

Contrato (espejo del API público de CognitiveGraph):
    register_index / store / store_many / get / delete / delete_many /
    max_key / search / add_edge / remove_edge / get_edges / get_related /
    replace_blob / sync_edges / set_metadata / get_metadata / get_docs /
    get_all_edges / keys / upsert / schema_info / metrics / list_indexes /
    find_node_index

Es @runtime_checkable: un adapter (FileBackend, DbBackend) se valida con
isinstance(). CognitiveGraph implementa este mismo port y DELEGA a un
StorageBackend inyectado, de modo que el backend es el dueño real del
storage y puede sustituirse por configuración.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class StorageBackend(Protocol):
    """Puerto de almacenamiento multi-índice de memorias."""

    # ── Registro ──
    def register_index(self, name: str, schema: Any, search_handler: Any = None) -> None:
        """Registra un índice (schema + handler de búsqueda opcional)."""
        ...

    # ── CRUD doc-level ──
    def store(self, index: str, doc_id: str, data: dict) -> dict:
        """Indexa/actualiza un doc. Retorna {"ok": bool, "_id": str, ...}."""
        ...

    def store_many(self, index: str, docs: dict[str, dict]) -> dict:
        ...

    def get(self, index: str, doc_id: str) -> dict | None:
        ...

    def delete(self, index: str, doc_id: str) -> dict:
        ...

    def delete_many(self, index: str, doc_ids: set[str]) -> dict:
        ...

    def max_key(self, index: str, prefix: str = "") -> tuple[str, int] | None:
        ...

    def search(self, index: str, query: dict) -> dict:
        ...

    # ── Edges / grafo ──
    def add_edge(self, from_id: str, to_id: str, **attrs: Any) -> dict:
        """Agrega edge; resuelve el índice dueño del nodo origen."""
        ...

    def remove_edge(self, from_id: str, to_id: str) -> dict:
        ...

    def get_edges(self, node_id: str, direction: str = "both") -> list:
        ...

    def get_related(self, node_id: str, depth: int = 1) -> dict:
        ...

    # ── Blob / metadata de dominio ──
    def replace_blob(self, index: str, nodes: dict, edges: list, metadata: dict) -> dict:
        ...

    def sync_edges(self, index: str, edges: list[dict]) -> dict:
        ...

    def set_metadata(self, index: str, metadata: dict) -> dict:
        ...

    def get_metadata(self, index: str) -> dict:
        ...

    def get_metadata_only(self, index: str) -> dict:
        """Metadata SIN poblar el read model (sin docs/edges ni sha1 por fila).

        Chequeos de staleness O(ms): el load completo de un índice grande
        reconstruye grafo/índices y serializa cada fila (json.loads +
        json.dumps por doc) — prohibitivo para leer un solo valor (ej:
        source_fingerprint del CPG, invocado por el watcher en cada scan).
        """
        ...

    def get_docs(self, index: str) -> dict[str, dict]:
        ...

    def get_all_edges(self, index: str) -> list[dict]:
        ...

    def keys(self, index: str) -> list[str]:
        ...

    # ── Primitivas de infra / introspección ──
    def upsert(self, index: str, key: str, data: dict) -> dict:
        ...

    def schema_info(self, index: str) -> dict:
        ...

    def metrics(self, index: str) -> dict:
        ...

    def list_indexes(self) -> list[str]:
        ...

    def find_node_index(self, node_id: str) -> str | None:
        """Resuelve qué índice contiene un nodo (lookup cross-index)."""
        ...
