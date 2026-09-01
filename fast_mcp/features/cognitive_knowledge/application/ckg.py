"""application/ckg.py — CodeKnowledgeGraph over CkgRepository (port).

CKG is stored as KnowledgeFact entities (ODM) in the "ckg" index
of CognitiveGraph. CodeKnowledgeGraph delegates to CkgRepository for
persistence and queries (Clean Architecture: depends on abstraction).
"""
from __future__ import annotations
from pathlib import Path

from fast_mcp.features.cognitive_knowledge.application.ports.ckg_repository import CkgRepository
from fast_mcp.features.cognitive_knowledge.infrastructure.cognitive_ckg_repo import CognitiveCkgRepository


CKG_INDEX_CONFIG = {
    "fields": {
        "type": "type",
        "scope": "scope",
        "label": "statement",
        "task": "metadata.task_id",
        "description": "rationale",
    },
    "types": {
        "decision": {"description": "Architecture decision"},
        "refactor": {"description": "Applied refactoring"},
        "convention": {"description": "Style convention"},
        "pattern": {"description": "Design pattern"},
        "rule": {"description": "Technical rule"},
        "component": {"description": "Reusable component"},
    },
    "search": {
        "type": "fulltext",
        "fields": ["statement", "rationale"],
    },
    "storage": {"path": "data/ckg.json.gz", "format": "json.gz"},
    "description": "Knowledge Graph: decisions, conventions, patterns",
}


class CodeKnowledgeGraph:
    """CKG for software development. Delegates to CkgRepository (port)."""

    def __init__(self, project_dir: str, repo: CkgRepository | None = None, graph_factory=None):
        self.project_dir = Path(project_dir).expanduser().resolve()
        self._repo = repo or CognitiveCkgRepository(str(self.project_dir), graph_factory=graph_factory)

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

    def search(self, query: str, max_results: int = 20) -> list[dict]:
        return self._repo.search(query, max_results)

    def schema(self, index: str) -> dict:
        """Get schema info for an index (delegates to CognitiveGraph)."""
        return self._repo._cg.schema(index)

    def list_indexes(self) -> list[str]:
        """List all available indexes."""
        return self._repo._cg.list_indexes()

    def search_many(self, queries: dict) -> dict:
        """Federated search across multiple indexes."""
        return self._repo._cg.search_many(queries)

    def delete_by_metadata(self, key: str, value: str) -> dict:
        return self._repo.delete_by_metadata(key, value)

    def delete_node(self, node_id: str) -> bool:
        """Delete a single node by ID."""
        try:
            result = self._repo._cg.delete("ckg", node_id)
            return result.get("ok", False)
        except Exception:
            return False
