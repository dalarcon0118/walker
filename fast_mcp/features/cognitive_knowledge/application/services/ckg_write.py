"""application/services/ckg_write.py — CkgWriteService.

All CKG mutations: save nodes, collect knowledge, capture decisions,
delete nodes, initialize CKG, index external repos.

SRP: this service handles write operations only. Read operations are in
ckg_read.py, extraction in ckg_extract.py.
"""
from __future__ import annotations

import logging
from typing import Any

from returns.pipeline import is_successful

from fast_mcp.features.cognitive_knowledge.application.services.ckg_common import (
    _resolve_ckg,
    build_ckg_node,
    is_empty_payload,
    normalize_type,
)

logger = logging.getLogger(__name__)


class CkgWriteService:
    """Write operations for the CKG."""

    @staticmethod
    def save_nodes(
        chain_dir: str, nodes: list[dict], task_id: str = "", session_id: str = ""
    ):
        """Save knowledge nodes to CKG with metadata injection."""
        from returns.result import safe

        @safe
        def _save() -> dict:
            ckg, _ = _resolve_ckg(chain_dir)
            if not ckg:
                return {"message": "no data to save", "saved": False}
            if not nodes:
                return {"message": "no data to save", "saved": False}
            skipped: list[str] = []
            for item in nodes:
                if not isinstance(item, dict):
                    continue
                # `label` es obligatorio para el storage: sin él el nodo se
                # descartaba EN SILENCIO (`nodes_created: 0`, sin error). Se
                # deriva de `statement`/`subject`/`id` y, si no hay ninguno, se
                # reporta como skipped en vez de fingir que se guardó.
                if not item.get("label"):
                    item["label"] = (
                        item.get("statement") or item.get("subject") or item.get("id") or ""
                    )
                if not item.get("label"):
                    skipped.append(str(item.get("id", "?")))
                    continue
                item.setdefault("metadata", {})
                item["metadata"].setdefault("task_id", task_id)
                if session_id:
                    item["metadata"].setdefault("source", f"session:{session_id}")
                # Estandarización (fix 2026-11-16): type canónico al guardar.
                item["type"] = normalize_type(item.get("type", "decision"))
            result = ckg.save_nodes(nodes)
            saved = {
                "ok": True,
                "nodes_saved": result.get("nodes_created", 0),
                "result": result,
            }
            if skipped:
                saved["skipped"] = skipped
            return saved

        return _save()

    @staticmethod
    def collect_knowledge(chain_dir: str, data: dict, task_id: str = ""):
        """Collect and persist knowledge from session."""
        from returns.result import safe

        @safe
        def _collect() -> dict:
            ckg, _ = _resolve_ckg(chain_dir)
            if not ckg:
                return {"ok": False, "error": "CKG not available"}
            nodes = data.get("nodes", [])
            if not nodes:
                return {"ok": True, "message": "no nodes to collect"}
            result = ckg.save_nodes(nodes)
            return {
                "ok": True,
                "nodes_saved": result.get("nodes_created", 0),
                "result": result,
            }

        return _collect()

    @staticmethod
    def research_topic(chain_dir: str, topic: str, query: str = ""):
        """Research a topic in the CKG."""
        from returns.result import safe

        @safe
        def _research() -> dict:
            ckg, _ = _resolve_ckg(chain_dir)
            if not ckg:
                return {"ok": False, "error": "CKG not available"}
            search_query = query or topic
            results = ckg.search(search_query)
            return {
                "ok": True,
                "topic": topic,
                "results": results[:20],
                "count": len(results),
            }

        return _research()

    @staticmethod
    def index_repo(
        chain_dir: str,
        url: str,
        name: str,
        branch: str = "main",
        outline: tuple | None = None,
    ):
        """Index an external repo as reference in CKG.

        `outline` es (run_outline, parse_outline) inyectado por el composition
        root / handler (regla §3bis R1): este feature NO importa code_analysis.
        """
        from fast_mcp.features.cognitive_knowledge.application.use_cases.index_repo import (
            IndexRepoUseCase,
        )

        return IndexRepoUseCase.execute(
            chain_dir, url, name, branch, outline=outline
        )

    @staticmethod
    def capture_decisions(
        decisions: list,
        project_root: str,
        spec_id: str,
        session_id: str,
        sprint_id: str,
    ) -> list[dict]:
        """Register captured decisions in the CKG (best-effort with log)."""
        captured_decisions: list[dict] = []
        for dec in decisions:
            if isinstance(dec, str):
                dec = {"statement": dec}
            statement = dec.get("statement", "") or dec.get("title", "")
            if not statement:
                continue
            node = {
                "type": "decision",
                "label": f"decision:{statement[:40]}",
                "description": dec.get("rationale", "") or dec.get("description", ""),
                "metadata": {
                    "task_id": spec_id,
                    "source": f"session:{session_id}",
                    "session_sprint": sprint_id,
                },
            }
            if dec.get("supersedes"):
                node["supersedes"] = dec["supersedes"]
            save_r = CkgWriteService.save_nodes(project_root, [node])
            if not is_successful(save_r):
                error = save_r.failure()
                logger.warning(
                    "capture_decisions: could not save decision '%s' (%s: %s)",
                    statement[:40], type(error).__name__, error,
                )
                continue
            saved = save_r.value_or({})
            captured_decisions.append({
                "node_id": (saved.get("node_ids") or [None])[0],
                "statement": statement[:60],
                "degraded": saved.get("degraded", []),
            })
        return captured_decisions

    @staticmethod
    def delete_nodes(chain_dir: str, nodes: list[dict]) -> dict:
        """Delete nodes from the CKG."""
        ckg, _ = _resolve_ckg(chain_dir)
        if not ckg:
            return {"ok": False, "deleted": 0}
        deleted = 0
        for node in nodes:
            node_id = node.get("_id") or node.get("id")
            if node_id and ckg.delete_node(node_id):
                deleted += 1
        return {"ok": True, "deleted": deleted}

    @staticmethod
    def delete_by_metadata(chain_dir: str, key: str, value: str) -> dict:
        """Delete nodes from the CKG by metadata key=value."""
        ckg, _ = _resolve_ckg(chain_dir)
        if not ckg:
            return {"ok": False, "nodes_removed": 0}
        return ckg.delete_by_metadata(key, value)

    @staticmethod
    def init_ckg(project_root: str) -> dict:
        """Initialize the CKG at project_root.

        Resolves the canonical data_dir via ProjectService (paths module)
        so the sqlite backend lands in the right place (ADR-063).
        """
        import os

        from fast_mcp.features.cognitive_knowledge import ckg_service
        from fast_mcp.shared_kernel.project_support import ProjectService

        try:
            paths = ProjectService.resolve_paths(project_root)
            data_dir = str(paths.data_dir) if paths.data_dir else os.path.join(
                os.path.abspath(project_root), "data"
            )
        except Exception:  # noqa: BLE001 - best-effort fallback
            data_dir = os.path.join(os.path.abspath(project_root), "data")

        ckg_service(data_dir)
        ckg_path = os.path.join(data_dir, "ckg.json.gz")
        os.makedirs(os.path.dirname(ckg_path), exist_ok=True)
        return {"initialized": True, "ckg_path": ckg_path}
