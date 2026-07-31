"""infrastructure/repository.py — Persistencia del CPG en cpg.json.gz.

SRP: Unica responsabilidad = leer/escribir el grafo CPG del filesystem.
Usa path_resolver para ubicar el archivo.
"""
from __future__ import annotations
import gzip
import json
import os
from typing import Any

from cpg.domain.graph import Graph


CPG_FILENAME = "cpg.json.gz"
CPG_VERSION = "2.0"


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class CpgFileRepository:
    """Repository that stores/loads CPG as a compressed JSON file."""

    def __init__(self, data_dir: str):
        self._data_dir = os.path.abspath(data_dir)
        os.makedirs(self._data_dir, exist_ok=True)

    @property
    def cpg_path(self) -> str:
        return os.path.join(self._data_dir, CPG_FILENAME)

    def exists(self) -> bool:
        return os.path.exists(self.cpg_path) or os.path.exists(
            self.cpg_path.replace(".gz", "")
        )

    def load(self, project_id: str = "") -> dict[str, Any]:
        """Carga el CPG del filesystem. Retorna dict con nodes, edges, metadata."""
        path = self.cpg_path
        # Try .gz first, then .json
        for p in [path, path.replace(".gz", ".json")]:
            if os.path.exists(p):
                try:
                    if p.endswith(".gz"):
                        raw = json.loads(gzip.decompress(open(p, "rb").read()).decode())
                    else:
                        raw = json.loads(open(p).read())
                    return raw
                except Exception:
                    pass
        return {
            "version": CPG_VERSION,
            "project_id": project_id,
            "generated_by": "CpgFileRepository",
            "generated_at": _now(),
            "nodes": {}, "edges": [],
            "stats": {"total_nodes": 0, "total_edges": 0, "files_scanned": 0},
        }

    def save(self, data: dict) -> None:
        """Guarda el CPG como JSON comprimido."""
        path = self.cpg_path
        data["stats"] = {
            "total_nodes": len(data.get("nodes", {})),
            "total_edges": len(data.get("edges", [])),
            "files_scanned": data.get("stats", {}).get("files_scanned", 0),
        }
        payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        open(path, "wb").write(gzip.compress(payload.encode()))

    def load_graph(self, project_id: str = "") -> Graph:
        """Carga el CPG como objeto Graph del dominio."""
        data = self.load(project_id)
        return Graph.from_dict(data)

    def save_graph(self, graph: Graph, project_id: str = "",
                   generated_by: str = "CpgFileRepository",
                   stats: dict | None = None) -> dict:
        """Guarda un Graph como CPG."""
        now = _now()
        data = graph.to_dict()
        data.update({
            "version": CPG_VERSION,
            "project_id": project_id,
            "generated_by": generated_by,
            "generated_at": now,
            "updated_at": now,
            "update_count": 1,
            "stats": stats or {"total_nodes": len(graph.nodes),
                               "total_edges": len(graph.edges), "files_scanned": 0},
        })
        self.save(data)
        return data
