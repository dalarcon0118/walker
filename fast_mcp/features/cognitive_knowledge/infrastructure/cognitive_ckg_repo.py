"""infrastructure/cognitive_ckg_repo.py — Implements CkgRepository via CognitiveGraph.

Infrastructure layer in Clean Architecture. Translates domain operations
to CognitiveGraph (store_many batch, search O(1), delete_many).

ADR-WALKER-061/063: accepts data_dir (resolved by the caller via
ProjectPaths.data_dir or paths module — NEVER hardcoded here).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from returns.result import safe
from returns.pipeline import is_successful

from fast_mcp.features.cognitive_knowledge.application.ports.ckg_repository import CkgRepository
from fast_mcp.features.cognitive_knowledge.domain.entities import KnowledgeFact


def _resolve_graph_factory():
    """Resolve graph_factory: builds a sqlite-backed CognitiveGraph (ADR-063).

    Mirrors cognitive_cpg_repo._default_graph: resolve_storage_backend(conn)
    → SqliteBackend.  Fail-open: None if imports fail.
    """
    try:
        from fast_mcp.shared_kernel.cognitive_graph import build_cognitive_graph
        from fast_mcp.shared_kernel.cognitive_graph.backends import (
            resolve_storage_backend,
        )

        def _factory(conn):
            backend = resolve_storage_backend(conn)
            return build_cognitive_graph(chain_dir="", conn=conn, backend=backend)

        return _factory
    except Exception:
        return None


# Canonical fact fields; the rest is normalized to metadata.
_FACT_FIELDS = {
    "label", "description", "rationale", "type", "tags",
    "metadata", "supersedes", "subject", "statement",
    "context", "query_hints", "location", "api", "status",
    "author", "source", "confidence",
}


class CognitiveCkgRepository(CkgRepository):
    """Implementation over CognitiveGraph (index 'ckg').

    The CognitiveGraph orchestrator is received via constructor injection
    (graph_factory). MUST be provided; no fallback to service locator.
    """

    def __init__(self, data_dir: str, graph_factory=None):
        """CognitiveCkgRepository — data_dir must be resolved by the caller.

        ADR-WALKER-061/063: data_dir is the canonical memory data directory
        (e.g. ProjectPaths.data_dir). The caller resolves it via the paths
        module; this repo NEVER computes paths internally.
        """
        if graph_factory is None:
            graph_factory = _resolve_graph_factory()
        if graph_factory is None:
            raise ValueError(
                "CognitiveCkgRepository requires graph_factory injection. "
                "Pass it from the composition root."
            )
        from fast_mcp.shared_kernel.cognitive_graph.config import (
            load_memory_connection,
        )

        self._data_dir = os.path.abspath(data_dir)
        conn = load_memory_connection(self._data_dir)
        self._cg = graph_factory(conn)
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
                "search": {"type": "fulltext", "fields": ["statement", "rationale"]},
            })

    def save_nodes(self, nodes_data: list[dict]) -> dict:
        """Batch persist with full domain logic (supersedes, metadata)."""
        degraded = []
        new_docs: dict[str, dict] = {}
        degraded_docs: dict[str, dict] = {}
        existing = self.query({})

        batch_counters: dict[str, int] = {}

        for item in nodes_data:
            if not isinstance(item, dict) or "label" not in item:
                continue
            label = item["label"]
            if not label or not str(label).strip():
                continue
            t = item.get("type", "node")
            batch_counters[t] = batch_counters.get(t, 0) + 1
            doc_id = self.next_id(t, offset=batch_counters[t])

            # Resolve supersedes (match by statement against index)
            supersedes = list(item.get("supersedes", []) or [])
            if supersedes:
                for old in existing:
                    old_stmt = old.get("statement", old.get("label", ""))
                    old_id = old.get("_id", old.get("id", ""))
                    if any(s in old_stmt or old_stmt in s for s in supersedes):
                        if old_id not in supersedes:
                            supersedes.append(old_id)

            # Normalize extra fields to metadata
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
            new_docs[doc_id] = fact.to_dict()

            # Degrade decisions that this one replaces
            if supersedes:
                for old in existing:
                    old_id = old.get("_id", old.get("id", ""))
                    if old_id in supersedes and old_id != doc_id:
                        old_meta = dict(old.get("metadata", {}) or {})
                        old_meta["superseded_by"] = label
                        old_meta["status"] = "superseded"
                        old_meta["superseded_at"] = (
                            datetime.now(timezone.utc).isoformat()
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

        return {
            "ok": True,
            "nodes_created": len(new_docs),
            "node_ids": list(new_docs.keys()),
            "degraded": degraded,
        }

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
        result = self._cg.search("ckg", {key: value, "size": 100000})
        ids = {h["_id"] for h in result["hits"]["hits"]}
        if ids:
            return self._cg.delete_many("ckg", ids)
        return {"ok": True, "nodes_removed": 0}

    def next_id(self, prefix: str = "node", offset: int = 0) -> str:
        """Return the next ID for a prefix, over the PERSISTED index.

        `offset` allows generating unique IDs within the SAME batch.
        """
        found = self._cg.max_key("ckg", prefix)
        max_num = found[1] if found else 0
        return f"{prefix}-{max_num + offset:03d}"


@safe
def _store_batch(cg, docs: dict[str, dict]) -> dict:
    return cg.store_many("ckg", docs)
