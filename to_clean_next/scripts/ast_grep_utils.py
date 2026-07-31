#!/usr/bin/env python3
"""Shared ast-grep utilities for analyze_refactored.py and task.py.

Módulo ÚNICO de integración con ast-grep (sg CLI).
Tanto analyze_refactored.py (deep analysis) como task.py (validación de
task_context) importan desde aquí — NO duplican _LANG_MAP ni run_ast_grep.

Uso:
    from ast_grep_utils import _LANG_MAP, AST_GREP_AVAILABLE, run_ast_grep
    from ast_grep_utils import extract_definitions, _ast_grep_lang

Funciones exportadas:
    run_ast_grep(pattern, filepath, lang=None)    → list[dict]
    extract_definitions(filepath, lang=None)      → set[str]
    _ast_grep_lang(filepath)                      → str | None
    _parse_ast_grep_json(stdout)                  → list[dict]

Constantes exportadas:
    _LANG_MAP         → dict[ext, lang_name]
    AST_GREP_AVAILABLE→ bool
    _LANG_TYPE_PATTERNS → dict[lang, list[pattern]]
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path


# ── Binary ──────────────────────────────────────────────────
AST_GREP = os.environ.get("AST_GREP_BIN") or "sg"
AST_GREP_AVAILABLE = False

try:
    r = subprocess.run([AST_GREP, "--version"], capture_output=True, text=True, timeout=5)
    AST_GREP_AVAILABLE = r.returncode == 0
except (FileNotFoundError, subprocess.TimeoutExpired):
    AST_GREP_AVAILABLE = False


# ═══════════════════════════════════════════════════════════════
#  LANG MAP — file extension → ast-grep language name
# ═══════════════════════════════════════════════════════════════
_LANG_MAP: dict[str, str] = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".jsx": "jsx",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".rb": "ruby",
    ".kt": "kotlin",
    ".swift": "swift",
    ".scala": "scala",
    ".php": "php",
    ".c": "c",
    ".cpp": "cpp",
    ".cs": "csharp",
    ".dart": "dart",
    ".hs": "haskell",
    ".sh": "bash",
    ".bash": "bash",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".css": "css",
    ".html": "html",
    ".md": "markdown",
}


def _ast_grep_lang(filepath: str | Path) -> str | None:
    """Map file extension to ast-grep language name."""
    if isinstance(filepath, str):
        filepath = Path(filepath)
    return _LANG_MAP.get(filepath.suffix.lower())


# ═══════════════════════════════════════════════════════════════
#  RUN — execute ast-grep and return structured results
# ═══════════════════════════════════════════════════════════════
def run_ast_grep(pattern: str, filepath: str | Path, lang: str | None = None) -> list[dict]:
    """Run ast-grep structural search and return structured results.

    Returns list of {line, col, text, message} for each match.
    Same interface as analyze_refactored.py's run_ast_grep().
    """
    if not AST_GREP_AVAILABLE:
        return []
    if isinstance(filepath, str):
        filepath = Path(filepath)
    if lang is None:
        lang = _ast_grep_lang(filepath)
    if not lang or not filepath.exists():
        return []

    try:
        r = subprocess.run(
            [AST_GREP, "run", "--json", "-l", lang, "-p", pattern, str(filepath)],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            return []
        return _parse_ast_grep_json(r.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []


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


# ═══════════════════════════════════════════════════════════════
#  DEFINITION EXTRACTION — language-specific patterns
# ═══════════════════════════════════════════════════════════════
_LANG_TYPE_PATTERNS: dict[str, list[str]] = {
    "python":      ["class $A", "def $A", "enum $A", "async def $A"],
    "typescript":  ["class $A", "interface $A", "enum $A", "type $A",
                    "function $A", "const $A = "],
    "tsx":         ["class $A", "interface $A", "enum $A", "type $A",
                    "function $A", "const $A = "],
    "javascript":  ["class $A", "function $A", "const $A = ", "let $A = "],
    "jsx":         ["class $A", "function $A", "const $A = ", "let $A = "],
    "rust":        ["struct $A", "enum $A", "trait $A", "type $A", "fn $A", "const $A"],
    "go":          ["struct $A", "interface $A", "type $A", "func $A"],
    "java":        ["class $A", "interface $A", "enum $A", "record $A"],
    "kotlin":      ["class $A", "interface $A", "enum $A", "fun $A"],
    "ruby":        ["class $A", "def $A"],
    "swift":       ["class $A", "struct $A", "enum $A", "protocol $A", "func $A"],
    "csharp":      ["class $A", "interface $A", "struct $A", "enum $A"],
    "scala":       ["class $A", "trait $A", "object $A", "def $A"],
    "php":         ["class $A", "interface $A", "trait $A", "enum $A", "function $A"],
}
_LANG_TYPE_PATTERNS["__default__"] = [
    "class $A", "interface $A", "struct $A",
    "trait $A", "enum $A", "type $A",
    "def $A", "fn $A", "func $A",
]


def _extract_name_from_def(text: str) -> str | None:
    """Extract definition name from ast-grep matched text.

    Ej: "class JackpotConfig:" → "JackpotConfig"
        "def calculate_jackpot():" → "calculate_jackpot"
        "fn foo()" → "foo"
        "const bar = (x: int) =>" → "bar"
    """
    if not text:
        return None
    text = text.strip()
    for keyword in ["class ", "interface ", "struct ", "trait ",
                    "enum ", "type ", "record ", "def ", "fn ",
                    "func ", "function ", "const ", "let ", "async def "]:
        if text.startswith(keyword):
            rest = text[len(keyword):].strip()
            name = re.split(r'[(:={]', rest)[0].strip()
            if name and len(name) >= 3:
                return name
    return None


def extract_definitions(filepath: str | Path, lang: str | None = None) -> set[str]:
    """Extract ALL definition names from a file using ast-grep.

    Uses LANGUAGE-SPECIFIC pattern groups (_LANG_TYPE_PATTERNS):
    - Python: 4 patterns (class, def, enum, async def)
    - TypeScript: 6 patterns
    - Rust: 7 patterns
    - etc.

    Returns set of symbol names found as definitions.
    """
    definitions: set[str] = set()
    if not AST_GREP_AVAILABLE:
        return definitions
    if isinstance(filepath, str):
        filepath = Path(filepath)
    if not filepath.exists():
        return definitions
    if lang is None:
        lang = _ast_grep_lang(filepath)
    if not lang:
        return definitions

    patterns = _LANG_TYPE_PATTERNS.get(lang, _LANG_TYPE_PATTERNS["__default__"])

    for pattern in patterns:
        try:
            r = subprocess.run(
                [AST_GREP, "run", "--json", "-l", lang, "-p", pattern, str(filepath)],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0 and r.stdout.strip():
                try:
                    matches = json.loads(r.stdout)
                    if isinstance(matches, list):
                        for m in matches:
                            text = m.get("text", "")
                            defn = _extract_name_from_def(text)
                            if defn:
                                definitions.add(defn)
                except json.JSONDecodeError:
                    pass
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue

    return definitions
