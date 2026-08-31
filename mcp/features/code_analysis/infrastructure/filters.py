"""infrastructure/filters.py — Node matching and file filtering.

SRP: Match CPG nodes against query filters.
"""

from __future__ import annotations

import logging

from returns.pipeline import is_successful
from returns.result import safe

from features.code_analysis.domain.graph import Graph
from features.code_analysis.domain.query import Filter

logger = logging.getLogger(__name__)


def match_node(node: dict, filters: list[Filter]) -> bool:
    """Check if a node matches all filters."""
    for f in filters:
        key, val = f.key, f.value
        if not key:
            continue
        if not _match_single(node, key, val):
            return False
    return True


def _match_single(node: dict, key: str, val: str) -> bool:
    """Evalua UN filtro contra el nodo (dispatch por tipo de campo)."""
    if key == "file":
        return _match_file_node(node, val)
    if key == "function":
        return node.get("type") in ("function", "method") and val in node.get(
            "name", ""
        )
    if key == "class":
        return node.get("type") == "class" and val in node.get("name", "")
    if key == "name":
        return val in node.get("name", "")
    if key == "type":
        return node.get("type") == val
    if key == "cyclo":
        return _match_threshold(node.get("cyclo"), val)
    if key == "lines":
        return _match_threshold(node.get("loc", 0), val)
    return val in str(node.get(key, ""))


def _match_file_node(node: dict, val: str) -> bool:
    """Matchea el filtro 'file' contra el path del nodo."""
    node_path = node.get("path", "")
    if val.endswith("/*"):
        prefix = val[:-1]
        if prefix not in node_path and not node_path.endswith(prefix.rstrip("/")):
            if not node_path.split("/")[-1].startswith(val[:-2]):
                return False
    elif val not in node_path and val != node.get("name", ""):
        if val != node_path.split("/")[-1]:
            return False
    return True


def _match_threshold(node_val, val: str) -> bool:
    """Matchea un filtro numerico de umbral (>, <, o igualdad de presencia).

    Result pattern: el core @safe nunca lanza; ante umbral no numérico
    devuelve True (match por presencia) y loggea en debug (convention-001).
    """
    if node_val is None:
        return False
    result = _match_threshold_core(node_val, val)
    if is_successful(result):
        return result.value_or(True)
    error = result.failure()
    logger.debug(
        "umbral '%s' no numérico para %r (%s); match por presencia",
        val, node_val, type(error).__name__,
    )
    return True


@safe
def _match_threshold_core(node_val, val: str) -> bool:
    """Core puro de _match_threshold (puede lanzar ValueError/TypeError)."""
    threshold = int(val.replace(">", "").replace("<", ""))
    node_val = int(node_val)
    if ">" in val and not (node_val > threshold):
        return False
    if "<" in val and not (node_val < threshold):
        return False
    return True


def match_file_filter(file_path: str, filters: list[Filter]) -> bool:
    """Check if a file path matches file/function filters."""
    if not filters:
        return True
    for f in filters:
        if f.key == "file":
            if f.value.endswith("/*"):
                if f.value[:-1] not in file_path:
                    return False
            elif f.value not in file_path:
                return False
        elif f.key == "function":
            if f.value not in file_path:
                return False
    return True


def find_nodes(
    graph: Graph,
    filters: list[Filter],
    free_text: str | None = None,
    relation_keys: set[str] | None = None,
) -> list[dict]:
    """Find nodes matching filters, with optional relation expansion.

    Returns list of match dicts with type, name, file, line, relations.
    """
    if relation_keys is None:
        relation_keys = {
            "calls",
            "called_by",
            "imports",
            "imported_by",
            "contains",
            "trace",
            "bug",
            "security",
        }
    attr_filters = [f for f in filters if f.key not in relation_keys]

    matched = []
    for nid, node in graph.nodes.items():
        node_dict = node.to_dict() if hasattr(node, "to_dict") else node
        if not match_node(node_dict, attr_filters):
            continue
        if free_text:
            name = node_dict.get("name", "")
            label = node_dict.get("label", "")
            if free_text not in name and free_text not in label:
                continue

        rels = {}
        for oe in graph.get_outgoing(nid):
            tgt = graph.get_node(oe["to"])
            tname = tgt.name if tgt else str(oe["to"])
            rels.setdefault(oe["type"], []).append(tname)

        entry = {
            "type": node_dict.get("type", "?"),
            "name": node_dict.get("name", ""),
            "file": node_dict.get("path", ""),
            "line": node_dict.get("line", 0),
        }
        if rels:
            entry["relations"] = {k: v[:5] for k, v in rels.items()}
        if node_dict.get("cyclo"):
            entry["cyclo"] = node_dict["cyclo"]
        if node_dict.get("loc"):
            entry["lines"] = node_dict["loc"]
        matched.append(entry)

    return matched
