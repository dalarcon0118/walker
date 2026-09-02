"""ast-grep integration — structural code search multi-lenguaje.

Result pattern: los cores puros con @safe devuelven Result (nunca lanzan);
los wrappers deciden con is_successful y hacen fallback explícito con log
(convention-001: nunca except silencioso).
"""
from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from returns.pipeline import is_successful
from returns.result import safe

from .config import AST_GREP, AST_GREP_AVAILABLE, ast_grep_lang

logger = logging.getLogger(__name__)


def run_ast_grep(pattern: str, filepath: Path, lang: str | None = None) -> list[dict]:
    """Run ast-grep structural search and return structured results.
    
    Returns list of {line, col, text, message} for each match.
    Multi-lenguaje: usa -l <lang> detectado desde extension.

    Result pattern: el core @safe nunca lanza; ante fallo devuelve []
    y loggea el error (convention-001).
    """
    if not AST_GREP_AVAILABLE:
        return []
    if lang is None:
        lang = ast_grep_lang(filepath)
    if not lang or not filepath.exists():
        return []

    result = _run_ast_grep_core(pattern, filepath, lang)
    if is_successful(result):
        return result.value_or([])
    error = result.failure()
    logger.error(
        "ast-grep run en %s fallo (%s: %s); sin matches",
        filepath, type(error).__name__, error,
    )
    return []


@safe
def _run_ast_grep_core(pattern: str, filepath: Path, lang: str) -> list[dict]:
    """Core puro de run_ast_grep (puede lanzar)."""
    r = subprocess.run(
        [AST_GREP, "run", "--json", "-l", lang, "-p", pattern, str(filepath)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if r.returncode != 0:
        return []
    return _parse_ast_grep_json(r.stdout)


def run_ast_grep_multi(patterns: list[str], filepath: Path, lang: str | None = None) -> dict[str, list[dict]]:
    """Run multiple ast-grep patterns on one file in a single invocation.
    
    ast-grep soporta -p multiple times or -c rule file.
    Returns dict {pattern: matches}
    """
    if not AST_GREP_AVAILABLE or not patterns:
        return {}
    if lang is None:
        lang = ast_grep_lang(filepath)
    if not lang or not filepath.exists():
        return {}

    results = {}
    for pattern in patterns:
        results[pattern] = run_ast_grep(pattern, filepath, lang)
    return results


def _parse_ast_grep_json(stdout: str) -> list[dict]:
    """Parse ast-grep --json output.

    Result pattern: el core @safe nunca lanza; ante JSON invalido
    devuelve [] y loggea el error (convention-001).
    """
    result = _parse_ast_grep_json_core(stdout)
    if is_successful(result):
        return result.value_or([])
    error = result.failure()
    logger.error(
        "Parse de ast-grep JSON fallo (%s: %s); sin matches",
        type(error).__name__, error,
    )
    return []


@safe
def _parse_ast_grep_json_core(stdout: str) -> list[dict]:
    """Core puro de _parse_ast_grep_json (puede lanzar)."""
    data = json.loads(stdout)
    if not isinstance(data, list):
        return []
    results = []
    for m in data:
        rng = m.get("range", {})
        start = rng.get("start", {})
        results.append({
            "line": start.get("line", 0) + 1,  # ast-grep is 0-indexed
            "col": start.get("col", 0) + 1,
            "text": m.get("text", "").strip(),
            "message": m.get("message", ""),
        })
    return results


def find_imports(filepath: Path, lang: str | None = None) -> set[str]:
    """Encuentra imports de cualquier lenguaje usando ast-grep.
    
    Retorna set de nombres de módulos importados (top-level).
    """
    if lang is None:
        lang = ast_grep_lang(filepath)
    if not lang or not AST_GREP_AVAILABLE:
        return set()

    # Patrones de import por lenguaje
    import_patterns = {
        "python": ["import $X", "from $X import $$$"],
        "typescript": ["import $$$ from $X", "import { $$$ } from $X", "import $X = $$$"],
        "javascript": ["import $$$ from $X", "import { $$$ } from $X", "const $X = require($Y)"],
        "rust": ["use $X", "use $X::$$$", "use $X::{$$$}"],
        "go": ['import "$X"', 'import ($$$"$X"$$$)'],
        "java": ["import $X", "import $X.$Y"],
        "ruby": ["require '$X'", "require \"$X\"", "require_relative '$X'"],
        "kotlin": ["import $X", "import $X.$Y"],
    }

    patterns = import_patterns.get(lang, ["import $X"])
    imports: set[str] = set()
    for pat in patterns:
        matches = run_ast_grep(pat, filepath, lang)
        for m in matches:
            text = m.get("text", "")
            # Extraer el nombre del módulo del match
            parts = text.replace("import", "").replace("from", "").strip()
            for sep in (" from ", " as ", "."):
                if sep in parts:
                    parts = parts.split(sep)[0]
                    break
            if parts:
                imports.add(parts.strip().split(".")[0])
    return imports
