"""infrastructure/ast_grep_adapter.py — Construye el CPG usando ast-grep.

Usa `ast-grep outline --json` multi-lenguaje (20+ via tree-sitter).
Proporciona símbolos (funciones, clases, imports, exports, miembros).
Reemplaza los patterns manuales legacy.

Result pattern: los cores puros con @safe devuelven Result (nunca lanzan);
los wrappers públicos deciden con is_successful y hacen fallback explícito
con log (convention-001: nunca except silencioso).
"""
from __future__ import annotations
import json
import logging
import os
import shutil
import subprocess
from pathlib import Path, PurePath
from typing import Any

from returns.pipeline import is_successful
from returns.result import safe

from fast_mcp.features.code_analysis.domain.graph import Node, Edge, Graph
from fast_mcp.shared_kernel.dependency_checker import ensure_tools

logger = logging.getLogger(__name__)


def _resolve_ast_grep() -> str:
    """Resuelve el binario REAL de ast-grep.

    En macOS hay dos `ast-grep`: el paquete npm 0.1.0 (no soporta
    `outline --json`) y el binario `sg` 0.44.x. Elegir por nombre hacía que
    el CPG escaneara 0 archivos en silencio. Detectamos por identidad:
    `sg --version` imprime "ast-grep X.Y.Z"; el wrapper npm imprime solo el
    número.
    """
    explicit = os.environ.get("AST_GREP_BIN")
    if explicit:
        return explicit
    for candidate in ("sg", "ast-grep"):
        binary = shutil.which(candidate)
        if not binary:
            continue
        try:
            version = subprocess.run(
                [binary, "--version"], capture_output=True, text=True, timeout=10
            ).stdout.lower()
        except (OSError, subprocess.SubprocessError):
            continue
        if "ast-grep" in version:
            return candidate
    return "ast-grep"


