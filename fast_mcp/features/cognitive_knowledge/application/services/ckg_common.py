"""application/services/ckg_common.py — Shared helpers for CKG services.

Pure functions and resolution helpers used by Write, Read, and Extract services.
No state, no side effects beyond import resolution.
"""
from __future__ import annotations

from typing import Any


def _resolve_ckg(chain_dir: str) -> tuple[Any, str]:
    """Resolve CKG from chain_dir. Returns (ckg, project_root) or (None, '')."""
    from fast_mcp.features.cognitive_knowledge import ckg_service
    from fast_mcp.shared_kernel.project_support import ProjectService

    paths = ProjectService.resolve_paths(chain_dir)
    project_root = str(paths.project_root) if paths.project_root else ""
    data_dir = str(paths.data_dir) if paths.data_dir else ""
    if not data_dir:
        return None, project_root
    return ckg_service(data_dir), project_root


def build_ckg_node(payload: dict) -> dict:
    """Transform a REST payload into a canonical CKG node."""
    statement = payload.get("statement", payload.get("label", ""))
    rationale = payload.get("rationale", "")
    node_type = payload.get("type", "decision")
    supersedes = payload.get("supersedes", []) or []
    metadata = payload.get("metadata", {}) or {}

    node_meta = dict(metadata)
    if "scope" in payload and "scope" not in node_meta:
        node_meta["scope"] = payload.get("scope")
    if "severity" in payload and "severity" not in node_meta:
        node_meta["severity"] = payload.get("severity")

    node = {
        "label": statement,
        "description": rationale,
        "type": node_type,
        "tags": payload.get("tags", []) or [],
        "metadata": node_meta,
    }
    if supersedes:
        node["supersedes"] = supersedes
    return node


def is_empty_payload(payload: dict) -> bool:
    """True if the payload is empty or has no relevant fields."""
    if not payload:
        return True
    return not any(
        k in payload
        for k in ("statement", "label", "nodes", "type", "rationale", "metadata")
    )
