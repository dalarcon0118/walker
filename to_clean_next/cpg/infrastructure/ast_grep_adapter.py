"""infrastructure/ast_grep_adapter.py — Construye el CPG usando ast-grep.

Usa `ast-grep outline --json` multi-lenguaje (20+ via tree-sitter).
Proporciona símbolos (funciones, clases, imports, exports, miembros).
Reemplaza tanto a sg_adapter.py como a los patterns manuales.
"""
from __future__ import annotations
import json
import os
import subprocess
from pathlib import Path, PurePath

from cpg.domain.graph import Node, Edge, Graph


AST_GREP_BIN = os.environ.get("AST_GREP_BIN") or "ast-grep"
IGNORED_DIRS = {
    "node_modules", ".git", "__pycache__", "venv", ".venv",
    ".env", "dist", "build", ".next", ".cognitive",
}

_LANG_MAP: dict[str, str] = {
    ".py": "python", ".ts": "typescript", ".tsx": "tsx",
    ".js": "javascript", ".jsx": "jsx", ".rs": "rust",
    ".go": "go", ".java": "java", ".rb": "ruby",
    ".kt": "kotlin", ".swift": "swift", ".scala": "scala",
    ".php": "php", ".c": "c", ".cpp": "cpp", ".cs": "csharp",
    ".dart": "dart", ".hs": "haskell", ".sh": "bash",
    ".bash": "bash", ".yaml": "yaml", ".yml": "yaml",
    ".json": "json", ".css": "css", ".html": "html",
    ".md": "markdown",
}


def _detect_lang(filepath: str) -> str | None:
    return _LANG_MAP.get(Path(filepath).suffix.lower())


def run_outline(paths: list[str]) -> list[dict]:
    """Ejecuta ast-grep outline en paths y retorna JSON estructurado.

    Multi-lenguaje: detecta el lenguaje automáticamente por extensión.
    Retorna símbolos, imports, exports, miembros por archivo.
    """
    try:
        r = subprocess.run(
            [AST_GREP_BIN, "outline", "--json", "--items", "all"] + paths,
            capture_output=True, text=True, timeout=120,
        )
        if r.returncode != 0:
            return []
        return json.loads(r.stdout)
    except (FileNotFoundError, json.JSONDecodeError, subprocess.TimeoutExpired):
        return []


def parse_outline(results: list[dict]) -> Graph:
    """Parsea output de ast-grep outline en un Graph con nodos y edges.

    Por cada archivo:
      - Crea un nodo file
      - Por cada símbolo (function, class, module, etc.) crea un nodo
      - Crea edges CONTAINS e IMPORTS entre file y símbolos
      - Si el símbolo tiene miembros (métodos de clase), crea edges CONTAINS
    """
    nodes: dict[str, Node] = {}
    edges: list[Edge] = []
    counter = [0]

    def _nid() -> str:
        counter[0] += 1
        return f"cpg-{counter[0]:04d}"

    type_map = {
        "module": "module", "class": "class", "function": "function",
        "method": "method", "constant": "constant", "variable": "variable",
        "interface": "interface", "struct": "struct", "enum": "enum",
        "type": "type",
    }

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
            id=file_id, type="file", name=fname,
            path=file_path, line=1, kind="file",
        )

        if not items:
            continue

        file_imports: list[str] = []

        for item in items:
            kind = item.get("symbolType", "unknown").lower()
            name = item.get("name", "")
            node_type = type_map.get(kind, kind)
            is_import = item.get("isImport", False)
            node_id = _nid()

            node_data = {
                "id": node_id,
                "type": node_type,
                "name": name,
                "path": file_path,
                "line": item.get("range", {}).get("start", {}).get("line", 0) + 1,
                "kind": kind,
                "signature": item.get("signature", ""),
                "is_import": is_import,
            }

            # Members (para clases con métodos)
            members_raw = item.get("members")
            if members_raw:
                member_ids = []
                for m in members_raw:
                    if isinstance(m, dict):
                        mid = _nid()
                        mname = m.get("name", "")
                        member_ids.append(mid)
                        nodes[mid] = Node(
                            id=mid, type="method", name=mname,
                            path=file_path,
                            line=m.get("range", {}).get("start", {}).get("line", 0) + 1,
                            kind=m.get("symbolType", "method").lower(),
                            signature=m.get("signature", ""),
                        )
                        edges.append(Edge(from_id=node_id, to_id=mid, type="CONTAINS"))
                if member_ids:
                    node_data["member_ids"] = member_ids

            nodes[node_id] = Node.from_dict(node_data)

            edge_type = "IMPORTS" if is_import else "CONTAINS"
            if is_import:
                file_imports.append(name)
            edges.append(Edge(from_id=file_id, to_id=node_id, type=edge_type))

        # CALLS edges a nivel archivo: si A importa B, A CALLS B
        for imp_name in file_imports:
            for other_id, other_node in nodes.items():
                if other_node.name == imp_name and other_node.type in ("module", "file"):
                    edges.append(Edge(from_id=file_id, to_id=other_id, type="CALLS"))
                    break

    return Graph(nodes, edges)


def scan_files(paths: list[str]) -> Graph:
    """Escanea archivos con ast-grep outline y construye el grafo CPG.

    Acepta archivos y directorios. Multi-lenguaje (20+ via tree-sitter).
    """
    all_files: list[str] = []
    for p in paths:
        if os.path.isfile(p):
            all_files.append(p)
        elif os.path.isdir(p):
            for root, dirs, files in os.walk(p):
                dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and not d.startswith(".")]
                for f in files:
                    fp = os.path.join(root, f)
                    if _detect_lang(fp):
                        all_files.append(fp)

    if not all_files:
        return Graph({}, [])

    results = run_outline(all_files)
    if not results:
        return Graph({}, [])

    return parse_outline(results)


def _run_ast_grep(pattern: str, filepath: str, lang: str | None = None) -> list[dict]:
    """Run ast-grep pattern search (para deep patterns en review_service)."""
    if not lang:
        lang = _detect_lang(filepath)
    if not lang or not os.path.exists(filepath):
        return []
    try:
        r = subprocess.run(
            [AST_GREP_BIN, "run", "--json", "-l", lang, "-p", pattern, filepath],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            return []
        data = json.loads(r.stdout)
        if not isinstance(data, list):
            return []
        results = []
        for m in data:
            rng = m.get("range", {})
            start = rng.get("start", {})
            results.append({"line": start.get("line", 0) + 1, "col": start.get("col", 0) + 1, "text": m.get("text", "").strip()})
        return results
    except (FileNotFoundError, json.JSONDecodeError, subprocess.TimeoutExpired):
        return []
