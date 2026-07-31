#!/usr/bin/env python3
"""
hallucination-scanner.py — Detect LLM hallucinated imports in source code.

LLMs frequently generate imports for modules, classes, or functions that
don't actually exist. This script verifies each import resolves to a real
symbol in the project tree, the Python standard library, or installed packages.

Algorithm per import:
  1. Resolve module name to a file path within the project
  2. Parse the module's AST to check if the symbol is defined there
  3. If not found locally, check standard library (sys.stdlib_module_names)
  4. If not in stdlib, try importlib.util.find_spec() for third-party packages
  5. Report any unresolvable import as a HALLUCINATION

Usage:
    python3 hallucination-scanner.py <file1.py> [file2.ts ...]
    python3 hallucination-scanner.py --dir <directory>
    python3 hallucination-scanner.py --json <file1.py> [file2.ts ...]
    python3 hallucination-scanner.py --self-test  (run built-in test cases)

Exit codes:
    0 — No hallucinations detected
    1 — Hallucinations detected
    2 — Error (file not found, invalid arguments)

Integration:
    Called by delta-qlty-check.py as an additional quality gate.
    Output JSON can be consumed as hallucination_gate results.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import re
import sys
from pathlib import Path

# ── Python stdlib module names (Python 3.10+) ────────────────
try:
    _STDLIB = sys.stdlib_module_names
except AttributeError:
    _STDLIB = None

# ── Helpers ──────────────────────────────────────────────────


def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def find_project_root(start: str | None = None) -> str:
    """Walk up to find project root (.git or .cognitive marker)."""
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
# 1. PROJECT-LOCAL MODULE RESOLUTION (Python)
# ═══════════════════════════════════════════════════════════════


def _module_to_path(module_name: str, project_root: str) -> Path | None:
    """Convert a dotted Python module path to a file path.

    Tries:
      pkg/pkg/__init__.py  (package)
      pkg/pkg.py           (module)
      pkg/pkg              (dir, namespace package)
    """
    parts = module_name.split(".")

    # Try as package: cognitive_v2/features/cpg/__init__.py
    candidate = Path(project_root, *parts, "__init__.py")
    if candidate.exists():
        return candidate

    # Try as module: cognitive_v2/features/cpg.py
    candidate = Path(project_root, *parts[:-1], f"{parts[-1]}.py")
    if candidate.exists():
        return candidate

    # Try with suffix on last part
    candidate = Path(project_root, f"{parts[-1]}.py")
    if len(parts) == 1 and candidate.exists():
        return candidate

    # Try as namespace package directory: cognitive_v2/features/cpg/
    candidate = Path(project_root, *parts)
    if candidate.is_dir():
        return (
            candidate / "__init__.py"
            if (candidate / "__init__.py").exists()
            else candidate
        )

    # Try parent as file + child as submodule
    if len(parts) >= 2:
        parent_module = ".".join(parts[:-1])
        child = parts[-1]
        parent_path = _module_to_path(parent_module, project_root)
        if parent_path:
            parent_dir = (
                parent_path.parent if parent_path.name == "__init__.py" else parent_path
            )
            # Check child as module in parent directory
            child_path = parent_dir / f"{child}.py"
            if child_path.exists():
                return child_path
            # Check child as package in parent directory
            child_pkg = parent_dir / child / "__init__.py"
            if child_pkg.exists():
                return child_pkg

    return None


def _collect_public_names(tree: ast.AST) -> set[str]:
    """Collect all publicly exported names from an AST."""
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
    """Check if a symbol is defined, imported, or accessible in a module."""
    if mod_path.is_dir():
        # Namespace package — check __init__.py
        init_path = mod_path / "__init__.py"
        if init_path.exists():
            mod_path = init_path
        else:
            # Cannot verify symbols in namespace packages without __init__.py
            return True  # Assume OK to avoid false positives

    if mod_path.suffix != ".py":
        return True  # Non-Python module, assume OK

    try:
        tree = ast.parse(mod_path.read_text())
    except (SyntaxError, FileNotFoundError):
        return True  # Can't parse, assume OK to avoid false positives

    names = _collect_public_names(tree)

    # Check also __all__ if defined
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
    """Resolve a project-local import: check module path + symbol existence.

    Returns (status, error_message):
      "RESOLVED", None              — import resolves correctly
      "HALLUCINATION", "message"     — import does not resolve
    """
    mod_path = _module_to_path(module_name, project_root)

    if mod_path is None:
        return "HALLUCINATION", f"Module path '{module_name}' not found in project"

    if symbol is None:
        # Just checking the module exists
        return "RESOLVED", None

    # For from X import Y, check if Y is a submodule of X first
    child_module_name = f"{module_name}.{symbol}"
    child_path = _module_to_path(child_module_name, project_root)
    if child_path is not None:
        return "RESOLVED", None  # Y is a submodule, valid

    # Check if symbol is defined/exported in the module
    if _symbol_in_module(symbol, mod_path):
        return "RESOLVED", None

    # Final check: try dynamic import
    try:
        full_name = f"{module_name}.{symbol}"
        spec = importlib.util.find_spec(full_name)
        if spec is not None:
            return "RESOLVED", None
    except (ImportError, ValueError):
        pass

    return "HALLUCINATION", f"Symbol '{symbol}' not found in module '{module_name}'"


# ═══════════════════════════════════════════════════════════════
# 2. STDLIB + THIRD-PARTY RESOLUTION
# ═══════════════════════════════════════════════════════════════


def _is_stdlib(module_name: str) -> bool:
    """Check if a top-level module is in the Python standard library."""
    top = module_name.split(".")[0]
    if _STDLIB is not None:
        return top in _STDLIB

    # Fallback: try importlib and check path
    try:
        spec = importlib.util.find_spec(top)
        if spec is None:
            return False
        if (
            spec.origin
            and "site-packages" not in spec.origin
            and "dist-packages" not in spec.origin
        ):
            return True
        return spec.origin is None  # namespace packages
    except (ImportError, ValueError):
        return False


def _resolve_third_party(
    module_name: str, symbol: str | None
) -> tuple[str, str | None]:
    """Resolve a third-party import via importlib."""
    try:
        spec = importlib.util.find_spec(module_name)
        if spec is None:
            return "HALLUCINATION", f"Third-party module '{module_name}' not installed"

        if symbol:
            # Try to load module and check attribute
            try:
                mod = importlib.import_module(module_name)
                if hasattr(mod, symbol):
                    return "RESOLVED", None
                return (
                    "HALLUCINATION",
                    f"Symbol '{symbol}' not found in third-party module '{module_name}'",
                )
            except ImportError:
                return (
                    "RESOLVED",
                    None,
                )  # Module exists but can't be fully imported (C extension, etc.)

        return "RESOLVED", None
    except (ImportError, ValueError) as e:
        return "HALLUCINATION", str(e)


# ═══════════════════════════════════════════════════════════════
# 3. PYTHON IMPORT SCANNING (AST)
# ═══════════════════════════════════════════════════════════════


def _is_relative_import(module_name: str) -> bool:
    """Check if a module name starts with a dot (relative import)."""
    return module_name.startswith(".")


def _resolve_relative_module(
    module_name: str, file_path: Path, project_root: str
) -> str | None:
    """Resolve a relative import to an absolute module name."""
    # Count leading dots
    dots = 0
    while dots < len(module_name) and module_name[dots] == ".":
        dots += 1

    rest = module_name[dots:]

    # Get the absolute path of the current file's package
    current_file = file_path.resolve()

    # Navigate up 'dots' levels from the current file's package
    pkg_dir = current_file.parent
    for _ in range(dots - 1):
        pkg_dir = pkg_dir.parent

    # If there's an __init__.py, the package is that dir
    # Otherwise, the package is the parent dir
    if (pkg_dir / "__init__.py").exists():
        pass  # pkg_dir is correct

    # Build the absolute module path
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
    """Full resolution chain: project-local → stdlib → third-party.

    resolution_order:
      1. Project-local (check file in project tree)
      2. Python standard library
      3. Third-party installed package

    Returns (status, error_message).
    """
    # Resolve relative imports first
    if _is_relative_import(module_name):
        resolved = _resolve_relative_module(module_name, file_path, project_root)
        if resolved is None:
            return "HALLUCINATION", f"Cannot resolve relative import '{module_name}'"
        module_name = resolved

    # 1. Project-local resolution
    mod_path = _module_to_path(module_name, project_root)
    if mod_path is not None:
        return _resolve_project_import(module_name, symbol, project_root)

    # Check if first component is a project directory (namespace package)
    first_dir = Path(project_root, module_name.split(".")[0])
    if first_dir.is_dir():
        return _resolve_project_import(module_name, symbol, project_root)

    # 2. Standard library
    if _is_stdlib(module_name):
        if symbol:
            try:
                mod = importlib.import_module(module_name)
                if hasattr(mod, symbol) or symbol in getattr(mod, "__all__", []):
                    return "RESOLVED", None
                # Check submodule
                try:
                    sub_spec = importlib.util.find_spec(f"{module_name}.{symbol}")
                    if sub_spec is not None:
                        return "RESOLVED", None
                except (ImportError, ValueError):
                    pass
                return (
                    "HALLUCINATION",
                    f"Symbol '{symbol}' not found in stdlib module '{module_name}'",
                )
            except ImportError:
                pass
        return "RESOLVED_STDLIB", None

    # 3. Third-party
    return _resolve_third_party(module_name, symbol)


def _hallucination_issue(line: int, mod: str, sym: str | None, error: str) -> dict:
    """Build a standardized hallucination issue dict."""
    return {
        "line": line,
        "type": "from_import" if sym else "import",
        "module": mod,
        "symbol": sym,
        "status": "HALLUCINATION",
        "error": error,
    }


def _resolve_package_for_file(filepath: Path, level: int) -> Path | None:
    """Resolve the package directory for a relative import with given level.

    level=1 means current package (parent dir),
    level=2 means grandparent dir, etc.
    """
    pkg_dir = filepath.resolve().parent
    for _ in range(level - 1):
        pkg_dir = pkg_dir.parent
    if pkg_dir.is_dir():
        return pkg_dir
    return None


def scan_python_file(filepath: Path, project_root: str) -> list[dict]:
    """Parse a Python file's AST and verify all imports resolve."""
    try:
        source = filepath.read_text()
        tree = ast.parse(source)
    except SyntaxError as e:
        return [{"line": 1, "type": "parse_error", "error": f"SyntaxError: {e}"}]

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

        # from X import Y  (including relative: from . import Y, from .X import Y)
        elif isinstance(node, ast.ImportFrom):
            if node.module is None:
                # from . import Y  (no explicit module = current package + level dots)
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
                            # No __init__.py — namespace package, can't verify
                            pass
                continue
            continue  # Guard: node.level==0 shouldn't happen in valid code

            # from X import Y (absolute or relative with explicit module)
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
# 4. TypeScript / JavaScript SUPPORT
# ═══════════════════════════════════════════════════════════════


def _find_node_modules(start: Path) -> Path | None:
    """Walk up to find the nearest node_modules directory."""
    for parent in [start] + list(start.parents):
        nm = parent / "node_modules"
        if nm.is_dir():
            return nm
    return None


def _parse_ts_import(line: str) -> list[dict]:
    """Extract module imports from a single line of TS/JS."""
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
    """Scan TS/JS file for imports and verify node_modules exist."""
    node_modules = _find_node_modules(filepath)
    issues: list[dict] = []

    for line_num, line in enumerate(filepath.read_text().splitlines(), 1):
        imports = _parse_ts_import(line)
        for imp in imports:
            module = imp.get("module", "")

            # Skip relative imports (./component, ../utils)
            if module.startswith("."):
                continue

            # Extract package name (handle @scoped/packages and paths)
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
# 5. FILE / DIRECTORY SCANNING
# ═══════════════════════════════════════════════════════════════


def scan_file(filepath: Path, project_root: str) -> dict:
    """Scan a single file for hallucinated imports."""
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
    """Recursively scan a directory for source files."""
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
        ".git",
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


# ═══════════════════════════════════════════════════════════════
# 6. SELF-TEST MODE
# ═══════════════════════════════════════════════════════════════
