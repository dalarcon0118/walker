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

from .lang_map import LANG_MAP as _LANG_MAP


def _detect_lang(filepath: str) -> str | None:
    return _LANG_MAP.get(Path(filepath).suffix.lower())


def _purescript_let_binding_lines(file_path: str) -> set[int]:
    try:
        from tree_sitter_language_pack import get_parser
    except ImportError:
        return set()
    try:
        parser = get_parser("purescript")
    except Exception:  # noqa: BLE001
        return set()
    try:
        with open(file_path, "rb") as fh:
            src = fh.read()
    except OSError:
        return set()
    try:
        tree = parser.parse(src)
    except Exception:  # noqa: BLE001
        return set()

    lines: set[int] = set()

    def walk(node) -> None:
        if node.type in ("exp_let_in", "let"):
            for ch in node.children:
                if ch.type == "declarations":
                    for decl in ch.children:
                        if decl.type == "function" and decl.children:
                            lines.add(decl.start_point[0] + 1)
        for ch in node.children:
            walk(ch)

    walk(tree.root_node)
    return lines


def _purescript_instance_scopes(file_path: str) -> list[dict]:
    """Rangos de las declaraciones `instance` de un archivo PureScript.

    El outline no expone el scope: los siete `show` de un módulo salen con el
    mismo `name` y sin instancia, así que el impacto elegía una arbitraria.
    tree-sitter sí da el nodo `instance` y su `instance_head` (p. ej.
    `Show Currency`), que es la identidad real del método.

    Returns:
        [{"start": 1-based, "end": 1-based, "head": "Show Currency"}, ...]
    """
    try:
        from tree_sitter_language_pack import get_parser
    except ImportError:
        return []
    try:
        parser = get_parser("purescript")
    except Exception:  # noqa: BLE001
        return []
    try:
        with open(file_path, "rb") as fh:
            src = fh.read()
    except OSError:
        return []
    try:
        tree = parser.parse(src)
    except Exception:  # noqa: BLE001
        return []

    scopes: list[dict] = []

    def walk(node) -> None:
        # `class_instance` es el bloque completo (`instance … where …`); el
        # nodo `instance` es solo la keyword y no contiene el head.
        if node.type == "class_instance":
            head = ""
            for ch in node.children:
                if ch.type == "instance_head":
                    head = " ".join(
                        src[ch.start_byte:ch.end_byte].decode("utf-8", "replace").split()
                    )
                    break
            if head:
                scopes.append({
                    "start": node.start_point[0] + 1,
                    "end": node.end_point[0] + 1,
                    "head": head,
                })
        for ch in node.children:
            walk(ch)

    walk(tree.root_node)
    return scopes


def _instance_scope_for_line(line: int, scopes: list[dict]) -> str:
    for scope in scopes:
        if scope["start"] <= line <= scope["end"]:
            return str(scope.get("head") or "")
    return ""


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
    sgconfig = _resolve_sgconfig()
    if sgconfig:
        cmd = [AST_GREP_BIN, "-c", sgconfig, "outline", "--json", "--items", "all"]
    else:
        cmd = [AST_GREP_BIN, "outline", "--json", "--items", "all"]
    r = subprocess.run(
        cmd + paths,
        capture_output=True, text=True, timeout=120,
    )
    if r.returncode != 0:
        return []
    return json.loads(r.stdout)


def _cyclo_by_line(path: str) -> dict[int, int]:
    """Complejidad ciclomática por línea de definición (solo Python).

    McCabe: 1 + puntos de decisión del cuerpo (if, for, while, except, if-exp,
    operadores booleanos y `if` de comprehensions). `cyclo` existía como campo
    del nodo y se indexaba en None en TODOS los nodos (medido 2026-11-17: 769
    funciones con cyclo=0), así que `metrics` reportaba complejidad 0 y el gate
    de complejidad nunca podía dispararse.

    Devuelve `{}` para lenguajes sin parser: mejor "no disponible" que un cero
    que parece un dato.
    """
    if not path.endswith(".py"):
        return {}
    try:
        import ast

        with open(path, encoding="utf-8", errors="replace") as fh:
            tree = ast.parse(fh.read())
    except (OSError, SyntaxError, ValueError):
        return {}

    out: dict[int, int] = {}

    def measure(node: ast.AST) -> int:
        """Complejidad del cuerpo de UNA función.

        No desciende en funciones/clases anidadas: sus ramas son de ellas, no
        del contenedor. Con `ast.walk` el padre acumulaba las ramas de sus
        closures (runner.main medía 122 por sus `def process(...)` internos).
        """
        value = 1
        stack = list(ast.iter_child_nodes(node))
        while stack:
            child = stack.pop()
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.Lambda, ast.ClassDef)):
                continue  # otro scope
            if isinstance(child, (ast.If, ast.For, ast.AsyncFor, ast.While,
                                  ast.ExceptHandler, ast.IfExp, ast.Assert)):
                value += 1
            elif isinstance(child, ast.BoolOp):
                value += max(0, len(child.values) - 1)
            elif isinstance(child, ast.comprehension):
                value += 1 + len(child.ifs)
            stack.extend(ast.iter_child_nodes(child))
        return value

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[node.lineno] = measure(node)
    return out


