"""application/services/ckg_service.py — CkgApplicationService.

Thin facade delegating to focused services:
  - CkgWriteService: mutations (save, collect, capture, delete, init)
  - CkgReadService: queries, search, schema, health
  - CkgExtractService: CPG cross-feature extraction

The facade exists for backward compatibility: consumers that import
CkgApplicationService continue to work. New consumers should import the
focused service directly.
"""
from __future__ import annotations

from typing import Any

from fast_mcp.features.cognitive_knowledge.application.services.ckg_common import (
    build_ckg_node,
    is_empty_payload,
)
from fast_mcp.features.cognitive_knowledge.application.services.ckg_write import CkgWriteService
from fast_mcp.features.cognitive_knowledge.application.services.ckg_read import CkgReadService
from fast_mcp.features.cognitive_knowledge.application.services.ckg_extract import CkgExtractService


# Re-export shared helpers for backward compatibility
__all__ = [
    "CkgApplicationService",
    "CkgWriteService",
    "CkgReadService",
    "CkgExtractService",
]


class CkgApplicationService:
    """Public facade for CKG operations.

    Delegates to focused services. New code should import the focused
    service directly (CkgWriteService, CkgReadService, CkgExtractService).
    """

    # ── Write (delegates to CkgWriteService) ─────────────────────────

    @staticmethod
    def save_nodes(chain_dir: str, nodes: list[dict], task_id: str = "", session_id: str = ""):
        return CkgWriteService.save_nodes(chain_dir, nodes, task_id=task_id, session_id=session_id)

    @staticmethod
    def collect_knowledge(chain_dir: str, data: dict, task_id: str = ""):
        return CkgWriteService.collect_knowledge(chain_dir, data, task_id=task_id)

    @staticmethod
    def research_topic(chain_dir: str, topic: str, query: str = ""):
        return CkgWriteService.research_topic(chain_dir, topic, query)

    @staticmethod
    def index_repo(chain_dir: str, url: str, name: str, branch: str = "main"):
        return CkgWriteService.index_repo(chain_dir, url, name, branch)

    @staticmethod
    def capture_decisions(decisions, project_root, spec_id, session_id, sprint_id):
        return CkgWriteService.capture_decisions(decisions, project_root, spec_id, session_id, sprint_id)

    @staticmethod
    def delete_nodes(chain_dir: str, nodes: list[dict]) -> dict:
        return CkgWriteService.delete_nodes(chain_dir, nodes)

    @staticmethod
    def delete_by_metadata(chain_dir: str, key: str, value: str) -> dict:
        return CkgWriteService.delete_by_metadata(chain_dir, key, value)

    @staticmethod
    def init_ckg(project_root: str) -> dict:
        return CkgWriteService.init_ckg(project_root)

    # ── Read (delegates to CkgReadService) ───────────────────────────

    @staticmethod
    def query_active(chain_dir: str, type_name: str = "") -> list[dict]:
        return CkgReadService.query_active(chain_dir, type_name=type_name)

    @staticmethod
    def query_by_files(chain_dir: str, files: list[str]) -> list[dict]:
        return CkgReadService.query_by_files(chain_dir, files)

    @staticmethod
    def query_all(chain_dir: str) -> list[dict]:
        return CkgReadService.query_all(chain_dir)

    @staticmethod
    def gather_context(chain_dir: str, query: str = "type:convention OR type:decision", task_id: str = ""):
        return CkgReadService.gather_context(chain_dir, query=query, task_id=task_id)

    @staticmethod
    def get_nodes_for_task(chain_dir: str, task_id: str, task_files: list[str]):
        return CkgReadService.get_nodes_for_task(chain_dir, task_id, task_files)

    @staticmethod
    def check_health(projects_data_dir: str):
        return CkgReadService.check_health(projects_data_dir)

    @staticmethod
    def schema(searcher: Any, index: str = "") -> dict:
        return CkgReadService.schema(searcher, index=index)

    @staticmethod
    def search(searcher: Any, payload: dict, project_id: str = "") -> dict:
        return CkgReadService.search(searcher, payload, project_id=project_id)

    @staticmethod
    def search_many(searcher: Any, payload: dict) -> dict:
        return CkgReadService.search_many(searcher, payload)

    # ── Extract (delegates to CkgExtractService) ─────────────────────

    @staticmethod
    def build_extract_payload(cpg: Any, scope: str, chain_dir: str) -> dict:
        return CkgExtractService.build_extract_payload(cpg, scope, chain_dir)

    # ── Shared helpers (re-exported from ckg_common) ─────────────────

    @staticmethod
    def build_ckg_node(payload: dict) -> dict:
        return build_ckg_node(payload)

    @staticmethod
    def is_empty_payload(payload: dict) -> bool:
        return is_empty_payload(payload)

    # ── Analysis adapter ─────────────────────────────────────────────

    @staticmethod
    def get_analysis_adapter(chain_dir: str) -> Any:
        """Build CKG analysis adapter (ICkgServicePort)."""
        from fast_mcp.features.cognitive_knowledge import ckg_analysis_adapter
        return ckg_analysis_adapter(chain_dir)
