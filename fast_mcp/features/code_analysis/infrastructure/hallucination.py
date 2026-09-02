"""infrastructure/hallucination.py — Detector de imports alucinados por LLM.

Verifica que cada import del código fuente resuelva a un símbolo real:
  - módulo/paquete local del proyecto
  - módulo de la librería estándar de Python
  - paquete de terceros instalado (importlib.find_spec)
  - paquete npm/TS en node_modules (para TS/JS)

Algoritmo por import:
  1. Resolver el nombre de módulo a una ruta dentro del proyecto
  2. Parsear el AST del módulo y comprobar que el símbolo existe
  3. Si no se encuentra localmente, probar stdlib (sys.stdlib_module_names)
  4. Si no está en stdlib, probar importlib.util.find_spec() (terceros)
  5. Reportar cualquier import irresoluble como HALLUCINATION

Migrado desde to_clean_next/_internal/hallucination.py (legacy).

Result pattern (dry-python/returns): los cores puros con @safe nunca lanzan;
los wrappers deciden con is_successful y hacen fallback explícito con log
(convention-001: nunca except silencioso).
"""
from __future__ import annotations

import ast
import importlib.util
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any

from returns.pipeline import is_successful
from returns.result import safe

logger = logging.getLogger(__name__)

# ── Python stdlib module names (Python 3.10+) ────────────────
# Detección de versión en import-time: fallback documentado a None (no es
# un error del sistema, sino detección de capacidad del runtime).
try:
    _STDLIB = sys.stdlib_module_names
except AttributeError:
    _STDLIB = None


@safe
def _parse_module_core(mod_path: Path) -> ast.AST:
    """Core puro: parsea un módulo Python (puede lanzar).

    Nota: @safe captura TODAS las excepciones (fail-open), no solo las
    que el original contemplaba (SyntaxError/FileNotFoundError).
    """
    return ast.parse(mod_path.read_text())


@safe
def _find_spec_core(module_name: str) -> Any:
    """Core puro: importlib.util.find_spec (puede lanzar ImportError/ValueError).

    Nota: @safe captura TODAS las excepciones (fail-open amplía el contrato
    original de (ImportError, ValueError)).
    """
    return importlib.util.find_spec(module_name)


@safe
def _import_module_core(module_name: str) -> Any:
    """Core puro: importlib.import_module (puede lanzar ImportError).

    Nota: @safe captura TODAS las excepciones (fail-open): un módulo con
    error de import distinto a ImportError ahora devuelve RESOLVED en vez
    de propagar.
    """
    return importlib.import_module(module_name)

# ── Helpers ──────────────────────────────────────────────────


def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def find_project_root(start: str | None = None) -> str:
    """Sube hasta encontrar la raíz del proyecto (.git o .cognitive)."""
    cwd = os.path.abspath(start or os.getcwd())
    for _ in range(10):
        if os.path.exists(os.path.join(cwd, ".git")):
            return cwd
        if os.path.exists(os.path.join(cwd, ".cognitive")):
            return cwd
        parent = os.path.dirname(cwd)
        if parent == cwd:
            break
        cwd = parent
    return os.path.abspath(os.getcwd())


# ═══════════════════════════════════════════════════════════════
# 1. Resolución de módulos locales del proyecto (Python)
# ═══════════════════════════════════════════════════════════════


def _module_to_path(module_name: str, project_root: str) -> Path | None:
    """Convierte un nombre de módulo Python punteado en ruta de archivo.

    Prueba:
      pkg/pkg/__init__.py  (paquete)
      pkg/pkg.py           (módulo)
      pkg/pkg              (dir, namespace package)
    """
    parts = module_name.split(".")

    # Como paquete: cognitive_v2/features/cpg/__init__.py
    candidate = Path(project_root, *parts, "__init__.py")
    if candidate.exists():
        return candidate

    # Como módulo: cognitive_v2/features/cpg.py
    candidate = Path(project_root, *parts[:-1], f"{parts[-1]}.py")
    if candidate.exists():
        return candidate

    # Con sufijo en la última parte
    candidate = Path(project_root, f"{parts[-1]}.py")
    if len(parts) == 1 and candidate.exists():
        return candidate

    # Como directorio namespace package: cognitive_v2/features/cpg/
    candidate = Path(project_root, *parts)
    if candidate.is_dir():
        return (
            candidate / "__init__.py"
            if (candidate / "__init__.py").exists()
            else candidate
        )

    # Probar padre como archivo + hijo como submódulo
    if len(parts) >= 2:
        parent_module = ".".join(parts[:-1])
        child = parts[-1]
        parent_path = _module_to_path(parent_module, project_root)
        if parent_path:
            parent_dir = (
                parent_path.parent if parent_path.name == "__init__.py" else parent_path
            )
            # Hijo como módulo en el directorio padre
            child_path = parent_dir / f"{child}.py"
            if child_path.exists():
                return child_path
            # Hijo como paquete en el directorio padre
            child_pkg = parent_dir / child / "__init__.py"
            if child_pkg.exists():
                return child_pkg

    return None


