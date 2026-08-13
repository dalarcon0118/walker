"""infrastructure/cognitive_ckg_repo.py — Implementa CkgRepository via CognitiveGraph.

Capa de infraestructura en Clean Architecture. Traduce operaciones de dominio
a CognitiveGraph (store_many batch, search O(1), delete_many).
"""
from __future__ import annotations

from returns.result import safe
from returns.pipeline import is_successful

from core.engine.bootstrap import get_module_service

from features.cognitive_knowledge.application.ports.ckg_repository import CkgRepository
from features.cognitive_knowledge.entities import KnowledgeFact


class CognitiveCkgRepository(CkgRepository):
    """Implementacion sobre CognitiveGraph (indice 'ckg').

    El orquestador CognitiveGraph se resuelve por DI (key "cognitive_graph").
    """

    def __init__(self, project_root: str):
        graph_factory = get_module_service("cognitive_graph")
        self._cg = graph_factory(project_root)
        if "ckg" not in self._cg.list_indexes():
            self._cg.register_index("ckg", {
                "fields": {
                    "type": "type", "scope": "scope",
                    "label": "statement", "description": "rationale",
                    "tag": "tags", "task": "metadata.task_id",
                },
                "types": {
                    "decision": {}, "refactor": {}, "convention": {},
                    "pattern": {}, "rule": {}, "component": {},
                },
                "storage": {"path": "data/ckg.json.gz", "format": "json.gz"},
                "search": {"type": "fulltext", "fields": ["statement", "rationale"]},
            })

    def save_nodes(self, nodes_data: list[dict]) -> dict:
        """Batch persist con logica completa de dominio (supersedes, metadata)."""
        created = []
        degraded = []
        new_docs: dict[str, dict] = {}
        degraded_docs: dict[str, dict] = {}

        _FACT_FIELDS = {
            "label", "description", "rationale", "type", "tags",
            "metadata", "supersedes", "subject", "statement",
            "context", "query_hints", "location", "api", "status",
            "author", "source", "confidence",
        }

        for item in nodes_data:
            if not isinstance(item, dict) or "label" not in item:
                continue
            label = item["label"]
            if not label or not str(label).strip():
                continue
            doc_id = self.next_id(item.get("type", "node"))

            # ── Resolver supersedes ──
            supersedes = list(item.get("supersedes", []) or [])
            if supersedes:
                existing = self.query({})
                for old in existing:
                    old_stmt = old.get("statement", old.get("label", ""))
                    old_id = old.get("_id", old.get("id", ""))
                    if any(s in old_stmt or old_stmt in s for s in supersedes):
                        if old_id not in supersedes:
                            supersedes.append(old_id)

            # ── Normalizar campos extra a metadata ──
            fact_meta = dict(item.get("metadata", {}) or {})
            for k, v in item.items():
                if k not in _FACT_FIELDS and v is not None:
                    fact_meta.setdefault(k, v)

            fact = KnowledgeFact(
                _id=doc_id,
                type=item.get("type", "decision"),
                subject=label.split(":")[0] if ":" in label else label[:40],
                statement=label,
                rationale=item.get("description", "") or item.get("rationale", ""),
                metadata=fact_meta,
                tags=item.get("tags", []),
            )
            fact._cg_instance = self._cg
            new_docs[doc_id] = dict(fact._data)
            created.append(doc_id)

            # ── Degradar decisiones que esta reemplaza ──
            if supersedes:
                existing = self.query({})
                for old in existing:
                    old_id = old.get("_id", old.get("id", ""))
                    if old_id in supersedes and old_id != doc_id:
                        old_meta = dict(old.get("metadata", {}) or {})
                        old_meta["superseded_by"] = label
                        old_meta["status"] = "superseded"
                        old_meta["superseded_at"] = (
                            __import__("datetime").datetime.now(
                                __import__("datetime").timezone.utc
                            ).isoformat()
                        )
                        degraded_docs[old_id] = {
                            **old,
                            "status": "superseded",
                            "metadata": old_meta,
                        }
                        degraded.append(old_id)

        all_docs = {**new_docs, **degraded_docs}
        if all_docs:
            store_result = _store_batch(self._cg, all_docs)
            if not is_successful(store_result):
                return {
                    "ok": False, "nodes_created": 0, "node_ids": [],
                    "degraded": [], "error": str(store_result.failure()),
                }

        return {"ok": True, "nodes_created": len(created), "node_ids": created,
                "degraded": degraded}

    def query(self, filters: dict | None = None) -> list[dict]:
        filters = dict(filters or {})
        include_all = filters.pop("status", "") == "any" or filters.pop("include_superseded", False)
        result = self._cg.search("ckg", filters or {})
        hits = result["hits"]["hits"]
        all_nodes = []
        for h in hits:
            src = dict(h.get("_source", {}))
            src.setdefault("_id", h.get("_id", ""))
            all_nodes.append(src)
        if include_all:
            return all_nodes
        return [
            n for n in all_nodes
            if (n.get("metadata", {}) or {}).get("status") not in ("superseded", "deprecated")
        ]

    def search(self, query: str, max_results: int = 20) -> list[dict]:
        result = self._cg.search("ckg", {"text": query, "size": max_results})
        hits = result["hits"]["hits"]
        return [
            {**h.get("_source", {}), "_id": h.get("_id", "")}
            for h in hits
        ]

    def delete_by_metadata(self, key: str, value: str) -> dict:
        result = self._cg.search("ckg", {key: value})
        ids = {h["_id"] for h in result["hits"]["hits"]}
        if ids:
            return self._cg.delete_many("ckg", ids)
        return {"ok": True, "nodes_removed": 0}

    def next_id(self, prefix: str = "node") -> str:
        found = self._cg.max_key("ckg", prefix)
        max_num = found[1] if found else 0
        return f"{prefix}-{max_num + 1:03d}"


@safe
def _store_batch(cg: CognitiveGraph, docs: dict[str, dict]) -> dict:
    return cg.store_many("ckg", docs)
