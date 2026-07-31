"""application/ckg.py — CodeKnowledgeGraph sobre CognitiveGraph.

CKG se registra como índice "ckg" en CognitiveGraph.
Toda operación de persistencia pasa por CognitiveGraph.
"""

from __future__ import annotations
from pathlib import Path
from _walker.shared.cognitive_graph import CognitiveGraph


CKG_INDEX_CONFIG = {
    "fields": {
        "type": "type",
        "scope": "scope",
        "label": "statement",
        "task": "metadata.task_id",
        "description": "rationale",
    },
    "types": {
        "decision": {"description": "Decisión de arquitectura"},
        "refactor": {"description": "Refactorización aplicada"},
        "convention": {"description": "Convención de estilo"},
        "pattern": {"description": "Patrón de diseño"},
        "rule": {"description": "Regla técnica"},
        "component": {"description": "Componente reutilizable"},
    },
    "storage": {"path": "data/ckg.json.gz", "format": "json.gz"},
    "description": "Knowledge Graph: decisiones, convenciones, patrones",
}


class CodeKnowledgeGraph:
    """CKG para desarrollo de software. Persiste via CognitiveGraph."""

    def __init__(self, project_dir: str):
        self.project_dir = Path(project_dir).expanduser().resolve()
        self._cg = CognitiveGraph(str(self.project_dir))
        self._index = self._cg.register_index("ckg", CKG_INDEX_CONFIG)

    def _next_id(self, prefix: str = "node") -> str:
        """Genera ID secuencial basado en docs existentes."""
        info = self._index.schema_info()
        count = info["total_docs"]
        return f"{prefix}-{count + 1:03d}"

    def save_decision(
        self,
        label: str,
        description: str = "",
        decision_type: str = "decision",
        metadata: dict | None = None,
    ) -> dict:
        """Persiste una decisión."""
        doc_id = self._next_id(decision_type)
        doc = {
            "type": decision_type,
            "subject": label.split(":")[0] if ":" in label else label[:40],
            "statement": label,
            "rationale": description,
            "metadata": {"task_id": metadata.get("task_id", "") if metadata else ""},
        }
        self._index.store(doc_id, doc)
        return {"ok": True, "node_id": doc_id, "label": label}

    def save_nodes(self, nodes_data: list[dict]) -> dict:
        """Persiste múltiples nodos."""
        created = []
        for item in nodes_data:
            if not isinstance(item, dict) or "label" not in item:
                continue
            label = item["label"]
            doc_id = self._next_id(item.get("type", "node"))
            doc = {
                "type": item.get("type", "decision"),
                "subject": label.split(":")[0] if ":" in label else label[:40],
                "statement": label,
                "rationale": item.get("description", ""),
                "metadata": item.get("metadata", {}),
                "tags": item.get("tags", []),
            }
            self._index.store(doc_id, doc)
            created.append(doc_id)
        return {"ok": True, "nodes_created": len(created), "node_ids": created}

    def query(self, filters: dict | None = None) -> list[dict]:
        """Consulta conocimiento."""
        result = self._index.search(filters or {})
        return [h["_source"] for h in result["hits"]["hits"]]

    def delete_by_metadata(self, key: str, value: str) -> dict:
        """Elimina nodos por metadata."""
        result = self._index.search({key: value})
        removed = 0
        for h in result["hits"]["hits"]:
            self._index.delete(h["_id"])
            removed += 1
        return {"ok": True, "nodes_removed": removed}

    def save(self) -> None:
        """Persiste (el índice ya persiste automáticamente en cada store)."""
        pass

    def load(self) -> None:
        """Carga (el índice carga automáticamente en cada operación)."""
        pass