def _collect_public_names(tree: ast.AST) -> set[str]:
    """Recopila todos los nombres exportados públicamente de un AST."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not node.name.startswith("_"):
                names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and not target.id.startswith("_"):
                    names.add(target.id)
                elif isinstance(target, ast.Tuple):
                    for elt in target.elts:
                        if isinstance(elt, ast.Name) and not elt.id.startswith("_"):
                            names.add(elt.id)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and not node.target.id.startswith("_"):
                names.add(node.target.id)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
    return names


def _symbol_in_module(symbol: str, mod_path: Path) -> bool:
    """Comprueba si un símbolo está definido, importado o accesible en un módulo."""
    if mod_path.is_dir():
        # Namespace package — comprobar __init__.py
        init_path = mod_path / "__init__.py"
        if init_path.exists():
            mod_path = init_path
        else:
            # No se pueden verificar símbolos en namespace packages sin __init__.py
            return True  # Asumir OK para evitar falsos positivos

    if mod_path.suffix != ".py":
        return True  # Módulo no-Python, asumir OK

    parsed = _parse_module_core(mod_path)
    if not is_successful(parsed):
        error = parsed.failure()
        logger.warning(
            "Módulo %s no parseable (%s); se asume OK para evitar falsos positivos",
            mod_path, error,
        )
        return True
    tree = parsed.value_or(None)
    if tree is None:
        return True

    names = _collect_public_names(tree)

    # Comprobar también __all__ si está definido
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    if isinstance(node.value, ast.List):
                        for elt in node.value.elts:
                            if isinstance(elt, ast.Constant) and isinstance(
                                elt.value, str
                            ):
                                names.add(elt.value)

    return symbol in names


def _resolve_project_import(
    module_name: str, symbol: str | None, project_root: str
) -> tuple[str, str | None]:
    """Resuelve un import local del proyecto: ruta de módulo + símbolo.

    Devuelve (status, error_message):
      "RESOLVED", None              — el import resuelve correctamente
      "HALLUCINATION", "message"     — el import no resuelve
    """
    mod_path = _module_to_path(module_name, project_root)

    if mod_path is None:
        return "HALLUCINATION", f"Module path '{module_name}' not found in project"

    if symbol is None:
        # Solo comprobamos que el módulo existe
        return "RESOLVED", None

    # Para "from X import Y", comprobar primero si Y es submódulo de X
    child_module_name = f"{module_name}.{symbol}"
    child_path = _module_to_path(child_module_name, project_root)
    if child_path is not None:
        return "RESOLVED", None  # Y es un submódulo, válido

    # Comprobar si el símbolo está definido/exportado en el módulo
    if _symbol_in_module(symbol, mod_path):
        return "RESOLVED", None

    # Comprobación final: import dinámico
    full_name = f"{module_name}.{symbol}"
    spec_result = _find_spec_core(full_name)
    if not is_successful(spec_result):
        error = spec_result.failure()
        logger.debug(
            "find_spec(%s) fallo (%s); se reporta no resuelto",
            full_name, error,
        )
        return "HALLUCINATION", f"Symbol '{symbol}' not found in module '{module_name}'"
    if spec_result.value_or(None) is not None:
        return "RESOLVED", None

    return "HALLUCINATION", f"Symbol '{symbol}' not found in module '{module_name}'"


# ═══════════════════════════════════════════════════════════════
# 2. Resolución stdlib + terceros
# ═══════════════════════════════════════════════════════════════


def _is_stdlib(module_name: str) -> bool:
    """Comprueba si un módulo de primer nivel está en la librería estándar."""
    top = module_name.split(".")[0]
    if _STDLIB is not None:
        return top in _STDLIB

    # Fallback: probar importlib y comprobar la ruta
    spec_result = _find_spec_core(top)
    if not is_successful(spec_result):
        error = spec_result.failure()
        logger.debug(
            "find_spec(%s) fallo (%s); se asume no-stdlib", top, error,
        )
        return False
    spec = spec_result.value_or(None)
    if spec is None:
        return False
    if (
        spec.origin
        and "site-packages" not in spec.origin
        and "dist-packages" not in spec.origin
    ):
        return True
    return spec.origin is None  # namespace packages


def _resolve_third_party(
    module_name: str, symbol: str | None
) -> tuple[str, str | None]:
    """Resuelve un import de terceros vía importlib (Result pattern: @safe)."""
    spec_result = _find_spec_core(module_name)
    if not is_successful(spec_result):
        error = spec_result.failure()
        logger.debug(
            "find_spec(%s) fallo (%s); se reporta no instalado",
            module_name, error,
        )
        return "HALLUCINATION", str(error)
    spec = spec_result.value_or(None)
    if spec is None:
        return "HALLUCINATION", f"Third-party module '{module_name}' not installed"

    if symbol:
        # Cargar el módulo y comprobar el atributo
        mod_result = _import_module_core(module_name)
        if not is_successful(mod_result):
            error = mod_result.failure()
            logger.debug(
                "import_module(%s) fallo (%s); el módulo existe pero no puede "
                "importarse completo (C extension, etc.)",
                module_name, error,
            )
            return "RESOLVED", None
        mod = mod_result.value_or(None)
        if mod is not None and hasattr(mod, symbol):
            return "RESOLVED", None
        return (
            "HALLUCINATION",
            f"Symbol '{symbol}' not found in third-party module '{module_name}'",
        )

    return "RESOLVED", None


# ═══════════════════════════════════════════════════════════════
# 3. Escaneo de imports Python (AST)
# ═══════════════════════════════════════════════════════════════


def _is_relative_import(module_name: str) -> bool:
    """Comprueba si un nombre de módulo empieza con punto (import relativo)."""
    return module_name.startswith(".")


def _resolve_relative_module(
    module_name: str, file_path: Path, project_root: str
) -> str | None:
    """Resuelve un import relativo a un nombre de módulo absoluto."""
    # Contar puntos iniciales
    dots = 0
    while dots < len(module_name) and module_name[dots] == ".":
        dots += 1

    rest = module_name[dots:]

    # Ruta absoluta del archivo actual
    current_file = file_path.resolve()

    # Subir 'dots' niveles desde el paquete del archivo actual
    pkg_dir = current_file.parent
    for _ in range(dots - 1):
        pkg_dir = pkg_dir.parent

    # Si hay __init__.py, el paquete es ese dir; si no, el padre
    if (pkg_dir / "__init__.py").exists():
        pass  # pkg_dir es correcto

    # Construir la ruta absoluta del módulo
    rel_to_root = pkg_dir.relative_to(Path(project_root).resolve())
    parts = list(rel_to_root.parts)
    if rest:
        parts.extend(rest.split("."))

    return ".".join(parts)


def check_python_import(
    module_name: str,
    symbol: str | None,
    file_path: Path,
    project_root: str,
) -> tuple[str, str | None]:
    """Cadena de resolución completa: proyecto-local → stdlib → terceros.

    resolution_order:
      1. Local del proyecto (comprobar archivo en el árbol del proyecto)
      2. Librería estándar de Python
      3. Paquete instalado de terceros

    Devuelve (status, error_message).
    """
    # Resolver imports relativos primero
    if _is_relative_import(module_name):
        resolved = _resolve_relative_module(module_name, file_path, project_root)
        if resolved is None:
            return "HALLUCINATION", f"Cannot resolve relative import '{module_name}'"
        module_name = resolved

    # 1. Resolución local del proyecto
    mod_path = _module_to_path(module_name, project_root)
    if mod_path is not None:
        return _resolve_project_import(module_name, symbol, project_root)

    # Comprobar si el primer componente es un directorio del proyecto (namespace package)
    first_dir = Path(project_root, module_name.split(".")[0])
    if first_dir.is_dir():
        return _resolve_project_import(module_name, symbol, project_root)

    # 2. Librería estándar
    if _is_stdlib(module_name):
        if symbol:
            mod_result = _import_module_core(module_name)
            if not is_successful(mod_result):
                error = mod_result.failure()
                logger.debug(
                    "import_module(%s) fallo (%s); se asume stdlib sin verificar",
                    module_name, error,
                )
                return "RESOLVED_STDLIB", None
            mod = mod_result.value_or(None)
            if mod is not None and (
                hasattr(mod, symbol) or symbol in getattr(mod, "__all__", [])
            ):
                return "RESOLVED", None
            # Comprobar submódulo
            sub_result = _find_spec_core(f"{module_name}.{symbol}")
            if is_successful(sub_result) and sub_result.value_or(None) is not None:
                return "RESOLVED", None
            return (
                "HALLUCINATION",
                f"Symbol '{symbol}' not found in stdlib module '{module_name}'",
            )
        return "RESOLVED_STDLIB", None

    # 3. Terceros
    return _resolve_third_party(module_name, symbol)


def _hallucination_issue(line: int, mod: str, sym: str | None, error: str) -> dict:
    """Construye un dict de issue de alucinación estandarizado."""
    return {
        "line": line,
        "type": "from_import" if sym else "import",
        "module": mod,
        "symbol": sym,
        "status": "HALLUCINATION",
        "error": error,
    }


def _resolve_package_for_file(filepath: Path, level: int) -> Path | None:
    """Resuelve el directorio del paquete para un import relativo con nivel dado.

    level=1 significa paquete actual (dir padre),
    level=2 significa dir abuelo, etc.
    """
    pkg_dir = filepath.resolve().parent
    for _ in range(level - 1):
        pkg_dir = pkg_dir.parent
    if pkg_dir.is_dir():
        return pkg_dir
    return None


def scan_python_file(filepath: Path, project_root: str) -> list[dict]:
    """Parsear el AST de un archivo Python y verificar que todos los imports resuelvan."""
    parsed = _parse_module_core(filepath)
    if not is_successful(parsed):
        error = parsed.failure()
        logger.warning(
            "Archivo %s no parseable (%s)", filepath, error,
        )
        return [
            {"line": 1, "type": "parse_error", "error": f"{type(error).__name__}: {error}"}
        ]
    tree = parsed.value_or(None)
    if tree is None:
        return []

    issues: list[dict] = []

    for node in ast.walk(tree):
        # import X, Y
        if isinstance(node, ast.Import):
            for alias in node.names:
                module_name = alias.name
                status, error = check_python_import(
                    module_name, None, filepath, project_root
                )
                if status == "HALLUCINATION":
                    issues.append(
                        _hallucination_issue(node.lineno, module_name, None, error)
                    )

        # from X import Y  (incluye relativos: from . import Y, from .X import Y)
        elif isinstance(node, ast.ImportFrom):
            if node.module is None:
                # from . import Y  (sin módulo explícito = paquete actual + nivel de puntos)
                if node.level > 0:
                    pkg_dir = _resolve_package_for_file(filepath, node.level)
                    if pkg_dir is None:
                        for alias in node.names:
                            issues.append(
                                _hallucination_issue(
                                    node.lineno,
                                    "." * node.level,
                                    alias.name,
                                    f"Cannot resolve package at level {node.level} from {filepath}",
                                )
                            )
                        continue
                    init_path = pkg_dir / "__init__.py"
                    for alias in node.names:
                        symbol_name = alias.name
                        if init_path.exists():
                            if not _symbol_in_module(symbol_name, init_path):
                                issues.append(
                                    _hallucination_issue(
                                        node.lineno,
                                        pkg_dir.name,
                                        symbol_name,
                                        f"Symbol '{symbol_name}' not found in package '{pkg_dir.name}'",
                                    )
                                )
                        else:
                            # Sin __init__.py — namespace package, no se puede verificar
                            pass
                continue

            # from X import Y (absoluto o relativo con módulo explícito)
            module_name = node.module
            for alias in node.names:
                symbol_name = alias.name
                status, error = check_python_import(
                    module_name, symbol_name, filepath, project_root
                )
                if status == "HALLUCINATION":
                    issues.append(
                        _hallucination_issue(
                            node.lineno, module_name, symbol_name, error
                        )
                    )

    return issues


# ═══════════════════════════════════════════════════════════════
# 4. Soporte TypeScript / JavaScript
# ═══════════════════════════════════════════════════════════════


def _find_node_modules(start: Path) -> Path | None:
    """Sube hasta encontrar el directorio node_modules más cercano."""
    for parent in [start] + list(start.parents):
        nm = parent / "node_modules"
        if nm.is_dir():
            return nm
    return None


def _parse_ts_import(line: str) -> list[dict]:
    """Extrae imports de módulo de una sola línea de TS/JS."""
    imports = []

    # import X from 'module'
    m = re.search(
        r'import\s+(?:\{[^}]*\}|\*\s+as\s+\w+|\w+(?:,\s*\w+)?)\s+from\s+[\'"]([^\'"]+)[\'"]',
        line,
    )
    if m:
        imports.append({"module": m.group(1), "type": "import"})

    # import 'module'
    m = re.search(r'import\s+[\'"]([^\'"]+)[\'"]', line)
    if m:
        imports.append({"module": m.group(1), "type": "import_side_effect"})

    # require('module')
    m = re.search(
        r'(?:const|let|var)\s+\w+\s*=\s*require\s*\(\s*[\'"]([^\'"]+)[\'"]\s*\)', line
    )
    if m:
        imports.append({"module": m.group(1), "type": "require"})

    # import { X } from 'module'
    m = re.search(r'import\s+\{([^}]+)\}\s+from\s+[\'"]([^\'"]+)[\'"]', line)
    if m:
        symbols = [s.strip() for s in m.group(1).split(",")]
        imports.append(
            {
                "module": m.group(2),
                "symbols": [s.split(" as ")[0].strip() for s in symbols],
                "type": "named_import",
            }
        )

    return imports


def scan_ts_file(filepath: Path) -> list[dict]:
    """Escanea un archivo TS/JS y verifica que los paquetes npm existan."""
    node_modules = _find_node_modules(filepath)
    issues: list[dict] = []

    for line_num, line in enumerate(filepath.read_text().splitlines(), 1):
        imports = _parse_ts_import(line)
        for imp in imports:
            module = imp.get("module", "")

            # Omitir imports relativos (./component, ../utils)
            if module.startswith("."):
                continue

            # Extraer nombre del paquete (maneja @scoped/packages y rutas)
            pkg_name = module.split("/")[0]
            if module.startswith("@"):
                pkg_name = "/".join(module.split("/")[:2])

            if node_modules is None:
                issues.append(
                    {
                        "line": line_num,
                        "type": "npm_import",
                        "module": module,
                        "status": "WARNING",
                        "error": "node_modules not found, cannot verify",
                    }
                )
            elif not (node_modules / pkg_name).exists():
                issues.append(
                    {
                        "line": line_num,
                        "type": "npm_import",
                        "module": module,
                        "symbols": imp.get("symbols"),
                        "status": "HALLUCINATION",
                        "error": f"npm package '{pkg_name}' not installed in node_modules",
                    }
                )

    return issues


# ═══════════════════════════════════════════════════════════════
# 5. Escaneo de archivos / directorios
# ═══════════════════════════════════════════════════════════════


def scan_file(filepath: Path, project_root: str) -> dict:
    """Escanea un solo archivo en busca de imports alucinados."""
    if not filepath.exists():
        return {"file": str(filepath), "error": "File not found"}

    ext = filepath.suffix.lower()

    if ext == ".py":
        issues = scan_python_file(filepath, project_root)
    elif ext in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
        issues = scan_ts_file(filepath)
    else:
        return {
            "file": str(filepath),
            "skipped": True,
            "reason": f"Unsupported language: {ext}",
        }

    hallucinations = [i for i in issues if i["status"] == "HALLUCINATION"]
    warnings = [i for i in issues if i["status"] == "WARNING"]

    return {
        "file": str(filepath),
        "language": ext,
        "issues": issues,
        "hallucinations": len(hallucinations),
        "warnings": len(warnings),
    }


def scan_directory(dir_path: Path, project_root: str) -> list[dict]:
    """Escanea recursivamente un directorio en busca de archivos fuente."""
    results: list[dict] = []
    skip_dirs = {
        "__pycache__",
        ".git",
        "node_modules",
        ".venv",
        "venv",
        "dist",
        "build",
        ".mypy_cache",
        ".pytest_cache",
        ".tox",
        ".eggs",
        "*.egg-info",
        ".coverage",
        "htmlcov",
    }

    for root, dirs, files in os.walk(dir_path):
        dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith(".")]
        for fname in files:
            if fname.endswith((".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")):
                fp = Path(root, fname)
                result = scan_file(fp, project_root)
                if result.get("hallucinations", 0) > 0:
                    results.append(result)

    return results
