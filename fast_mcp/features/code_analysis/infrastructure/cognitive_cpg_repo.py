"""infrastructure/cognitive_cpg_repo.py — Implementa CpgRepository via StorageBackend.

ADR-WALKER-061 (F5) + ADR-WALKER-063 (CA-9): UN SOLO CAMINO de persistencia
(la facade/el puerto). Habla exclusivamente con StorageBackend (get_docs/
get_all_edges/get_metadata/replace_blob/search), igual que los repos de
Delta/CKG/Trace. SQLite (v2) es SSOT; no existe lectura directa del
`.json.gz` legacy en el runtime.

Inyección: si el composition root provee graph_factory se usa (permite un
DbBackend futuro por config); si no, se construye deterministicamente un
backend desde data_dir (resuelto por config — default sqlite) — sin service
locator ni imports de features/main.
"""

from __future__ import annotations

import os
from typing import Any

from fast_mcp.features.code_analysis.application.ports.cpg_repository import CpgRepository
from fast_mcp.shared_kernel.cognitive_graph.config import load_memory_connection


class CognitiveCpgRepository(CpgRepository):
    """Implementación sobre el índice 'cpg' del puerto StorageBackend."""

    INDEX = "cpg"

    def __init__(self, data_dir: str, project_id: str, graph_factory: Any = None):
        self._data_dir = os.path.abspath(data_dir)
        self._project_id = project_id
        # data_dir YA es la raíz de memorias resuelta (no re-resolver).
        conn = load_memory_connection(self._data_dir)
        if graph_factory is not None:
            # Canónico (ADR-059/061): el composition root inyecta el factory
            # (Callable[[MemoryConnection], CognitiveGraph]).
            self._cg = graph_factory(conn)
        else:
            # Fallback determinista: misma composición que el composition root,
            # sin service locator (el puerto sigue siendo el único camino IO).
            self._cg = _default_graph(conn)

    # ── Lectura (shape legacy preservado: nodes/edges/metadata/stats) ──

    @property
    def data_dir(self) -> str:
        return self._data_dir

    def exists(self) -> bool:
        """True si el índice cpg tiene docs (sqlite/v2 SSOT, no .json.gz)."""
        return bool(self._cg.get_docs(self.INDEX))

    def read_fingerprint(self) -> str:
        """Devuelve el source_fingerprint del CPG (metadata del índice cpg).

        ADR-WALKER-055 F1 + ADR-WALKER-063 CA-9: lectura SOLO por el puerto
        (v2); el .json.gz legacy ya no se lee en el runtime.
        """
        data = self.load(self._project_id)
        return str(data.get("source_fingerprint", "") or "")

    def load(self, project_id: str = "") -> dict:
        pid = project_id or self._project_id
        nodes = self._cg.get_docs(self.INDEX)
        edges = self._cg.get_all_edges(self.INDEX)
        metadata = self._cg.get_metadata(self.INDEX)
        data = dict(metadata)
        data["nodes"] = nodes
        data["edges"] = edges
        data.setdefault("version", "2.0")
        data.setdefault("project_id", pid)
        data["stats"] = {
            "total_nodes": len(nodes),
            "total_edges": len(edges),
            "files_scanned": metadata.get("stats", {}).get("files_scanned", 0),
        }
        return data

    def save(self, data: dict) -> None:
        """FULL REPLACE en un ciclo atómico (mismo contrato que antes).

        F3 (ADR-WALKER-055): los derivados (business_layer/quality_report/
        bug_proposals/dfg/analysis_results) NO se persisten en el blob.
        """
        from fast_mcp.features.code_analysis.infrastructure.cpg_constants import CPG_DERIVED_KEYS

        nodes = data.get("nodes", {}) or {}
        edges = data.get("edges", []) or []
        metadata = {
            k: v for k, v in data.items()
            if k not in ("nodes", "edges") and k not in CPG_DERIVED_KEYS
        }
        self._cg.replace_blob(self.INDEX, dict(nodes), list(edges), metadata)

    # ── Búsqueda estructurada (por el port) ──

    def search_nodes(
        self,
        filters: dict,
        max_results: int = 50,
        offset: int = 0,
    ) -> dict:
        query = dict(filters)
        if "size" not in query:
            query["size"] = max_results
        if offset:
            query["_from"] = offset
        return self._cg.search(self.INDEX, query)

    def get_node(self, node_id: str) -> dict | None:
        result = self._cg.search(self.INDEX, {"_id": node_id, "size": 1})
        hits = result.get("hits", {}).get("hits", [])
        return hits[0].get("_source") if hits else None

    def get_stats(self, project_id: str = "") -> dict:
        data = self.load(project_id or self._project_id)
        nodes = data.get("nodes", {})
        return {
            "total_nodes": len(nodes),
            "total_edges": len(data.get("edges", [])),
            "files_scanned": len(
                set(n.get("path", "") for n in nodes.values() if isinstance(n, dict))
            ),
        }


def _default_graph(conn: Any) -> Any:
    """Construye el CognitiveGraph por el port (misma composición del root).

    Determinista: backend resuelto por config + registro de índices de
    dominio. Es la MISMA secuencia que _build_graph — sin locator.
    """
    from fast_mcp.shared_kernel.cognitive_graph import build_cognitive_graph
    from fast_mcp.shared_kernel.cognitive_graph.backends import resolve_storage_backend
    from fast_mcp.features.code_analysis.schema import register_cpg_indexes

    backend = resolve_storage_backend(conn)
    # Construcción canónica (ADR-068 G7): índices auto-registrados por features.
    # El índice 'cpg' (SSOT del schema) debe quedar registrado en el grafo del
    # repo; sin esto, replace_blob/store_many("cpg", ...) fallan con "Índice
    # 'cpg' no registrado" al guardar/leer el CPG por el puerto StorageBackend.
    graph = build_cognitive_graph(
        chain_dir="",
        conn=conn,
        backend=backend,
        register_defaults=register_cpg_indexes,
    )
    return graph
