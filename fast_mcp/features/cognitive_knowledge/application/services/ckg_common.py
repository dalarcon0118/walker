"""application/services/ckg_common.py — Shared helpers for CKG services.

Pure functions and resolution helpers used by Write, Read, and Extract services.
No state, no side effects beyond import resolution.
"""
from __future__ import annotations

import re
from typing import Any

# ── Vocabulario canónico de tipos de conocimiento (fix 2026-11-16) ─────────
# El collector del workflow consulta por `type` y antes pedía `quality`, que no
# existía en ningún proyecto (los types reales son decision/design-decision/
# implementation*/verification/fix). Normalizar al guardar garantiza que un
# agente que escribe "Design Decision" no deja un type que nadie busca.
TYPE_ALIASES: dict[str, str] = {
    "design decision": "design-decision",
    "design_decision": "design-decision",
    "designdecision": "design-decision",
    "implementation state": "implementation_state",
    "implementation_state": "implementation_state",
    "implementationstate": "implementation_state",
}


def normalize_type(node_type: Any) -> str:
    """Canonicaliza un type: lowercase, trim, alias map. Desconocido pasa tal cual.

    El alias map cubre tanto guiones como guiones bajos (implementation_state
    y design-decision son formas canónicas distintas en el schema); por eso el
    lookup se hace primero sobre la clave cruda y solo después se normaliza.
    """
    if not isinstance(node_type, str):
        return "decision"
    raw = node_type.strip().lower()
    if raw in TYPE_ALIASES:
        return TYPE_ALIASES[raw]
    # camelCase: "DesignDecision" -> "design-decision"
    camel = re.sub(r"([a-z])([A-Z])", r"\1-\2", raw).lower()
    normalized = camel.replace(" ", "-").replace("_", "-")
    if normalized in TYPE_ALIASES:
        return TYPE_ALIASES[normalized]
    # Reintroducir underscores para los que son canónicos con guion bajo.
    if normalized.replace("-", "_") in TYPE_ALIASES:
        return TYPE_ALIASES[normalized.replace("-", "_")]
    return normalized or "decision"


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
    node_type = normalize_type(payload.get("type", "decision"))
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
