"""application/ckg.py — CodeKnowledgeGraph sobre CkgRepository (port).

CKG se almacena como entidades KnowledgeFact (ODM) en el indice "ckg"
de CognitiveGraph. CodeKnowledgeGraph delega en CkgRepository para
persistencia y consulta (Clean Architecture: depende de abstraccion).
"""
from __future__ import annotations
from pathlib import Path

from features.cognitive_knowledge.application.ports.ckg_repository import CkgRepository
from features.cognitive_knowledge.infrastructure.cognitive_ckg_repo import CognitiveCkgRepository


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
    """CKG para desarrollo de software. Delega en CkgRepository (port)."""

    def __init__(self, project_dir: str, repo: CkgRepository | None = None):
        self.project_dir = Path(project_dir).expanduser().resolve()
        self._repo = repo or CognitiveCkgRepository(str(self.project_dir))

    def _next_id(self, prefix: str = "node") -> str:
        return self._repo.next_id(prefix)

    def save_decision(
        self,
        label: str,
        description: str = "",
        decision_type: str = "decision",
        metadata: dict | None = None,
    ) -> dict:
        return self.save_nodes([{
            "label": label,
            "description": description,
            "type": decision_type,
            "metadata": metadata or {},
        }])

    def save_nodes(self, nodes_data: list[dict]) -> dict:
        return self._repo.save_nodes(nodes_data)

    def query(self, filters: dict | None = None) -> list[dict]:
        return self._repo.query(filters)

    def delete_by_metadata(self, key: str, value: str) -> dict:
        return self._repo.delete_by_metadata(key, value)
