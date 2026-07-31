"""application/ckg.py — CodeKnowledgeGraph sobre CognitiveGraph via ODM.

CKG se almacena como entidades KnowledgeFact (ODM) en el indice "ckg"
de CognitiveGraph. Cada nodo se persiste via KnowledgeFact(...).save()
lo que automaticamente construye el TokenIndex para busqueda full-text
y permite usar las relaciones del grafo.
"""

from __future__ import annotations
from pathlib import Path
from libs.cognitive_graph import CognitiveGraph
from features.cognitive_knowledge.entities import KnowledgeFact


CKG_INDEX_CONFIG = {
    "fields": {
        "type": "type",
        "scope": "scope",
        "label": "statement",
        "task": "metadata.task_id",
        "description": "rationale",
    },
    "types": {
        "decision": {"description": "Decision de arquitectura"},
        "refactor": {"description": "Refactorizacion aplicada"},
        "convention": {"description": "Convencion de estilo"},
        "pattern": {"description": "Patron de diseno"},
        "rule": {"description": "Regla tecnica"},
        "component": {"description": "Componente reutilizable"},
    },
    "search": {
        "type": "fulltext",
        "fields": ["statement", "rationale"],
    },
    "storage": {"path": "data/ckg.json.gz", "format": "json.gz"},
    "description": "Knowledge Graph: decisiones, convenciones, patrones",
}


class CodeKnowledgeGraph:
    """CKG para desarrollo de software. Persiste via KnowledgeFact(entity).save()."""

    def __init__(self, project_dir: str):
        self.project_dir = Path(project_dir).expanduser().resolve()
        self._cg = CognitiveGraph(str(self.project_dir))
        # Registrar indice con schema ODM si no existe
        if "ckg" not in self._cg.list_indexes():
            self._cg.register_index("ckg", CKG_INDEX_CONFIG)
            KnowledgeFact.bind(self._cg)

    def _next_id(self, prefix: str = "node") -> str:
        """Genera ID secuencial basado en docs existentes."""
        if "ckg" in self._cg.list_indexes():
            info = self._cg._indexes["ckg"].schema_info()
            count = info["total_docs"]
        else:
            count = 0
        return f"{prefix}-{count + 1:03d}"

    def save_decision(
        self,
        label: str,
        description: str = "",
        decision_type: str = "decision",
        metadata: dict | None = None,
    ) -> dict:
        """Persiste una decision como KnowledgeFact."""
        doc_id = self._next_id(decision_type)
        fact = KnowledgeFact(
            _id=doc_id,
            type=decision_type,
            subject=label.split(":")[0] if ":" in label else label[:40],
            statement=label,
            rationale=description,
            metadata={"task_id": metadata.get("task_id", "") if metadata else ""},
        )
        fact._cg_instance = self._cg
        fact.save()
        return {"ok": True, "node_id": doc_id, "label": label}

    def save_nodes(self, nodes_data: list[dict]) -> dict:
        """Persiste multiples nodos como KnowledgeFact entities."""
        created = []
        for item in nodes_data:
            if not isinstance(item, dict) or "label" not in item:
                continue
            label = item["label"]
            if not label or not str(label).strip():
                continue
            doc_id = self._next_id(item.get("type", "node"))
            fact = KnowledgeFact(
                _id=doc_id,
                type=item.get("type", "decision"),
                subject=label.split(":")[0] if ":" in label else label[:40],
                statement=label,
                rationale=item.get("description", ""),
                metadata=item.get("metadata", {}),
                tags=item.get("tags", []),
            )
            fact._cg_instance = self._cg
            fact.save()
            created.append(doc_id)
        return {"ok": True, "nodes_created": len(created), "node_ids": created}

    def query(self, filters: dict | None = None) -> list[dict]:
        """Consulta conocimiento via CognitiveGraph search."""
        result = self._cg.search("ckg", filters or {})
        return [h["_source"] for h in result["hits"]["hits"]]

    def delete_by_metadata(self, key: str, value: str) -> dict:
        """Elimina nodos por metadata."""
        result = self._cg.search("ckg", {key: value})
        removed = 0
        for h in result["hits"]["hits"]:
            self._cg.delete("ckg", h["_id"])
            removed += 1
        return {"ok": True, "nodes_removed": removed}