def _dedupe_outline_items(items: list[dict]) -> list[dict]:
    """Elimina la firma de tipo cuando la definición del mismo símbolo existe.

    El outline de PureScript emite DOS items por símbolo anotado: la firma
    (`astKind: signature`, patrón `_NAME :: _TYPE`) y la definición
    (`astKind: function`). Cada uno creaba su propio nodo, con ids distintos
    (`-2`), y las CALLS se partían entre ambos: medido 2026-11-22 en
    client-chat, 455 pares (path, name) duplicados y `routeToPath` con 3
    callers en un nodo y 0 en el otro.

    Regla: si el grupo tiene al menos una definición, las firmas se descartan
    (se conserva el orden de definición y, si hay varias, todas — un
    redefinido real no se oculta). Si solo hay firmas, se conserva la primera
    (dos firmas idénticas del mismo nombre no son dos símbolos).
    """
    groups: dict[tuple, list[dict]] = {}
    order: list[tuple] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        key = (
            str(item.get("name") or ""),
            str(item.get("symbolType") or "").lower(),
            bool(item.get("isImport", False)),
        )
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(item)

    out: list[dict] = []
    for key in order:
        group = groups[key]
        is_signature = [
            str(i.get("astKind") or "").lower() == "signature" for i in group
        ]
        if any(not sig for sig in is_signature):
            out.extend(i for i, sig in zip(group, is_signature) if not sig)
        else:
            out.append(group[0])
    return out


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
        cyclo_map = _cyclo_by_line(file_path)

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

        # Un símbolo = un nodo: la firma de tipo no es un segundo símbolo.
        items = _dedupe_outline_items(items)

        let_lines: set[int] = set()
        instance_scopes: list[dict] = []
        if file_path.endswith(".purs"):
            let_lines = _purescript_let_binding_lines(file_path)
            instance_scopes = _purescript_instance_scopes(file_path)

        for item in items:
            kind = item.get("symbolType", "unknown").lower()
            name = item.get("name", "")
            start_line = item.get("range", {}).get("start", {}).get("line", 0) + 1
            if let_lines and start_line in let_lines and kind == "function":
                continue
            node_type = type_map.get(kind, kind)
            is_import = item.get("isImport", False)
            node_id = _nid()

            node_data = {
                "id": node_id,
                "type": node_type,
                "name": name,
                "path": file_path,
                "line": start_line,
                "kind": kind,
                "signature": item.get("signature", ""),
                "is_import": is_import,
            }
            # Un método de instancia no es "una función más": su identidad es
            # la instancia (`Show Currency`). Sin esto, los N `show` del módulo
            # son indistinguibles y el impacto elige uno al azar.
            method_scope = _instance_scope_for_line(start_line, instance_scopes)
            if method_scope and not is_import:
                node_data["scope"] = f"instance {method_scope}"
                node_data["qualified_name"] = f"{method_scope}.{name}"
            if node_type in ("function", "method") and cyclo_map:
                cyclo = cyclo_map.get(start_line)
                if cyclo is not None:
                    node_data["cyclo"] = cyclo

            # Members (para clases con métodos)
            members_raw = item.get("members")
            if members_raw:
                member_ids = []
                for m in members_raw:
                    if isinstance(m, dict):
                        mid = _nid()
                        mname = m.get("name", "")
                        member_ids.append(mid)
                        m_start = m.get("range", {}).get("start", {}).get("line", 0) + 1
                        nodes[mid] = Node(
                            id=mid, type="method", name=mname,
                            path=file_path,
                            line=m_start,
                            kind=m.get("symbolType", "method").lower(),
                            signature=m.get("signature", ""),
                            cyclo=cyclo_map.get(m_start) if cyclo_map else None,
                        )
                        edges.append(Edge(from_id=node_id, to_id=mid, type="CONTAINS"))
                if member_ids:
                    node_data["member_ids"] = member_ids

            nodes[node_id] = Node.from_dict(node_data)

            edge_type = "IMPORTS" if is_import else "CONTAINS"
            edges.append(Edge(from_id=file_id, to_id=node_id, type=edge_type))

        # NOTA (2026-11-17): acá había un bloque que agregaba `file -> módulo`
        # como CALLS ("si A importa B, A CALLS B") además del IMPORTS. El
        # storage deduplica aristas por (from, to) ignorando el type, así que
        # una de las dos se perdía: con ids estables la pérdida era siempre el
        # IMPORTS y cada sync borraba ~4 aristas por archivo. Un import no es
        # una llamada: la relación ya está en IMPORTS.

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
    sgconfig = _resolve_sgconfig()
    if sgconfig:
        cmd = [AST_GREP_BIN, "-c", sgconfig, "run", "--json", "-l", lang, "-p", pattern, filepath]
    else:
        cmd = [AST_GREP_BIN, "run", "--json", "-l", lang, "-p", pattern, filepath]
    r = subprocess.run(
        cmd,
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


def _merge_sgconfig(candidate: str) -> str:
    """Fusiona el sgconfig del proyecto con el global del walker."""
    try:
        from fast_mcp.shared_kernel.paths import walker_root
        from fast_mcp.shared_kernel.sgconfig import seed_sgconfig

        data_dir = walker_root() / "data"
        seed_sgconfig(data_dir)
        global_sg = data_dir / "sgconfig.yml"
    except Exception:  # noqa: BLE001
        return candidate
    if not global_sg.is_file() or os.path.realpath(candidate) == os.path.realpath(str(global_sg)):
        return candidate
    try:
        import yaml

        base = yaml.safe_load(global_sg.read_text()) or {}
        cand_data = yaml.safe_load(Path(candidate).read_text()) or {}
    except Exception:  # noqa: BLE001
        return candidate
    base_cl = base.get("customLanguages") or {}
    cand_cl = cand_data.get("customLanguages") or {}
    merged_cl = dict(base_cl)
    merged_cl.update(cand_cl)
    if not merged_cl:
        return candidate
    merged = dict(cand_data)
    merged["customLanguages"] = merged_cl
    try:
        import yaml as _y

        merged_text = _y.safe_dump(merged, sort_keys=False, allow_unicode=True)
    except Exception:  # noqa: BLE001
        return candidate
    merged_path = Path(candidate).with_name("sgconfig.merged.yml")
    try:
        if not merged_path.is_file() or merged_path.read_text() != merged_text:
            merged_path.write_text(merged_text)
    except OSError:
        return candidate
    return str(merged_path)


def _resolve_sgconfig() -> str | None:
    """Resuelve sgconfig.yml para lenguajes custom (ej. purescript).

    Cascada (el proyecto puede declarar sus propios lenguajes sin modificar
    walker):
      1. env WALKER_SGCONFIG
      2. sgconfig.yml en la raíz del proyecto (autoría del proyecto)
      3. sgconfig.yml en el data dir cognitivo del proyecto activo
      4. sgconfig.yml global del walker (data/sgconfig.yml)
      5. sgconfig.yml en el cwd

    El candidato se fusiona con el global del walker para no perder los
    lenguajes que shippea walker (p. ej. purescript).
    """
    explicit = os.environ.get("WALKER_SGCONFIG")
    if explicit and os.path.isfile(explicit):
        return _merge_sgconfig(explicit)

    # 0. Raíz del proyecto (autoría del proyecto)
    try:
        from fast_mcp.shared_kernel.paths import find_cognitive_root

        cog = find_cognitive_root()
        if cog is not None:
            cand = cog.parent / "sgconfig.yml"
            if cand.is_file():
                return _merge_sgconfig(str(cand))
    except Exception:  # noqa: BLE001
        pass

    # Proyecto activo: sgconfig en el data dir del proyecto que contiene el CWD.
    try:
        from fast_mcp.shared_kernel.paths import find_cognitive_root
        import json as _json
        import os as _os

        cog = find_cognitive_root()
        if cog is not None:
            projects_dir = cog / "projects"
            if projects_dir.is_dir():
                cwd = _os.getcwd()
                candidates = [p for p in projects_dir.iterdir() if p.is_dir()]
                for proj in candidates:
                    manifest = proj / "data" / "project.json"
                    if manifest.is_file():
                        try:
                            data = _json.loads(manifest.read_text())
                            repo = (data.get("project") or {}).get("location", {}).get("repo_path", "")
                            if repo and (cwd == repo or cwd.startswith(repo + _os.sep)):
                                cand = proj / "data" / "sgconfig.yml"
                                if cand.is_file():
                                    return _merge_sgconfig(str(cand))
                        except Exception:  # noqa: BLE001
                            pass
                if len(candidates) == 1:
                    cand = candidates[0] / "data" / "sgconfig.yml"
                    if cand.is_file():
                        return _merge_sgconfig(str(cand))
    except Exception:  # noqa: BLE001
        pass

    # Global del walker
    try:
        from fast_mcp.shared_kernel.paths import walker_root
        from fast_mcp.shared_kernel.sgconfig import seed_sgconfig

        data_dir = walker_root() / "data"
        # Regenerar si la config existente apunta a un parser inexistente
        # (config vieja de otra máquina o walker movido).
        cand = data_dir / "sgconfig.yml"
        if cand.is_file():
            try:
                import yaml as _y

                existing = _y.safe_load(cand.read_text()) or {}
                pl = (existing.get("customLanguages") or {}).get("purescript", {}).get("libraryPath")
                if pl and not Path(pl).is_file():
                    cand.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass
        seed_sgconfig(data_dir)
        cand = data_dir / "sgconfig.yml"
        if cand.is_file():
            return str(cand)
    except Exception:  # noqa: BLE001
        pass

    # cwd
    if os.path.isfile("sgconfig.yml"):
        return _merge_sgconfig(os.path.abspath("sgconfig.yml"))
    return None
