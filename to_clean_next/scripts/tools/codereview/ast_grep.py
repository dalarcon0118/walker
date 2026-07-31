"""ast-grep integration — structural code search multi-lenguaje."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .config import AST_GREP, AST_GREP_AVAILABLE, ast_grep_lang


def run_ast_grep(pattern: str, filepath: Path, lang: str | None = None) -> list[dict]:
    """Run ast-grep structural search and return structured results.
    
    Returns list of {line, col, text, message} for each match.
    Multi-lenguaje: usa -l <lang> detectado desde extension.
    """
    if not AST_GREP_AVAILABLE:
        return []
    if lang is None:
        lang = ast_grep_lang(filepath)
    if not lang or not filepath.exists():
        return []

    try:
        r = subprocess.run(
            [AST_GREP, "run", "--json", "-l", lang, "-p", pattern, str(filepath)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if r.returncode != 0:
            return []
        return _parse_ast_grep_json(r.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []


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
    """Parse ast-grep --json output."""
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return []
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
