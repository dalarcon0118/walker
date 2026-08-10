"""cognitive_knowledge/infrastructure/adapters/walker_ckg_adapter.py — Adapter hacia task_execution.

Implementa ICkgServicePort (puerto definido en task_execution) orquestando
CognitiveCkgRepository + KnowledgeGraph del dominio CKG.

SRP: este archivo es el ÚNICO punto de acoplamiento entre cognitive_knowledge
y task_execution. Si task_execution cambia el puerto, solo este adapter
se actualiza.
"""

from __future__ import annotations

from features.cognitive_knowledge.application.ports.ckg_repository import CkgRepository
from features.cognitive_knowledge.domain.services.ckg_service import ICkgService


class WalkerCkgAdapter(ICkgService):
    """Implementación del puerto de task_execution sobre cognitive_knowledge."""

    def __init__(self, repo: CkgRepository, event_bus=None):
        self._repo = repo
        self._bus = event_bus

    def save_knowledge(
        self, nodes: list[dict], project_id: str = ""
    ) -> dict[str, object]:
        result = self._repo.save_nodes(nodes)
        self._emit(
            KnowledgeSaved(
                node_ids=result.get("node_ids", []),
                count=result.get("nodes_created", 0),
                project_id=project_id,
            )
        )
        return result

    def search_knowledge(
        self, query: str, max_results: int = 20
    ) -> list[dict[str, object]]:
        results = self._repo.search(query, max_results)
        self._emit(
            KnowledgeSearched(
                query=query,
                index="ckg",
                hits=len(results),
            )
        )
        return results

    def get_active_knowledge(self, type_name: str = "") -> list[dict[str, object]]:
        return self._repo.query(
            {"status": "active", "type": type_name}
            if type_name
            else {"status": "active"}
        )

    def supersede_decision(
        self, old_node_id: str, new_node_id: str, statement: str = ""
    ) -> dict[str, object]:
        old = self._repo.query({"_id": old_node_id})
        if not old:
            return {"ok": False, "error": f"Node {old_node_id} not found"}
        old_doc = old[0]
        old_meta = dict(old_doc.get("metadata", {}) or {})
        old_meta["superseded_by"] = new_node_id
        old_meta["status"] = "superseded"
        old_meta["superseded_at"] = datetime.now(timezone.utc).isoformat()
        update_result = self._repo.save_nodes([{**old_doc, "metadata": old_meta}])
        self._emit(
            DecisionSuperseded(
                old_node_id=old_node_id,
                new_node_id=new_node_id,
                statement=statement,
            )
        )
        return update_result

    def get_knowledge_for_files(
        self, files: list[str], depth: int = 2
    ) -> list[dict[str, object]]:
        all_nodes = self._repo.query({})
        relevant = []
        for doc in all_nodes:
            doc_text = json.dumps(doc).lower()
            for tf in files:
                tf_lower = tf.lower()
                if tf_lower in doc_text:
                    relevant.append(doc)
                    break
        return relevant[-5:]

    def _emit(self, event):
        if self._bus is not None:
            try:
                self._bus.publish(event)
            except Exception:
                pass
