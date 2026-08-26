"""infrastructure/repository.py — Persistencia del CPG en cpg.json.gz.

SRP: Unica responsabilidad = leer/escribir el grafo CPG del filesystem.

ADR-WALKER-054 D5: el índice cpg de CognitiveGraph ES el almacén. El archivo
`cpg.json.gz` vive en la raíz de memorias (`.cognitive/projects/<pid>/data/`)
y se lee/escribe en el formato del índice (docs + edges + metadata +
token_index). Este repository es la capa de compatibilidad entre el dominio
(code_analysis) y el almacenamiento indexado.

Result pattern (dry-python/returns): el core @safe nunca lanza; load()
falla abierto con log (convention-001: nunca except silencioso).
"""
from __future__ import annotations
import json
import logging
import os
from typing import Any

from returns.pipeline import is_successful
from returns.result import safe

from features.code_analysis.domain.graph import Graph

logger = logging.getLogger(__name__)

CPG_FILENAME = "cpg.json.gz"
CPG_VERSION = "2.0"

# Derivados que NO viven en el SSOT gráfico (ADR-WALKER-055 F3): son
# proyecciones (archivos *.json) o recomputables on-the-fly.
CPG_DERIVED_KEYS = frozenset({
    "business_layer", "quality_report", "bug_proposals",
    "dfg", "analysis_results",
})


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _cpg_index(data_dir: str):
    """Construye el índice cpg (schema del composition root, D5).

    El índice persiste en <data_dir>/cpg.json.gz — el MISMO archivo que
    este repository lee/escribe (formato único).
    """
    from libs.cognitive_graph.cognitive_registry import cpg_index_config
    from shared.core.cognitive_graph.index import Index, IndexSchema

    return Index("cpg", IndexSchema(cpg_index_config()), os.path.abspath(data_dir))


def make_cpg_fingerprint_reader(data_dir: str):
    """Factory del reader de fingerprint del CPG (para FileWatcher, DI).

    ADR-WALKER-055 F1: features/project (watcher) NO conoce el formato del
    CPG; el composition root le inyecta este reader provisto por el feature
    que posee el dominio cpg.
    """
    repo = CpgFileRepository(data_dir)
    return repo.read_fingerprint


class CpgFileRepository:
    """Repository that stores/loads CPG as a compressed JSON file.

    Capa de compatibilidad sobre el índice cpg de CognitiveGraph (D5):
    los nodos se guardan como docs completos, los edges como edges del
    grafo y las claves top-level del CPG como metadata del índice.
    """

    def __init__(self, data_dir: str):
        self._data_dir = os.path.abspath(data_dir)
        os.makedirs(self._data_dir, exist_ok=True)

    @property
    def cpg_path(self) -> str:
        return os.path.join(self._data_dir, CPG_FILENAME)

    @property
    def data_dir(self) -> str:
        return self._data_dir

    def exists(self) -> bool:
        return os.path.exists(self.cpg_path) or os.path.exists(
            self.cpg_path.replace(".gz", "")
        )

    def read_fingerprint(self) -> str:
        """Devuelve el source_fingerprint del CPG (formato índice o legacy).

        ADR-WALKER-055 F1: el conocimiento cpg (`source_fingerprint`) vive
        en este feature; shared/core solo expone la lectura genérica del
        archivo (io.read_index_file). Compat con ambos formatos:
          - Índice (D5): `metadata.source_fingerprint`.
          - Legacy: `source_fingerprint` top-level.
        """
        from shared.core.cognitive_graph.io import read_index_file

        data = read_index_file(self.cpg_path)
        if not data:
            data = read_index_file(self.cpg_path.replace(".gz", ".json"))
        if not data:
            return ""
        meta = data.get("metadata", {}) or {}
        return str(
            meta.get("source_fingerprint") or data.get("source_fingerprint", "") or ""
        )

    def load(self, project_id: str = "") -> dict[str, Any]:
        """Carga el CPG del filesystem. Retorna dict con nodes, edges, metadata.

        Result pattern: el core @safe nunca lanza; si el CPG está corrupto se
        devuelve un dict vacío y se loggea (convention-001).
        """
        path = self.cpg_path
        if not os.path.exists(path) and os.path.exists(path.replace(".gz", ".json")):
            # Legacy: archivo .json plano (pre-D5) → leerlo como CPG directo
            result = _read_legacy_json_core(path.replace(".gz", ".json"))
            if is_successful(result):
                return result.value_or({})
        try:
            idx = _cpg_index(self._data_dir)
            idx._load()
            nodes = idx.get_docs()
            edges = idx.get_all_edges()
            metadata = idx.get_metadata()
        except Exception as exc:
            logger.error(
                "CPG ilegible en %s (%s: %s); se devuelve vacío",
                path, type(exc).__name__, exc,
            )
            return {
                "version": CPG_VERSION,
                "project_id": project_id,
                "generated_by": "CpgFileRepository",
                "generated_at": _now(),
                "nodes": {}, "edges": [],
                "stats": {"total_nodes": 0, "total_edges": 0, "files_scanned": 0},
            }

        data = dict(metadata)
        data["nodes"] = nodes
        data["edges"] = edges
        data.setdefault("version", CPG_VERSION)
        data.setdefault("project_id", project_id)
        data.setdefault("generated_by", "CpgFileRepository")
        data.setdefault("generated_at", _now())
        data.setdefault("update_count", 0)
        data["stats"] = {
            "total_nodes": len(nodes),
            "total_edges": len(edges),
            "files_scanned": metadata.get("stats", {}).get("files_scanned", 0),
        }
        return data

    def save(self, data: dict) -> None:
        """Guarda el CPG a través del índice cpg (D5: formato único).

        Contrato FULL REPLACE (como el save() original) en UN ciclo atómico
        (ADR-WALKER-055 F2 — Index.replace_blob): nodos/edges/metadata se
        reemplazan completos y se persiste una sola vez. Los nodos de
        archivos eliminados no quedan stale.

        F3: los derivados (business_layer/quality_report/bug_proposals) NO
        se persisten en el blob — son proyecciones (archivos *.json) o
        recomputables on-the-fly.
        """
        nodes = data.get("nodes", {}) or {}
        edges = data.get("edges", []) or []
        metadata = {
            k: v
            for k, v in data.items()
            if k not in ("nodes", "edges") and k not in CPG_DERIVED_KEYS
        }
        idx = _cpg_index(self._data_dir)
        idx.replace_blob(dict(nodes), edges, metadata)

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


@safe
def _read_legacy_json_core(p: str) -> dict:
    """Core puro de lectura de un CPG legacy .json (puede lanzar)."""
    return json.loads(open(p).read())