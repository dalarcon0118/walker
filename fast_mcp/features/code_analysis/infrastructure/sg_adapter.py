"""infrastructure/sg_adapter.py — sg outline runner + parser.

Multi-language: sg outline supports 40+ languages via tree-sitter.
Generates CALLS edges at FILE level (imports) for all languages.
Function-level CALLS require language-specific CFG analysis (see ast_analyzer.py).

Result pattern (dry-python/returns): el core @safe nunca lanza; los wrappers
hacen fallback explícito con log (convention-001: nunca except silencioso).
"""

from __future__ import annotations
import json
import logging
import subprocess
from pathlib import PurePath

from returns.pipeline import is_successful
from returns.result import safe

from fast_mcp.features.code_analysis.domain.graph import Node, Edge, Graph

logger = logging.getLogger(__name__)

SG_CMD = "sg"
SG_ARGS = ["outline", "--json"]
SG_TIMEOUT = 300
IGNORED_DIRS = {
    "node_modules",
    ".git",
    "__pycache__",
    "venv",
    ".venv",
    ".env",
    "dist",
    "build",
    ".next",
    ".cognitive",
}


def run_sg_outline(paths: list[str]) -> list[dict]:
    """Ejecuta sg outline y retorna resultados JSON.

    Result pattern: el core @safe nunca lanza; ante fallo devuelve []
    y loggea el error (convention-001).
    """
    result = _run_sg_outline_core(paths)
    if is_successful(result):
        return result.value_or([])
    error = result.failure()
    logger.error(
        "sg outline fallo (%s: %s); sin resultados",
        type(error).__name__, error,
    )
    return []


@safe
def _run_sg_outline_core(paths: list[str]) -> list[dict]:
    """Core puro de run_sg_outline (puede lanzar)."""
    cmd = [SG_CMD] + SG_ARGS
    if paths:
        cmd.extend(paths)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=SG_TIMEOUT)
    if r.returncode != 0:
        return []
    return json.loads(r.stdout)


def parse_sg_output(results: list[dict]) -> Graph:
    """Parsea output de sg outline en un Graph con nodos y edges.

    Edge types:
      - CONTAINS: archivo → simbolo (function, class, etc.)
      - IMPORTS:  archivo → modulo importado
      - CALLS:    archivo → archivo del modulo importado
    """
    nodes: dict[str, Node] = {}
    edges: list[Edge] = []
    counter = [0]

    def _nid() -> str:
        counter[0] += 1
        return f"cpg-{counter[0]:04d}"

    for file_result in results:
        file_path = file_result.get("path", "")
        items = file_result.get("items", [])
        if not file_path:
            continue

        parts = PurePath(file_path).parts
        if any(ign in parts for ign in IGNORED_DIRS):
            continue

        # File node
        file_id = _nid()
        fname = PurePath(file_path).name
        nodes[file_id] = Node(
            id=file_id,
            type="file",
            name=fname,
            path=file_path,
            line=1,
            kind="file",
        )

        if not items:
            continue

        file_imports: list[str] = []

        for item in items:
            kind = item.get("symbolType", "unknown").lower()
            name = item.get("name", "")

            type_map = {
                "module": "module",
                "class": "class",
                "function": "function",
                "method": "method",
                "constant": "constant",
                "variable": "variable",
                "interface": "interface",
                "struct": "struct",
                "enum": "enum",
                "type": "type",
            }
            node_type = type_map.get(kind, kind)
            is_import = item.get("isImport", False)

            node_data = {
                "id": _nid(),
                "type": node_type,
                "name": name,
                "path": file_path,
                "line": item.get("range", {}).get("start", {}).get("line", 0) + 1,
                "kind": kind,
                "signature": item.get("signature", ""),
                "is_import": is_import,
                "ast_kind": item.get("astKind", ""),
            }

            node_id = node_data["id"]
            members_raw = item.get("members")
            if members_raw:
                member_ids = []
                for m in members_raw:
                    if isinstance(m, dict):
                        mid = _nid()
                        mname = m.get("name", "")
                        member_ids.append(mid)
                        nodes[mid] = Node(
                            id=mid,
                            type="method",
                            name=mname,
                            path=file_path,
                            line=m.get("range", {}).get("start", {}).get("line", 0) + 1,
                            kind=m.get("symbolType", "method").lower(),
                            signature=m.get("signature", ""),
                            ast_kind=m.get("astKind", ""),
                        )
                        edges.append(Edge(from_id=node_id, to_id=mid, type="CONTAINS"))
                if member_ids:
                    node_data["member_ids"] = member_ids

            nodes[node_id] = Node.from_dict(node_data)

            edge_type = "IMPORTS" if is_import else "CONTAINS"
            if is_import:
                file_imports.append(name)
            edges.append(Edge(from_id=file_id, to_id=node_id, type=edge_type))

        # FILE-level CALLS edges: file A → file B when A imports B
        for imp_name in file_imports:
            for other_id, other_node in nodes.items():
                if other_node.name == imp_name and other_node.type in (
                    "module",
                    "file",
                ):
                    edges.append(Edge(from_id=file_id, to_id=other_id, type="CALLS"))
                    break

    return Graph(nodes, edges)