AST_GREP_BIN = _resolve_ast_grep()
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

    Result pattern: el core @safe nunca lanza; ante fallo devuelve []
    y loggea el error (convention-001: nunca silencioso).
    """
    result = _run_outline_core(paths)
    if is_successful(result):
        return result.value_or([])
    error = result.failure()
    logger.error(
        "ast-grep outline fallo (%s: %s); se devuelve outline vacio",
        type(error).__name__,
        error,
    )
    return []


@safe
def _run_outline_core(paths: list[str]) -> list[dict]:
    """Core puro de run_outline (puede lanzar)."""
    r = subprocess.run(
        [AST_GREP_BIN, "outline", "--json", "--items", "all"] + paths,
        capture_output=True, text=True, timeout=120,
    )
    if r.returncode != 0:
        return []
    return json.loads(r.stdout)


def parse_outline(results: list[dict]) -> Graph:
    """Parsea output de ast-grep outline en un Graph con nodos y edges.

    Por cada archivo:
      - Crea un nodo file
      - Por cada símbolo (function, class, module, etc.) crea un nodo
      - Crea edges CONTAINS e IMPORTS entre file y símbolos
      - Si el símbolo tiene miembros (métodos de clase), crea edges CONTAINS

    Result pattern: el core @safe nunca lanza; ante fallo devuelve un
    grafo vacio y loggea el error (convention-001).
    """
    result = _parse_outline_core(results)
    if is_successful(result):
        return result.value_or(Graph({}, []))
    error = result.failure()
    logger.error(
        "Parse de outline fallo (%s: %s); se devuelve grafo vacio",
        type(error).__name__,
        error,
    )
    return Graph({}, [])


@safe
def _parse_outline_core(results: list[dict]) -> Graph:
    """Core puro de parse_outline (puede lanzar)."""
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


def scan_files(paths: list[str], ignore: Any | None = None) -> Graph:
    """Escanea archivos con ast-grep outline y construye el grafo CPG.

    Acepta archivos y directorios. Multi-lenguaje (20+ via tree-sitter).

    Args:
        paths: Archivos o directorios a escanear.
        ignore: Opcional. Instancia de IgnoreRules (proyecto .walkerignore).
                Si se provee, es la SSOT de exclusión (además de IGNORED_DIRS).

    Result pattern: el core @safe nunca lanza; ante fallo devuelve un
    grafo vacio y loggea el error (convention-001).
    """
    result = _scan_files_core(paths, ignore)
    if is_successful(result):
        return result.value_or(Graph({}, []))
    error = result.failure()
    logger.error(
        "scan_files fallo (%s: %s); se devuelve grafo vacio",
        type(error).__name__,
        error,
    )
    return Graph({}, [])


@safe
def _scan_files_core(paths: list[str], ignore: Any | None) -> Graph:
    """Core puro de scan_files (puede lanzar)."""
    ensure_tools(["ast-grep"])

    # Root del proyecto para resolver rutas relativas de IgnoreRules
    ignore_root_dir = _resolve_ignore_root(ignore, paths)

    all_files: list[str] = []
    for p in paths:
        if os.path.isfile(p):
            if ignore:
                rel = _to_rel(p, ignore_root_dir)
                if ignore.is_ignored(rel):
                    continue
            all_files.append(p)
        elif os.path.isdir(p):
            for root, dirs, files in os.walk(p):
                dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and not d.startswith(".")]
                if ignore:
                    # Poda por path relativo (soporta patrones anidados como
                    # `lib/es6/`; el basename solo no matchea).
                    kept = []
                    for d in dirs:
                        rel_dir = _to_rel(os.path.join(root, d), ignore_root_dir)
                        if ignore.is_ignored(rel_dir + "/") or ignore.is_ignored(rel_dir):
                            continue
                        kept.append(d)
                    dirs[:] = kept
                for f in files:
                    fp = os.path.join(root, f)
                    if _detect_lang(fp):
                        if ignore:
                            rel = _to_rel(fp, ignore_root_dir)
                            if ignore.is_ignored(rel):
                                continue
                        all_files.append(fp)

    if not all_files:
        return Graph({}, [])

    results = run_outline(all_files)
    if not results:
        return Graph({}, [])

    return parse_outline(results)


def _resolve_ignore_root(ignore: Any | None, paths: list[str]) -> str:
    """Resuelve el root del proyecto para IgnoreRules.

    IgnoreRules ya conoce su project_root internamente; esta funcion
    intenta derivarlo para calcular rutas relativas consistentes.
    """
    if ignore is None:
        return ""
    # Intentar acceder al project_root interno de IgnoreRules
    root = getattr(ignore, "_project_root", None) or getattr(ignore, "project_root", None)
    if root:
        return str(root)
    # Fallback heurístico desde el primer path absoluto
    for p in paths:
        if os.path.isabs(p):
            return _project_root_of(p)
    return ""


def _to_rel(path: str, root: str) -> str:
    """Convierte path a relativo desde root para el filtro IgnoreRules."""
    if root and os.path.isabs(path):
        try:
            return os.path.relpath(path, root)
        except ValueError:
            return path
    return path


def _project_root_of(path: str) -> str:
    """Heuristica para resolver la raiz del proyecto desde un path absoluto.

    El .walkerignore se define en la raiz del proyecto; los paths absolutos
    de scan_files pueden venir del repo (relativos) o absolutos.
    Para filtrar con IgnoreRules usamos rutas relativas desde la raiz.
    """
    # Si el path ya es relativo (viene de resolve_code_paths), usarlo tal cual
    if not os.path.isabs(path):
        return ""
    # Buscar directorios de proyecto conocidos hacia arriba
    for marker in ("app", "src", "features", "lib", "components", "pages", "tests"):
        idx = path.find(f"/{marker}/")
        if idx != -1:
            return path[:idx]
    # Fallback: devolver el dirname del path (sin barra final)
    return os.path.dirname(path)


def _run_ast_grep(pattern: str, filepath: str, lang: str | None = None) -> list[dict]:
    """Run ast-grep pattern search (para deep patterns en review_service).

    Result pattern: el core @safe nunca lanza; ante fallo devuelve []
    y loggea el error (convention-001).
    """
    if not lang:
        lang = _detect_lang(filepath)
    if not lang or not os.path.exists(filepath):
        return []
    result = _run_ast_grep_core(pattern, filepath, lang)
    if is_successful(result):
        return result.value_or([])
    error = result.failure()
    logger.error(
        "ast-grep pattern '%s' en %s fallo (%s: %s); sin matches",
        pattern, filepath, type(error).__name__, error,
    )
    return []


@safe
def _run_ast_grep_core(pattern: str, filepath: str, lang: str) -> list[dict]:
    """Core puro de _run_ast_grep (puede lanzar)."""
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
