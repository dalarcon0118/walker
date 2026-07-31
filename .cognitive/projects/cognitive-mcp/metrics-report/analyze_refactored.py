#!/usr/bin/env python3
"""Multi-language code quality analysis via Qlty + ast-grep.

Qlty supports 40+ languages (Python, TypeScript, Rust, Go, Java, etc.).
This script wraps three Qlty commands into a unified report:

    1. qlty metrics   — CC, NLOC, functions, classes, LCOM, cognitive complexity
    2. qlty smells    — duplication, code smells (identical/similar code, complexity)
    3. qlty check     — linter suggestions (ruff, eslint, clippy, bandit, etc.)

Plus ast-grep deep analysis on findings:

    4. ast-grep       — structural queries that deepen qlty findings
                        (classify exception types, find complex structures,
                         locate exact unused imports, etc.)

Usage:
    python3 analyze_refactored.py <file1> [file2.js ...]            # terminal report
    python3 analyze_refactored.py --json <file1.rs> [file2.ts ...]  # JSON output
    python3 analyze_refactored.py --fix <file1.py> [file2.js ...]   # auto-fix + report

Exit codes:
    0 — success
    1 — error
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

# ── Qlty binary ──────────────────────────────────────────────
QLTY = os.environ.get("QLTY_BIN") or "qlty"
QLTY_AVAILABLE = False

try:
    r = subprocess.run([QLTY, "--version"], capture_output=True, text=True, timeout=5)
    QLTY_AVAILABLE = r.returncode == 0
except (FileNotFoundError, subprocess.TimeoutExpired):
    QLTY_AVAILABLE = False

# ── Quality gate thresholds (env-configurable) ────────────────
# These gates determine the exit code: if exceeded, returns 1.
# This enables CODE_REVIEW_SCAN's on_failure_loop to trigger.
_QLTY_MAX_ERRORS = int(os.environ.get("DELTA_QLTY_MAX_ERRORS", "0"))
_QLTY_MAX_WARNINGS = int(os.environ.get("DELTA_QLTY_MAX_WARNINGS", "10"))
_QLTY_MAX_SECURITY_HIGH = int(os.environ.get("DELTA_QLTY_MAX_SECURITY_HIGH", "0"))
_QLTY_MAX_TOTAL_ISSUES = int(os.environ.get("DELTA_QLTY_MAX_TOTAL_ISSUES", "30"))
_QLTY_MAX_SMELLS = int(os.environ.get("DELTA_QLTY_MAX_SMELLS", "5"))

# ── ast-grep binary — shared module ─────────────────────────
# _internal/ast_grep_utils.py contiene TODAS las funciones de
# integración con ast-grep. analyze_refactored.py importa desde
# allí para no duplicar código con task.py.
#
# NOTA: Este script es un symlink desde otro directorio.
# __file__ resuelve a la ruta del symlink, por lo que
# sys.path[0] contiene _internal/. Pero hacemos doble-aseguro.
_THIS_DIR = str(Path(__file__).parent)
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)
# ast_grep_utils.py vive en _internal/ — agregarlo explicitamente
_AST_INTERNAL = str(Path(_THIS_DIR) / "_internal")
if _AST_INTERNAL not in sys.path:
    sys.path.insert(0, _AST_INTERNAL)

from ast_grep_utils import (
    _LANG_MAP,
    AST_GREP,
    AST_GREP_AVAILABLE,
    _ast_grep_lang,
    run_ast_grep,
    _parse_ast_grep_json,
)


# ═══════════════════════════════════════════════════════════════
# 1. QLTY METRICS — per-file summary (language-agnostic)
# ═══════════════════════════════════════════════════════════════

_ANSI_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _split_pipe(line: str) -> list[str]:
    clean = _strip_ansi(line)
    return [p.strip() for p in clean.split("|")]


# Headers may vary slightly by language; these are the common columns
_METRICS_HEADERS = [
    "name",
    "classes",
    "funcs",
    "fields",
    "cyclo",
    "complex",
    "lcom",
    "lines",
    "loc",
]
_FUNC_HEADERS = ["function", "fields", "cyclo", "cognitive", "lines", "loc"]


def run_qlty_metrics(filepath: Path) -> dict:
    """Run `qlty metrics <file>` and return file-level summary."""
    if not QLTY_AVAILABLE:
        return {}
    try:
        r = subprocess.run(
            [QLTY, "metrics", "--quiet", str(filepath)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if r.returncode != 0:
            return {}
        return _parse_metrics_table(r.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return {}


def _parse_metrics_table(stdout: str) -> dict:
    """Parse the qlty metrics table output for a single file row."""
    for line in stdout.splitlines():
        parts = _split_pipe(line)
        if len(parts) == len(_METRICS_HEADERS) and parts[0] not in (
            "name",
            "TOTAL",
            "",
        ):
            try:
                funcs = int(parts[2])
                cyclo = int(parts[4])
                return {
                    "classes": int(parts[1]),
                    "funcs": funcs,
                    "fields": int(parts[3]),
                    "cyclo": cyclo,
                    "complexity": int(parts[5]),
                    "lcom": int(parts[6]),
                    "lines": int(parts[7]),
                    "loc": int(parts[8]),
                    "avg_cc": round(cyclo / max(funcs, 1), 2),
                }
            except (ValueError, IndexError):
                continue
    return {}


def run_qlty_metrics_functions(filepath: Path) -> list[dict]:
    """Run `qlty metrics --functions <file>` and return per-function list."""
    if not QLTY_AVAILABLE:
        return []
    try:
        r = subprocess.run(
            [QLTY, "metrics", "--functions", "--quiet", str(filepath)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if r.returncode != 0:
            return []
        return _parse_func_table(r.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []


def _parse_func_table(stdout: str) -> list[dict]:
    funcs = []
    for line in stdout.splitlines():
        parts = _split_pipe(line)
        if len(parts) == len(_FUNC_HEADERS) and parts[0] not in (
            "function",
            "",
            "function",
        ):
            try:
                funcs.append(
                    {
                        "name": parts[0].strip(),
                        "fields": int(parts[1]),
                        "cyclo": int(parts[2]),
                        "cognitive": int(parts[3]),
                        "lines": int(parts[4]),
                        "loc": int(parts[5]),
                    }
                )
            except (ValueError, IndexError):
                continue
    return funcs


# ═══════════════════════════════════════════════════════════════
# 2. QLTY SMELLS — duplication & code smells (SARIF)
# ═══════════════════════════════════════════════════════════════


def run_qlty_smells(target: Path | None = None) -> list[dict]:
    """Run `qlty smells --json` and return structured results.

    JSON format gives effortMinutes, category, value — richer than SARIF.
    Falls back to SARIF if JSON fails.
    """
    if not QLTY_AVAILABLE:
        return []
    cmd = [QLTY, "smells", "--json"]
    if target:
        cmd.append(str(target))
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if r.returncode not in (0, 1):
            return []
        # Try JSON first
        results = _parse_smells_json(r.stdout)
        if results:
            return results
        # Fall back to SARIF
        sarif = _parse_sarif(r.stdout)
        if sarif:
            return sarif
        return results
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []


def _parse_sarif(sarif_text: str) -> list[dict]:
    try:
        data = json.loads(sarif_text)
    except json.JSONDecodeError:
        return []

    findings = []
    for run in data.get("runs", []):
        for result in run.get("results", []):
            rule_id = result.get("ruleId", "")
            if not rule_id.startswith("qlty:"):
                continue
            for loc in result.get("locations", []):
                phys = loc.get("physicalLocation", {})
                # Extract effort minutes if available (from properties)
                effort = 0
                props = result.get("properties", {}) or {}
                effort = props.get("effortMinutes", 0) or 0
                findings.append(
                    {
                        "rule_id": rule_id,
                        "message": result.get("message", {}).get("text", ""),
                        "level": result.get("level", "warning"),
                        "file": phys.get("artifactLocation", {}).get("uri", ""),
                        "line": phys.get("region", {}).get("startLine", 0),
                        "effort_minutes": effort,
                        "value": result.get("message", {})
                        .get("text", "")
                        .split("count = ")[-1]
                        .split(")")[0]
                        if "count =" in result.get("message", {}).get("text", "")
                        else 0,
                    }
                )
    return findings


def _parse_smells_json(stdout: str) -> list[dict]:
    """Parse qlty smells --json output (array format)."""
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    results = []
    for item in data:
        loc = item.get("location", {})
        results.append(
            {
                "rule_id": f"qlty:{item.get('ruleKey', 'unknown')}",
                "message": item.get("message", ""),
                "level": item.get("level", "LEVEL_INFO").replace("LEVEL_", "").lower(),
                "file": loc.get("path", ""),
                "line": loc.get("range", {}).get("startLine", 0),
                "category": item.get("category", ""),
                "effort_minutes": item.get("effortMinutes", 0),
                "value": item.get("value", 0),
            }
        )
    return results


# ═══════════════════════════════════════════════════════════════
# 2b. QLTY CHECK — linter suggestions (language-agnostic)
# ═══════════════════════════════════════════════════════════════


def run_qlty_check(filepaths: list[Path]) -> list[dict]:
    """Run `qlty check --level note --sarif` on given files.

    Works for any language Qlty supports (Python, TS, Rust, Go, Java, etc.).
    """
    if not QLTY_AVAILABLE or not filepaths:
        return []

    paths = [str(f) for f in filepaths if f.exists()]
    if not paths:
        return []

    try:
        r = subprocess.run(
            [QLTY, "check", "--level", "note", "--no-error", "--sarif", "--no-progress"]
            + paths,
            capture_output=True,
            text=True,
            timeout=180,
        )
        if r.returncode not in (0, 1):
            return []
        return _parse_check_sarif(r.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []


def run_qlty_fix(filepaths: list[Path]) -> bool:
    """Auto-apply fixes via `qlty check --fix` on given files.

    Returns True if successful, False otherwise.
    Works for any language with auto-fixable rules.
    """
    if not QLTY_AVAILABLE or not filepaths:
        return False

    paths = [str(f) for f in filepaths if f.exists()]
    if not paths:
        return False

    try:
        r = subprocess.run(
            [QLTY, "check", "--fix", "--no-progress"] + paths,
            capture_output=True,
            text=True,
            timeout=180,
        )
        return r.returncode in (0, 1)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def _parse_check_sarif(sarif_text: str) -> list[dict]:
    try:
        data = json.loads(sarif_text)
    except json.JSONDecodeError:
        return []

    issues = []
    for run in data.get("runs", []):
        for result in run.get("results", []):
            rule_id = result.get("ruleId", "")
            level = result.get("level", "note")
            message = result.get("message", {}).get("text", "")
            taxa = [t.get("id", "") for t in result.get("taxa", [])]
            fixes = result.get("fixes", [])
            has_fix = len(fixes) > 0
            fix_desc = fixes[0].get("description", {}).get("text", "") if fixes else ""

            for loc in result.get("locations", []):
                phys = loc.get("physicalLocation", {})
                region = phys.get("region", {})
                issues.append(
                    {
                        "rule_id": rule_id,
                        "level": level,
                        "message": message,
                        "file": phys.get("artifactLocation", {}).get("uri", ""),
                        "line": region.get("startLine", 0),
                        "col": region.get("startColumn", 0),
                        "end_line": region.get("endLine", 0),
                        "end_col": region.get("endColumn", 0),
                        "has_fix": has_fix,
                        "fix_description": fix_desc,
                        "taxonomy": taxa,
                    }
                )
    return issues


def _format_check_issue(issue: dict) -> str:
    level_icon = {"note": "💡", "warning": "⚠️", "error": "❌"}.get(issue["level"], "💡")
    fix_badge = " \033[36m[auto-fix]\033[0m" if issue["has_fix"] else ""
    tax_badge = ""
    if issue["taxonomy"]:
        tags = [t for t in issue["taxonomy"] if t]
        if tags:
            tax_badge = f" \033[90m({', '.join(tags)})\033[0m"
    return (
        f"  {level_icon} \033[1m{issue['rule_id']}\033[0m"
        f" L{issue['line']}:{issue['col']}{fix_badge}{tax_badge}\n"
        f"    {issue['message'][:120]}"
    )


# ═══════════════════════════════════════════════════════════════
# 3. COUPLING (Ce + Ca) — Python-only via AST
# ═══════════════════════════════════════════════════════════════


def coupling_metrics_python(
    filepath: Path, project_prefixes: set[str] | None = None
) -> dict:
    """Ce = external imports; Ca = files importing this module.

    Only works for Python files. Returns empty metrics for other languages.
    """
    if filepath.suffix != ".py":
        return {"ce": 0, "ca": 0, "imports": [], "imported_by": []}

    import ast  # noqa: F811 — lazy import for non-Python files

    try:
        tree = ast.parse(filepath.read_text())
    except SyntaxError:
        return {"ce": 0, "ca": 0, "imports": [], "imported_by": []}

    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module.split(".")[0])

    prefixes = project_prefixes or {
        "cognitive_v2",
        "mcp",
        "fastmcp",
        "helpers",
        "session",
    }
    external = {i for i in imports if i not in prefixes}

    # Ca: grep project for imports of this module
    imported_by: list[str] = []
    try:
        project_root = _find_project_root(filepath)
        rel = filepath.relative_to(project_root)
        mod_path = str(rel).replace("/", ".").replace(".py", "")
        if mod_path:
            r = subprocess.run(
                ["grep", "-rl", mod_path, str(project_root), "--include=*.py"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            imported_by = [
                line
                for line in r.stdout.strip().split("\n")
                if line and line != str(filepath)
            ]
    except (ValueError, Exception):
        pass

    return {
        "ce": len(external),
        "ca": len(imported_by),
        "imports": sorted(external),
        "imported_by": imported_by,
    }


def _find_project_root(start: Path) -> Path:
    """Walk up to find project root (contains .git or .cognitive)."""
    for parent in [start] + list(start.parents):
        if (parent / ".git").exists() or (parent / ".cognitive").exists():
            return parent
    return start.parents[-1] if list(start.parents) else start


# ═══════════════════════════════════════════════════════════════
# 4. AST-GREP DEEP ANALYSIS — structural queries on findings
# ═══════════════════════════════════════════════════════════════

# Map file extension → ast-grep language name
_LANG_MAP = {
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


def _ast_grep_lang(filepath: Path) -> str | None:
    """Map file extension to ast-grep language name."""
    return _LANG_MAP.get(filepath.suffix.lower())


def run_ast_grep(pattern: str, filepath: Path, lang: str | None = None) -> list[dict]:
    """Run ast-grep structural search and return structured results.

    Returns list of {line, col, text, message} for each match.
    """
    if not AST_GREP_AVAILABLE:
        return []
    if lang is None:
        lang = _ast_grep_lang(filepath)
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
        results.append(
            {
                "line": start.get("line", 0) + 1,  # ast-grep is 0-indexed
                "col": start.get("col", 0) + 1,
                "text": m.get("text", "").strip(),
                "message": m.get("message", ""),
            }
        )
    return results


# ── Deep analysis: qlty findings → ast-grep queries ──────────

# Patterns for deepening specific qlty/ruff/bandit findings
_DEEP_PATTERNS: dict[str, list[dict]] = {
    # ── B110: try/except/pass → classify exception type ──
    "B110": [
        {
            "id": "bare-except",
            "pattern": "except:\n  pass",
            "label": "except: pass (traga TODO)",
            "severity": "high",
        },
        {
            "id": "except-exception",
            "pattern": "except Exception:\n  pass",
            "label": "except Exception: pass (genérico)",
            "severity": "medium",
        },
        {
            "id": "except-typed",
            "pattern": "except $E:\n  pass",
            "label": "except <Tipo>: pass (específico)",
            "severity": "low",
        },
    ],
    # ── B1xx: hardcoded creds, assert, exec, etc. ──
    "B101": [
        {
            "id": "assert-used",
            "pattern": "assert $X",
            "label": "assert — se desactiva con -O (producción)",
            "severity": "medium",
        },
    ],
    "B102": [
        {
            "id": "exec-used",
            "pattern": "exec($X)",
            "label": "exec() — ejecución dinámica de código",
            "severity": "high",
        },
    ],
    "B105": [
        {
            "id": "hardcoded-password",
            "pattern": "$$$ = $$$password$$$",
            "label": "posible hardcoded password/secret",
            "severity": "high",
        },
    ],
    # ── B6xx: injection variants ──
    "B602": [
        {
            "id": "subprocess-shell",
            "pattern": "subprocess.call($$$, shell=True)",
            "label": "subprocess.call() con shell=True",
            "severity": "high",
        },
        {
            "id": "subprocess-popen",
            "pattern": "subprocess.Popen($$$, shell=True)",
            "label": "subprocess.Popen() con shell=True",
            "severity": "high",
        },
        {
            "id": "os-system",
            "pattern": "os.system($X)",
            "label": "os.system() — shell injection",
            "severity": "high",
        },
    ],
    "B608": [
        {
            "id": "sql-format",
            "pattern": "$$$.execute($$$ % $$$)",
            "label": "SQL query construida con % (format) — SQLi",
            "severity": "high",
        },
        {
            "id": "sql-fstring",
            "pattern": "$$$.execute(f$$$)",
            "label": "SQL query con f-string — SQLi",
            "severity": "high",
        },
    ],
    # ── E402: import not at top level ──
    "E402": [
        {
            "id": "import-in-function",
            "pattern": "def $NAME($$$):\n  $$$import $$$",
            "label": "import dentro de función",
            "severity": "medium",
        },
        {
            "id": "import-in-conditional",
            "pattern": "if $$$:\n  import $$$",
            "label": "import condicional",
            "severity": "low",
        },
        {
            "id": "import-in-try",
            "pattern": "try:\n  import $$$",
            "label": "import dentro de try",
            "severity": "low",
        },
    ],
    # ── F401: unused imports ──
    "F401": [
        {
            "id": "unused-import",
            "pattern": "import $X",
            "label": "import no usado",
            "severity": "low",
        },
        {
            "id": "unused-from-import",
            "pattern": "from $X import $Y",
            "label": "from-import no usado",
            "severity": "low",
        },
    ],
    # ── F541: f-string without placeholders ──
    "F541": [
        {
            "id": "bare-fstring",
            "pattern": "f'$TEXT'",
            "label": "f-string sin placeholder (debug leftover)",
            "severity": "low",
        },
        {
            "id": "bare-fstring-double",
            "pattern": 'f"$TEXT"',
            "label": "f-string sin placeholder (debug leftover)",
            "severity": "low",
        },
    ],
    # ── High complexity → nested structures ──
    "high-cyclo": [
        {
            "id": "nested-if",
            "pattern": "if $$$:\n  if $$$:",
            "label": "if anidado",
            "severity": "medium",
        },
        {
            "id": "nested-for",
            "pattern": "for $$$:\n  for $$$:",
            "label": "for anidado",
            "severity": "medium",
        },
        {
            "id": "nested-while",
            "pattern": "while $$$:\n  while $$$:",
            "label": "while anidado",
            "severity": "medium",
        },
        {
            "id": "many-params",
            "pattern": "def $NAME($P1, $P2, $P3, $P4, $P5, $$$):",
            "label": "función con >5 parámetros",
            "severity": "medium",
        },
        {
            "id": "no-docstring",
            "pattern": "def $NAME($$$):\n  $$$",
            "label": "función sin docstring ni comentario",
            "severity": "low",
        },
    ],
    # ── Generic: dangerous patterns (Python) ──
    "danger": [
        {
            "id": "eval-call",
            "pattern": "eval($X)",
            "label": "eval() detectado",
            "severity": "high",
        },
        {
            "id": "exec-call",
            "pattern": "exec($X)",
            "label": "exec() detectado",
            "severity": "high",
        },
        {
            "id": "shell-true",
            "pattern": "shell=True",
            "label": "shell=True en subprocess",
            "severity": "high",
        },
        {
            "id": "pickle-load",
            "pattern": "pickle.load($X)",
            "label": "pickle.load() inseguro",
            "severity": "high",
        },
        {
            "id": "mktemp-danger",
            "pattern": "tempfile.mktemp($X)",
            "label": "mktemp() inseguro (race condition)",
            "severity": "high",
        },
        {
            "id": "requests-verify-false",
            "pattern": "verify=False",
            "label": "SSL verification disabled",
            "severity": "high",
        },
        {
            "id": "yaml-load",
            "pattern": "yaml.load($X)",
            "label": "yaml.load() sin SafeLoader (inyección)",
            "severity": "high",
        },
    ],
    # ── Patterns for TypeScript / JavaScript ──
    "ts-js": [
        {
            "id": "console-log",
            "pattern": "console.log($X)",
            "label": "console.log en producción",
            "severity": "low",
        },
        {
            "id": "debugger-stmt",
            "pattern": "debugger",
            "label": "debugger statement en producción",
            "severity": "medium",
        },
        {
            "id": "inner-html",
            "pattern": "$$$.innerHTML = $X",
            "label": "innerHTML assignment (XSS)",
            "severity": "high",
        },
        {
            "id": "any-type",
            "pattern": "const $X: any = $V",
            "label": "tipo any sin restricción",
            "severity": "low",
        },
    ],
    # ── Unnecessary code (always run, not triggered by qlty) ──
    "unnecessary-code": [
        {
            "id": "empty-function",
            "pattern": "def $NAME($$$):\n  pass",
            "label": "función vacía — implementar o eliminar",
            "severity": "medium",
            "lang": "python",
        },
        {
            "id": "empty-class",
            "pattern": "class $NAME:\n  pass",
            "label": "clase vacía — eliminar o implementar",
            "severity": "medium",
            "lang": "python",
        },
        {
            "id": "bare-fstring",
            "pattern": "f'$TEXT'",
            "label": "f-string sin placeholder — usar string normal",
            "severity": "low",
        },
        {
            "id": "bare-fstring-double",
            "pattern": 'f"$TEXT"',
            "label": "f-string sin placeholder — usar string normal",
            "severity": "low",
        },
        {
            "id": "console-log",
            "pattern": "console.log($X)",
            "label": "console.log en producción — eliminar o usar logger",
            "severity": "low",
        },
        {
            "id": "debugger-stmt",
            "pattern": "debugger",
            "label": "debugger statement en producción — eliminar",
            "severity": "medium",
        },
        {
            "id": "pass-only-except",
            "pattern": "except:\n  pass",
            "label": "except vacío — traga errores silenciosamente",
            "severity": "high",
        },
        {
            "id": "todo-without-ticket",
            "pattern": "# TODO",
            "label": "TODO sin ticket asociado — crear issue o resolver",
            "severity": "low",
        },
    ],
    # ── Production bugs (always run, not triggered by qlty) ──
    "production-bugs": [
        # Python
        {
            "id": "mutable-default-list",
            "pattern": "def $NAME($$$=[]):",
            "label": "lista mutable como default — mutación entre llamadas",
            "severity": "critical",
            "lang": "python",
        },
        {
            "id": "mutable-default-dict",
            "pattern": "def $NAME($$$={}):",
            "label": "dict mutable como default — mutación entre llamadas",
            "severity": "critical",
            "lang": "python",
        },
        {
            "id": "return-none-in-except",
            "pattern": "except:\n  return None",
            "label": "except: return None — oculta la causa del error",
            "severity": "high",
            "lang": "python",
        },
        {
            "id": "no-timeout-requests",
            "pattern": "requests.$METHOD($$$)",
            "label": "requests.$METHOD() sin timeout — puede colgarse en producción",
            "severity": "high",
            "lang": "python",
        },
        {
            "id": "eval-call",
            "pattern": "eval($X)",
            "label": "eval() — ejecución dinámica de código",
            "severity": "critical",
            "lang": "python",
        },
        {
            "id": "pickle-load",
            "pattern": "pickle.load($X)",
            "label": "pickle.load() — deserialización insegura",
            "severity": "critical",
            "lang": "python",
        },
        # Rust
        {
            "id": "unwrap-used",
            "pattern": "$$$.unwrap()",
            "label": "unwrap() — panic en producción si hay None/Err",
            "severity": "high",
            "lang": "rust",
        },
        {
            "id": "todo-macro",
            "pattern": "todo!()",
            "label": "todo!() — panic en runtime si se ejecuta",
            "severity": "high",
            "lang": "rust",
        },
        # TypeScript/JavaScript
        {
            "id": "non-null-assert",
            "pattern": "$X!",
            "label": "Non-null assertion (!) — puede explotar en runtime",
            "severity": "medium",
            "lang": "typescript",
        },
        {
            "id": "loose-equality",
            "pattern": "$X == $V",
            "label": "== en vez de === — comparación débil",
            "severity": "medium",
            "lang": "javascript",
        },
        # Go
        {
            "id": "panic-call",
            "pattern": "panic($X)",
            "label": "panic() — puede terminar el programa inesperadamente",
            "severity": "high",
            "lang": "go",
        },
        # Java
        {
            "id": "null-return",
            "pattern": "return null;",
            "label": "return null — puede causar NullPointerException",
            "severity": "high",
            "lang": "java",
        },
    ],
}


def _get_deep_patterns_for_rule(rule_id: str) -> list[dict]:
    """Return ast-grep patterns relevant to a given qlty rule ID."""
    for key, patterns in _DEEP_PATTERNS.items():
        if key in rule_id:
            return patterns
    return []


def deep_analyze_checks(checks: list[dict], filepath: Path, cyclo: int = 0) -> dict:
    """Correlate qlty findings with ast-grep structural queries.

    For each unique rule type found by qlty, run ast-grep patterns
    that deepen the analysis (classify exception types, find nested
    structures, locate exact unused imports, etc.).
    """
    if not AST_GREP_AVAILABLE:
        return {}

    lang = _ast_grep_lang(filepath)
    if not lang:
        return {}

    findings = []

    # 1. Deep patterns matched to specific qlty rules (only if checks exist)
    if checks:
        rule_types = set(c["rule_id"] for c in checks)
        for rule_id in rule_types:
            patterns = _get_deep_patterns_for_rule(rule_id)
            for pat in patterns:
                matches = run_ast_grep(pat["pattern"], filepath, lang)
                if matches:
                    findings.append(
                        {
                            "trigger_rule": rule_id,
                            "pattern_id": pat["id"],
                            "label": pat["label"],
                            "severity": pat["severity"],
                            "matches": matches[:10],
                            "count": len(matches),
                        }
                    )

    # 2. High-cyclo patterns (triggered by metrics, not by a specific rule)
    if cyclo > 20:
        for pat in _DEEP_PATTERNS["high-cyclo"]:
            matches = run_ast_grep(pat["pattern"], filepath, lang)
            if matches:
                findings.append(
                    {
                        "trigger_rule": f"cyclo={cyclo}",
                        "pattern_id": pat["id"],
                        "label": pat["label"],
                        "severity": pat["severity"],
                        "matches": matches[:10],
                        "count": len(matches),
                    }
                )

    # 3. Danger patterns (triggered by security issues)
    has_security = checks and any(
        "B1" in c["rule_id"] or "B6" in c["rule_id"] for c in checks
    )
    if has_security:
        for pat in _DEEP_PATTERNS["danger"]:
            matches = run_ast_grep(pat["pattern"], filepath, lang)
            if matches:
                findings.append(
                    {
                        "trigger_rule": "security-context",
                        "pattern_id": pat["id"],
                        "label": pat["label"],
                        "severity": pat["severity"],
                        "matches": matches[:10],
                        "count": len(matches),
                    }
                )

    # 4. TypeScript/JavaScript patterns (triggered by file type)
    if lang in ("typescript", "tsx", "javascript", "jsx"):
        for pat in _DEEP_PATTERNS["ts-js"]:
            matches = run_ast_grep(pat["pattern"], filepath, lang)
            if matches:
                findings.append(
                    {
                        "trigger_rule": f"lang={lang}",
                        "pattern_id": pat["id"],
                        "label": pat["label"],
                        "severity": pat["severity"],
                        "matches": matches[:10],
                        "count": len(matches),
                    }
                )

    # 5. Production-bug patterns (always run, any language)
    for pat in _DEEP_PATTERNS.get("production-bugs", []):
        pat_lang = pat.get("lang")
        if pat_lang and pat_lang != lang:
            continue  # skip patterns for other languages
        matches = run_ast_grep(pat["pattern"], filepath, lang)
        if matches:
            findings.append(
                {
                    "trigger_rule": "production-bug",
                    "pattern_id": pat["id"],
                    "label": pat["label"],
                    "severity": pat["severity"],
                    "matches": matches[:10],
                    "count": len(matches),
                }
            )

    # 6. Unnecessary-code patterns (always run, any language)
    for pat in _DEEP_PATTERNS.get("unnecessary-code", []):
        pat_lang = pat.get("lang")
        if pat_lang and pat_lang != lang:
            continue
        matches = run_ast_grep(pat["pattern"], filepath, lang)
        if matches:
            findings.append(
                {
                    "trigger_rule": "unnecessary-code",
                    "pattern_id": pat["id"],
                    "label": pat["label"],
                    "severity": pat["severity"],
                    "matches": matches[:10],
                    "count": len(matches),
                }
            )

    return {"deep_findings": findings, "total_deep": len(findings)}


# ═══════════════════════════════════════════════════════════════
# 5. CROSS-REFERENCE LAYER — correlaciona datos entre plugins
# ═══════════════════════════════════════════════════════════════


def classify_risk(
    checks: list[dict], cyclo: int, filepath: Path | None = None
) -> list[dict]:
    """Clasifica issues en categorías de riesgo y genera hallazgos compuestos.

    Cruces que implementa:
      - Exception swallow + cyclo alta      → bug probable
      - Inyección (B6xx)                    → injection risk
      - Código muerto + cyclo alto          → variable leak
      - Imports peligrosos + muertos        → refactor incompleto
      - Nombres sin definir + cyclo alto    → bug probable (typo o falta import)
      - Múltiples B1xx                      → systemic security debt
      - E402 + cyclo alto                   → spaghetti code (imports mezclados)
      - Secrets + código peligroso          → exploit chain (gitleaks + bandit)
    """
    findings = []

    # Agrupar por tipo
    cats = defaultdict(list)
    for c in checks:
        rid = c["rule_id"]
        if "B110" in rid:
            cats["exception_swallow"].append(c)
        elif rid.startswith("B6"):
            cats["injection"].append(c)
        elif rid in ("F401", "F841"):
            cats["dead_code"].append(c)
        elif rid.startswith("B3") or rid.startswith("B4"):
            cats["dangerous_import"].append(c)
        elif rid.startswith("B5"):
            cats["ssl_crypto"].append(c)
        elif rid.startswith("B7"):
            cats["xss_template"].append(c)
        elif rid.startswith("B1"):
            cats["security_debt"].append(c)
        elif rid == "F821":
            cats["undefined_name"].append(c)
        elif "E402" in rid:
            cats["import_not_top"].append(c)
        elif "gitleaks" in rid.lower():
            cats["secret"].append(c)

    # CRUCE 1: Exception swallow + cyclo alto
    if cats["exception_swallow"] and cyclo > 30:
        findings.append(
            {
                "type": "bug",
                "severity": "critical",
                "title": f"{len(cats['exception_swallow'])} excepciones silenciadas en cyclo={cyclo}",
                "description": "try/except/pass en código muy complejo — probable bug real",
                "count": len(cats["exception_swallow"]),
            }
        )
    elif cats["exception_swallow"] and cyclo > 10:
        findings.append(
            {
                "type": "bug",
                "severity": "warning",
                "title": f"{len(cats['exception_swallow'])} excepciones silenciadas en cyclo={cyclo}",
                "description": "try/except/pass en código moderadamente complejo — revisar",
                "count": len(cats["exception_swallow"]),
            }
        )

    # CRUCE 2: Injection (B6xx)
    if cats["injection"]:
        findings.append(
            {
                "type": "injection",
                "severity": "critical",
                "title": f"{len(cats['injection'])} posibles inyecciones (shell/sql/command)",
                "description": "subprocess/command/sql injection detectado",
                "count": len(cats["injection"]),
            }
        )

    # CRUCE 3: Dead code + complejidad → variable leak
    if cats["dead_code"] and cyclo > 15:
        findings.append(
            {
                "type": "var_leak",
                "severity": "warning",
                "title": f"{len(cats['dead_code'])} variables/imports sin usar en cyclo={cyclo}",
                "description": "código muerto en función compleja — posible fuga de scope",
                "count": len(cats["dead_code"]),
            }
        )

    # CRUCE 4: Dangerous import + dead code = refactor incompleto
    if cats["dangerous_import"] and cats["dead_code"]:
        di_rules = ", ".join(c["rule_id"] for c in cats["dangerous_import"])
        findings.append(
            {
                "type": "refactor",
                "severity": "warning",
                "title": f"Imports peligrosos ({di_rules}) + código muerto",
                "description": "Quedaron imports peligrosos de refactors incompletos",
                "count": len(cats["dangerous_import"]),
            }
        )

    # CRUCE 5: XSS / template injection
    if cats["xss_template"]:
        findings.append(
            {
                "type": "xss",
                "severity": "critical",
                "title": f"{len(cats['xss_template'])} posibles XSS/template injection",
                "description": "Plantillas o outputs sin sanitizar",
                "count": len(cats["xss_template"]),
            }
        )

    # CRUCE 6: Nombres sin definir + cyclo alto → bug probable
    if cats["undefined_name"]:
        findings.append(
            {
                "type": "bug",
                "severity": "critical",
                "title": f"{len(cats['undefined_name'])} nombres usados sin definir",
                "description": "Variables/funciones referenciadas pero no definidas en scope — typo o import faltante",
                "count": len(cats["undefined_name"]),
            }
        )

    # CRUCE 7: Múltiples B1xx → systemic security debt
    if len(cats["security_debt"]) > 5:
        rules = ", ".join(set(c["rule_id"] for c in cats["security_debt"]))
        findings.append(
            {
                "type": "security_debt",
                "severity": "warning",
                "title": f"{len(cats['security_debt'])} issues de seguridad sistémicos ({rules})",
                "description": "Múltiples hallazgos de seguridad — patrón sistémico, no casos aislados",
                "count": len(cats["security_debt"]),
            }
        )

    # CRUCE 8: E402 + cyclo alto → spaghetti code
    if cats["import_not_top"] and cyclo > 30:
        findings.append(
            {
                "type": "spaghetti",
                "severity": "warning",
                "title": f"{len(cats['import_not_top'])} imports fuera de lugar en cyclo={cyclo}",
                "description": "Imports mezclados con lógica en archivo muy complejo — difícil de seguir",
                "count": len(cats["import_not_top"]),
            }
        )

    # CRUCE 9: Secrets + bandit dangerous → exploit chain
    if cats["secret"] and (cats["injection"] or cats["dangerous_import"]):
        findings.append(
            {
                "type": "exploit_chain",
                "severity": "critical",
                "title": f"Secrets ({len(cats['secret'])}) + código peligroso en el mismo archivo",
                "description": "Combinación de secretos y código vulnerable — cadena de explotación posible",
                "count": len(cats["secret"]),
            }
        )

    return findings


# ═══════════════════════════════════════════════════════════════
# 6. QUALITY METRICS — expanded quality model
# ═══════════════════════════════════════════════════════════════


# Maintainability ratings based on Tech Debt Ratio (inspired by SQALE / Qlty)
def _maintainability_rating(ratio: float) -> str:
    """A = 0-5%, B = 5-10%, C = 10-20%, D = 20-50%, E = 50-100%, F > 100%"""
    if ratio <= 0.05:
        return "A"
    if ratio <= 0.10:
        return "B"
    if ratio <= 0.20:
        return "C"
    if ratio <= 0.50:
        return "D"
    if ratio <= 1.00:
        return "E"
    return "F"


def _security_rating(high: int, medium: int, low: int) -> str:
    """Simple security rating based on issue severity."""
    if high > 0:
        return "E"
    if medium > 5:
        return "D"
    if medium > 0:
        return "C"
    if low > 10:
        return "B"
    return "A"


def _coverage_rating(pct: float) -> str:
    if pct >= 80:
        return "A"
    if pct >= 60:
        return "B"
    if pct >= 40:
        return "C"
    if pct >= 20:
        return "D"
    return "E"


# Estimated implementation time: 1 LOC ≈ 1 minute (standard industry heuristic)
_ESTIMATED_MINUTES_PER_LOC = 1.0


def compute_quality_metrics(
    metrics: dict,
    smells: list[dict],
    checks: list[dict],
    nloc: int,
    lines: int,
    comment_lines: int,
) -> dict:
    """Compute ALL quality metrics from qlty raw data.

    Returns a flat dict with keys matching the standard quality model:
      Size, Complexity, Maintainability, Security, Issues, Duplication, Coverage.
    """
    # ── SIZE ──
    classes = metrics.get("classes", 0)
    fields = metrics.get("fields", 0)
    funcs = metrics.get("funcs", 0)
    cyclo = metrics.get("cyclo", 0)
    complexity_n = metrics.get("complexity", 0)  # cognitive complexity

    # ── DUPLICATION — from smells ──
    dup_smells = [
        s
        for s in smells
        if "duplic" in s.get("rule_id", "").lower()
        or s.get("category", "") == "CATEGORY_DUPLICATION"
    ]
    if not dup_smells:
        dup_smells = [s for s in smells if "duplic" in s.get("message", "").lower()]
    dup_blocks = len(dup_smells)
    dup_lines = sum(s.get("value", 0) for s in dup_smells)
    dup_density = round(dup_lines / max(nloc, 1), 4)

    # ── COMPLEXITY ──
    cyclo_density = round(cyclo / max(nloc, 1), 4)
    complexity_density = round(complexity_n / max(nloc, 1), 4)

    # ── MAINTAINABILITY — from smells effort ──
    total_smells = len(smells)
    tech_debt_minutes = sum(
        s.get("effort_minutes", 0) or s.get("effortMinutes", 0) for s in smells
    )
    # Fallback: estimate ~30min per smell
    if tech_debt_minutes == 0 and total_smells > 0:
        tech_debt_minutes = total_smells * 30
    est_impl_minutes = nloc * _ESTIMATED_MINUTES_PER_LOC
    tech_debt_ratio = round(tech_debt_minutes / max(est_impl_minutes, 1), 4)
    maintainability_rating = _maintainability_rating(tech_debt_ratio)

    # ── SECURITY ──
    def _rid_base(rid: str) -> str:
        return rid.split(":")[-1] if ":" in rid else rid

    sec_high = sum(
        1 for c in checks if _rid_base(c.get("rule_id", "")).startswith("B6")
    )
    sec_medium = sum(
        1
        for c in checks
        if _rid_base(c.get("rule_id", "")).startswith(("B1", "B2", "B3"))
    )
    sec_low = sum(
        1
        for c in checks
        if _rid_base(c.get("rule_id", "")).startswith(("B4", "B5", "B7"))
    )
    # Count gitleaks as high
    sec_high += sum(1 for c in checks if "gitleaks" in c.get("rule_id", "").lower())
    total_sec = sec_high + sec_medium + sec_low
    security_rating = _security_rating(sec_high, sec_medium, sec_low)

    # ── ISSUES ──
    total_issues = len(checks)
    issues_high = sum(1 for c in checks if c.get("level") == "error")
    issues_medium = sum(1 for c in checks if c.get("level") == "warning")
    issues_low = sum(1 for c in checks if c.get("level") in ("note", "low"))
    fixable = sum(1 for c in checks if c.get("has_fix"))

    # ── COVERAGE (not available from qlty) ──
    coverage_pct = None

    return {
        # ── Size ──
        "classes": classes,
        "fields": fields,
        "functions": funcs,
        "code_files": 1,
        "lines": lines,
        "nloc": nloc,
        "comment_lines": comment_lines,
        "comments_density": round(comment_lines / max(lines, 1), 4),
        # ── Duplication ──
        "dup_blocks": dup_blocks,
        "dup_lines": dup_lines,
        "dup_density": dup_density,
        # ── Complexity ──
        "cyclomatic_complexity": cyclo,
        "cognitive_complexity": complexity_n,
        "cyclo_density": cyclo_density,
        "complexity_density": complexity_density,
        # ── Maintainability ──
        "smells": total_smells,
        "tech_debt_minutes": tech_debt_minutes,
        "tech_debt_hours": round(tech_debt_minutes / 60, 1),
        "tech_debt_ratio": tech_debt_ratio,
        "maintainability_rating": maintainability_rating,
        # ── Security ──
        "security_issues": total_sec,
        "security_high": sec_high,
        "security_medium": sec_medium,
        "security_low": sec_low,
        "security_rating": security_rating,
        # ── Issues ──
        "issues": total_issues,
        "issues_high": issues_high,
        "issues_medium": issues_medium,
        "issues_low": issues_low,
        "fixable_issues": fixable,
        # ── Coverage ──
        "coverage_pct": coverage_pct,
        "coverage_rating": None
        if coverage_pct is None
        else _coverage_rating(coverage_pct),
    }


def format_rating(rating: str) -> str:
    """Color-code letter ratings."""
    colors = {
        "A": "\033[32m",
        "B": "\033[32m",
        "C": "\033[33m",
        "D": "\033[33m",
        "E": "\033[31m",
        "F": "\033[31m",
    }
    c = colors.get(rating, "\033[0m")
    return f"{c}{rating}\033[0m"


def format_minutes(m: int) -> str:
    """Format minutes as human-readable."""
    if m < 60:
        return f"{m}min"
    hours = m / 60
    if hours < 24:
        return f"{hours:.1f}h"
    days = hours / 8
    return f"{days:.1f}d"


# ═══════════════════════════════════════════════════════════════
# 7. REPORT — colored helpers
# ═══════════════════════════════════════════════════════════════


def c_lower_better(label: str, v: float, good_max: float, mid_max: float) -> str:
    if v <= good_max:
        return f"\033[32m{label}={v}\033[0m"
    if v <= mid_max:
        return f"\033[33m{label}={v}\033[0m"
    return f"\033[31m{label}={v}\033[0m"


# ═══════════════════════════════════════════════════════════════
# 5. MAIN
# ═══════════════════════════════════════════════════════════════


def _evaluate_quality_gates(
    all_checks: list[dict],
    total_smells: int,
    files_results: list[dict],
) -> tuple[int, list[str]]:
    """Evaluate quality metrics against configurable thresholds.

    Returns (exit_code, reasons) where:
    - exit_code=0 if all gates pass
    - exit_code=1 if any gate fails
    - reasons is a list of failure descriptions for reporting.
    """
    reasons: list[str] = []

    # Count by severity across all checks
    errors = sum(1 for c in all_checks if c.get("level") == "error")
    warnings = sum(1 for c in all_checks if c.get("level") == "warning")

    # Aggregate security issues across files
    sec_high = 0
    for f in files_results:
        q = f.get("quality", {}) or {}
        sec_high += q.get("security_high", 0)

    total_issues = len(all_checks)

    # ── Evaluate each gate ──
    if errors > _QLTY_MAX_ERRORS:
        reasons.append(f"❌ Errors: {errors} > max ({_QLTY_MAX_ERRORS})")
    if sec_high > _QLTY_MAX_SECURITY_HIGH:
        reasons.append(
            f"🔴 Security HIGH: {sec_high} > max ({_QLTY_MAX_SECURITY_HIGH})"
        )
    if warnings > _QLTY_MAX_WARNINGS:
        reasons.append(f"⚠️  Warnings: {warnings} > max ({_QLTY_MAX_WARNINGS})")
    if total_issues > _QLTY_MAX_TOTAL_ISSUES:
        reasons.append(
            f"📊 Total issues: {total_issues} > max ({_QLTY_MAX_TOTAL_ISSUES})"
        )
    if total_smells > _QLTY_MAX_SMELLS:
        reasons.append(f"👃 Code smells: {total_smells} > max ({_QLTY_MAX_SMELLS})")

    return (1 if reasons else 0, reasons)


def main():
    json_mode = False
    fix_mode = False

    args = sys.argv[1:]
    if "--json" in args:
        json_mode = True
        args.remove("--json")
    if "--fix" in args:
        fix_mode = True
        args.remove("--fix")

    files_to_analyze = [Path(a) for a in args] if args else []

    if not files_to_analyze:
        print(
            "Uso: python3 analyze_refactored.py [--json] [--fix] <file1> [file2 ...]",
            file=sys.stderr,
        )
        sys.exit(1)

    # Filter to existing files
    files_to_analyze = [f for f in files_to_analyze if f.exists()]
    if not files_to_analyze:
        print("❌ Ningún archivo válido encontrado.", file=sys.stderr)
        sys.exit(1)

    # ── Auto-fix mode: apply fixes before analysis ──
    if fix_mode:
        fix_msg = f"  🔧 Aplicando auto-fixes en {len(files_to_analyze)} archivos..."
        if json_mode:
            print(fix_msg, file=sys.stderr)
        else:
            print(fix_msg)
        ok = run_qlty_fix(files_to_analyze)
        status = "✅" if ok else "⚠️"
        result_msg = f"  {status} Auto-fixes {'aplicados' if ok else 'fallaron (puede no haber herramientas instaladas)'}."
        if json_mode:
            print(result_msg, file=sys.stderr)
        else:
            print(result_msg)

    if json_mode:
        return _main_json(files_to_analyze)

    return _main_terminal(files_to_analyze)


def _main_terminal(files_to_analyze: list[Path]) -> None:
    print("=" * 88)
    print("  \033[1m📊 Qlty — ANÁLISIS MULTILENGUAJE\033[0m")
    print(
        f"  Qlty: {'\033[32m✓\033[0m' if QLTY_AVAILABLE else '\033[31m✗\033[0m'}  |  "
        f"Archivos: {len(files_to_analyze)}  |  "
        f"Lenguajes: {len({f.suffix for f in files_to_analyze})} detectados"
    )
    print("=" * 88)

    all_smells = run_qlty_smells() if QLTY_AVAILABLE else []
    all_checks = run_qlty_check(files_to_analyze) if QLTY_AVAILABLE else []

    total_loc = 0
    summary_rows: list[dict] = []

    for fp in files_to_analyze:
        metrics = run_qlty_metrics(fp)
        funcs = run_qlty_metrics_functions(fp)

        cyclo = metrics.get("cyclo", 0)
        complexity = metrics.get("complexity", 0)
        lcom = metrics.get("lcom", 0)
        loc = metrics.get("loc", 0)
        func_count = metrics.get("funcs", 0)
        raw_loc = metrics.get("lines", 0) or len(
            [l for l in fp.read_text().splitlines() if l.strip()]
        )
        total_loc += raw_loc

        # Coupling: only for Python
        coupl = (
            coupling_metrics_python(fp)
            if fp.suffix == ".py"
            else {"ce": 0, "ca": 0, "imports": [], "imported_by": []}
        )

        # File matching for smells/checks
        fp_str = str(fp)
        rel_path = (
            str(fp.relative_to(_find_project_root(fp)))
            if _find_project_root(fp)
            else fp_str
        )

        file_smells = [
            s
            for s in all_smells
            if rel_path in s.get("file", "") or fp_str in s.get("file", "")
        ]
        file_checks = [
            c
            for c in all_checks
            if c.get("file", "") in (fp_str, rel_path)
            or (rel_path and c.get("file", "").endswith(rel_path))
        ]

        # ── Per-file display ──
        lang_tag = f"\033[90m{fp.suffix or '?'}\033[0m"
        print(f"\n{'─' * 88}")
        print(f"  \033[1m📄 {fp.name}\033[0m  {lang_tag}")
        print(f"{'─' * 88}")

        print(
            f"  Qlty NLOC: {loc}  |  "
            f"{c_lower_better('Cyclo', cyclo, 30, 60)}  |  "
            f"{c_lower_better('Cog Cpx', complexity, 20, 50)}  |  "
            f"Funcs: {func_count}  |  "
            f"{c_lower_better('LCOM', lcom, 5, 20)}"
        )

        if funcs:
            print(f"  Functions ({len(funcs)}):")
            for f in funcs[:6]:
                cc_s = c_lower_better("CC", f["cyclo"], 5, 10)
                print(
                    f"    {f['name']:<42} NLOC={f['loc']:<3}  {cc_s}  "
                    f"cog={f['cognitive']}  lines={f['lines']}"
                )
            if len(funcs) > 6:
                print(f"    ... +{len(funcs) - 6} more")

        for f in funcs:
            if f["cyclo"] > 15:
                print(
                    f"    \033[33m⚠️  HIGH CC: {f['name']} — CC={f['cyclo']} > 15\033[0m"
                )

        if fp.suffix == ".py":
            print(
                f"  Coupling: {c_lower_better('Ce', coupl['ce'], 5, 10)}  "
                f"{c_lower_better('Ca', coupl['ca'], 5, 10)}"
            )

        # Smells
        if file_smells:
            for s in file_smells[:5]:
                rid = s["rule_id"].replace("qlty:", "")
                print(f"  \033[33m🔍 {rid}\033[0m L{s['line']}: {s['message'][:100]}")
            if len(file_smells) > 5:
                print(f"    ... +{len(file_smells) - 5} more")

        # Check issues
        if file_checks:
            warnings = [c for c in file_checks if c["level"] == "warning"]
            errors = [c for c in file_checks if c["level"] == "error"]
            [c for c in file_checks if c["level"] == "note"]
            n_fix = sum(1 for c in file_checks if c["has_fix"])
            print(
                f"  \033[1m🔬 Qlty check:\033[0m {len(file_checks)} issues"
                f" ({len(errors)} errors, {len(warnings)} warnings)"
                + (f", {n_fix} auto-fixable" if n_fix else "")
            )
            for c in file_checks[:6]:
                print(_format_check_issue(c))
            if len(file_checks) > 6:
                print(f"    ... +{len(file_checks) - 6} more")

        # ── Cross-reference findings ──
        cross_findings = classify_risk(file_checks, cyclo, fp)
        if cross_findings:
            print("  \033[1m🔗 CRUCES DE DATOS:\033[0m")
            for cf in cross_findings:
                sv = cf["severity"]
                icon = {"critical": "🔴", "warning": "🟡", "info": "🔵"}.get(sv, "⚪")
                print(
                    f"    {icon} \033[1m{cf['type'].upper()}\033[0m [{sv}] {cf['title']}"
                )
                print(f"      {cf['description']}")

        # ── Deep analysis (ast-grep) ──
        deep = deep_analyze_checks(file_checks, fp, cyclo=cyclo)
        if deep.get("deep_findings"):
            print("  \033[1m🔎 AST-GREP DEEP:\033[0m")
            for df in deep["deep_findings"]:
                sv = df["severity"]
                icon = {"high": "🔴", "medium": "🟡", "low": "🔵", "info": "ℹ️"}.get(
                    sv, "⚪"
                )
                print(
                    f"    {icon} {df['label']} \033[90m(x{df['count']})\033[0m"
                    f" \033[2m→ trigger: {df['trigger_rule']}\033[0m"
                )
                for m in df["matches"][:3]:
                    print(f"      L{m['line']}:{m['col']}  {m['text'][:70]}")
                if df["count"] > 3:
                    print(f"      ... +{df['count'] - 3} more")

        # Per-file quality metrics
        file_text = fp.read_text() if fp.exists() else ""
        comment_lines_f = sum(
            1
            for l in file_text.splitlines()
            if l.strip().startswith("#")
            or l.strip().startswith("//")
            or l.strip().startswith("/*")
        )
        file_qm = compute_quality_metrics(
            metrics, file_smells, file_checks, loc, raw_loc, comment_lines_f
        )
        summary_rows.append(
            {
                "name": fp.name,
                "lang": fp.suffix or "?",
                "loc": raw_loc,
                "nlok": loc,
                "cyclo": cyclo,
                "complexity": complexity,
                "lcom": lcom,
                "funcs": func_count,
                "ce": coupl["ce"],
                "ca": coupl["ca"],
                "smells": len(file_smells),
                "checks": len(file_checks),
                "classes": metrics.get("classes", 0),
                "fields": metrics.get("fields", 0),
                "comment_lines": comment_lines_f,
                "quality": file_qm,
            }
        )

    # ── Duplication summary ──
    dup_smells = [
        s
        for s in all_smells
        if "duplic" in s.get("rule_id", "").lower()
        or s.get("category", "") == "CATEGORY_DUPLICATION"
    ]
    if not dup_smells:
        # Also search by message text
        dup_smells = [s for s in all_smells if "duplic" in s.get("message", "").lower()]
    if dup_smells:
        print(f"\n{'─' * 88}")
        print("  \033[1m🔄 DUPLICACIONES (de Qlty smells)\033[0m")
        print(f"{'─' * 88}")
        by_file: dict[str, int] = defaultdict(int)
        for d in dup_smells:
            by_file[d["file"]] += 1
        for fname, count in sorted(by_file.items(), key=lambda x: -x[1])[:8]:
            print(f"  x{count} en {Path(fname).name}")

    # ── Summary table ──
    print(f"\n{'=' * 88}")
    print("  \033[1m📊 TABLA RESUMEN\033[0m")
    print(f"{'=' * 88}")
    hdr = (
        f"  {'Archivo':<30} {'Lang':<6} {'LOC':<6} {'NLOC':<6} {'Cyclo':<7} "
        f"{'Cog':<6} {'LCOM':<6} {'Olores':<6} {'Issues'}"
    )
    print(hdr)
    print(
        f"  {'─' * 30} {'─' * 6} {'─' * 6} {'─' * 6} {'─' * 7} {'─' * 6} {'─' * 6} {'─' * 6} {'─' * 6}"
    )

    for r in summary_rows:
        print(
            f"  {r['name']:<30} {r['lang']:<6} {r['loc']:<6} {r['nlok']:<6} {r['cyclo']:<7} "
            f"{r['complexity']:<6} {r['lcom']:<6} {r['smells']:<6} {r['checks']}"
        )

    # ── Check issues summary ──
    n_fixable = sum(1 for c in all_checks if c["has_fix"])
    n_warnings = sum(1 for c in all_checks if c["level"] == "warning")
    n_errors = sum(1 for c in all_checks if c["level"] == "error")
    if all_checks:
        print(
            f"\n  \033[1m🔬 CHECK ISSUES\033[0m  ({len(all_checks)} total, "
            f"{n_errors} errors, {n_warnings} warnings, {n_fixable} auto-fixable)"
        )
        rule_counts = Counter(c["rule_id"] for c in all_checks)
        for rule_id, count in rule_counts.most_common():
            fix_mark = (
                " \033[36m[fix]\033[0m"
                if any(c["rule_id"] == rule_id and c["has_fix"] for c in all_checks)
                else ""
            )
            print(f"    x{count}  {rule_id}{fix_mark}")

    # ── Quality Metrics Summary (aggregated from analyzed files) ──
    # Uses per-file quality data stored in summary_rows
    all_qm = None
    if summary_rows:
        agg_nloc = sum(r.get("nlok", 0) for r in summary_rows)
        agg_lines = sum(r.get("loc", 0) for r in summary_rows)
        agg_comm = sum(r.get("comment_lines", 0) for r in summary_rows)
        agg_metrics = {
            "classes": sum(r.get("classes", 0) for r in summary_rows),
            "fields": sum(r.get("fields", 0) for r in summary_rows),
            "funcs": sum(r.get("funcs", 0) for r in summary_rows),
            "cyclo": sum(r.get("cyclo", 0) for r in summary_rows),
            "complexity": sum(r.get("complexity", 0) for r in summary_rows),
            "lcom": max(r.get("lcom", 0) for r in summary_rows),
        }
        # Aggregate per-file quality data
        agg_dup_blocks = sum(
            r.get("quality", {}).get("dup_blocks", 0) for r in summary_rows
        )
        agg_dup_lines = sum(
            r.get("quality", {}).get("dup_lines", 0) for r in summary_rows
        )
        agg_smells = sum(r.get("quality", {}).get("smells", 0) for r in summary_rows)
        agg_tech_debt = sum(
            r.get("quality", {}).get("tech_debt_minutes", 0) for r in summary_rows
        )
        issues_high = sum(1 for c in all_checks if c.get("level") == "error")
        issues_med = sum(1 for c in all_checks if c.get("level") == "warning")
        issues_low = sum(1 for c in all_checks if c.get("level") in ("note", "low"))
        fixable = sum(1 for c in all_checks if c.get("has_fix"))

        all_qm = {
            "lines": agg_lines,
            "nloc": agg_nloc,
            "comment_lines": agg_comm,
            "comments_density": round(agg_comm / max(agg_lines, 1), 4),
            "classes": agg_metrics["classes"],
            "functions": agg_metrics["funcs"],
            "fields": agg_metrics["fields"],
            "code_files": len(summary_rows),
            "cyclomatic_complexity": agg_metrics["cyclo"],
            "cognitive_complexity": agg_metrics["complexity"],
            "cyclo_density": round(agg_metrics["cyclo"] / max(agg_nloc, 1), 4),
            "complexity_density": round(
                agg_metrics["complexity"] / max(agg_nloc, 1), 4
            ),
            "dup_blocks": agg_dup_blocks,
            "dup_lines": agg_dup_lines,
            "dup_density": round(agg_dup_lines / max(agg_nloc, 1), 4),
            "smells": agg_smells,
            "tech_debt_minutes": agg_tech_debt,
            "tech_debt_hours": round(agg_tech_debt / 60, 1),
            "tech_debt_ratio": round(
                agg_tech_debt / max(int(agg_nloc * _ESTIMATED_MINUTES_PER_LOC), 1), 4
            ),
            "maintainability_rating": _maintainability_rating(
                agg_tech_debt / max(int(agg_nloc * _ESTIMATED_MINUTES_PER_LOC), 1)
            ),
            "security_issues": sum(
                1 for c in all_checks if ":B" in c.get("rule_id", "")
            ),
            "security_high": sum(
                1 for c in all_checks if ":B6" in c.get("rule_id", "")
            ),
            "security_medium": sum(
                1
                for c in all_checks
                if c.get("rule_id", "").split(":")[-1].startswith(("B1", "B2", "B3"))
            ),
            "security_low": sum(
                1
                for c in all_checks
                if c.get("rule_id", "").split(":")[-1].startswith(("B4", "B5", "B7"))
            ),
            "issues": len(all_checks),
            "issues_high": issues_high,
            "issues_medium": issues_med,
            "issues_low": issues_low,
            "fixable_issues": fixable,
            "security_rating": _security_rating(
                sum(1 for c in all_checks if ":B6" in c.get("rule_id", "")),
                sum(
                    1
                    for c in all_checks
                    if c.get("rule_id", "")
                    .split(":")[-1]
                    .startswith(("B1", "B2", "B3"))
                ),
                sum(
                    1
                    for c in all_checks
                    if c.get("rule_id", "")
                    .split(":")[-1]
                    .startswith(("B4", "B5", "B7"))
                ),
            ),
            "coverage_pct": None,
            "coverage_rating": None,
        }

    if all_qm:
        print(f"\n{'=' * 88}")
        print("  \033[1m📊 QUALITY METRICS — PANEL COMPLETO\033[0m")
        print(f"{'=' * 88}")

        # Size
        print("  \033[1m📏 Size\033[0m")
        print(
            f"    Lines: {all_qm['lines']}  |  NLOC: {all_qm['nloc']}  |  "
            f"Comments: {all_qm['comment_lines']} ({all_qm['comments_density']:.1%})"
        )
        print(
            f"    Classes: {all_qm['classes']}  |  Functions: {all_qm['functions']}  |  "
            f"Fields: {all_qm['fields']}  |  Files: {all_qm['code_files']}"
        )

        # Complexity
        print("  \033[1m🧠 Complexity\033[0m")
        print(
            f"    Cyclomatic: {all_qm['cyclomatic_complexity']}  |  "
            f"Cognitive: {all_qm['cognitive_complexity']}"
        )
        print(
            f"    Cyclo density: {all_qm['cyclo_density']:.4f}  |  "
            f"Cognitive density: {all_qm['complexity_density']:.4f}"
        )

        # Duplication
        print("  \033[1m📋 Duplication\033[0m")
        print(
            f"    Blocks: {all_qm['dup_blocks']}  |  "
            f"Lines: {all_qm['dup_lines']}  |  "
            f"Density: {all_qm['dup_density']:.4f}"
        )

        # Maintainability
        tech_str = format_minutes(all_qm["tech_debt_minutes"])
        print("  \033[1m🔧 Maintainability\033[0m")
        print(
            f"    Smells: {all_qm['smells']}  |  "
            f"Tech Debt: {tech_str}  |  "
            f"Ratio: {all_qm['tech_debt_ratio']:.2%}  |  "
            f"Rating: {format_rating(all_qm['maintainability_rating'])}"
        )

        # Security
        print("  \033[1m🛡  Security\033[0m")
        print(
            f"    Issues: {all_qm['security_issues']}  |  "
            f"H:{all_qm['security_high']} M:{all_qm['security_medium']} L:{all_qm['security_low']}  |  "
            f"Rating: {format_rating(all_qm['security_rating'])}"
        )

        # Issues
        print("  \033[1m🐛 Issues\033[0m")
        print(
            f"    Total: {all_qm['issues']}  |  "
            f"H:{all_qm['issues_high']} M:{all_qm['issues_medium']} L:{all_qm['issues_low']}  |  "
            f"Fixable: {all_qm['fixable_issues']}"
        )

    print(
        "\n  \033[1mRANGOS:\033[0m  "
        "\033[32mCyclo 🟢≤30\033[0m \033[33m🟡31-60\033[0m \033[31m🔴>60\033[0m  |  "
        "\033[32mLCOM 🟢≤5\033[0m \033[33m🟡6-20\033[0m \033[31m🔴>20\033[0m"
    )
    total_smells_in_files = sum(r["smells"] for r in summary_rows)
    print(
        f"\n  Total LOC: {total_loc}  |  Archivos: {len(summary_rows)}  |  "
        f"Olores: {total_smells_in_files}  |  "
        f"Issues: {len(all_checks)} ({n_fixable} auto-fixables)"
    )
    print()

    # ── Evaluate quality gates ──
    gate_code, gate_reasons = _evaluate_quality_gates(
        all_checks, total_smells_in_files, results
    )
    if gate_code != 0:
        print(f"  {'─' * 60}")
        print("  ❌ QUALITY GATES FAILED:")
        for r in gate_reasons:
            print(f"     {r}")
        print(f"  {'─' * 60}")
        print("  ➡️  La tarea volvera a WORK para correccion (on_failure_loop)")
        print()
    return gate_code


def _main_json(files_to_analyze: list[Path]) -> int:
    """JSON mode: output machine-readable results for hooks/CI."""
    import json as _json

    all_smells = run_qlty_smells() if QLTY_AVAILABLE else []
    all_checks = run_qlty_check(files_to_analyze) if QLTY_AVAILABLE else []

    results: list[dict] = []
    all_cross_findings = []

    for fp in files_to_analyze:
        if not fp.exists():
            continue

        metrics = run_qlty_metrics(fp)
        funcs_data = run_qlty_metrics_functions(fp)
        cyclo = metrics.get("cyclo", 0)
        coupl = (
            coupling_metrics_python(fp)
            if fp.suffix == ".py"
            else {"ce": 0, "ca": 0, "imports": [], "imported_by": []}
        )

        fp_str = str(fp)
        rel_path = (
            str(fp.relative_to(_find_project_root(fp)))
            if _find_project_root(fp)
            else fp_str
        )

        file_smells = [
            s
            for s in all_smells
            if rel_path in s.get("file", "") or fp_str in s.get("file", "")
        ]
        file_checks = [
            c
            for c in all_checks
            if c.get("file", "") in (fp_str, rel_path)
            or (rel_path and c.get("file", "").endswith(rel_path))
        ]

        # ── Cross-reference analysis ──
        cross_findings = classify_risk(file_checks, cyclo, fp)
        all_cross_findings.extend(cross_findings)

        # ── Deep analysis (ast-grep) ──
        deep = deep_analyze_checks(file_checks, fp, cyclo=cyclo)

        # ── Quality metrics per file ──
        file_text = fp.read_text() if fp.exists() else ""
        comment_lines_f = sum(
            1
            for l in file_text.splitlines()
            if l.strip().startswith("#")
            or l.strip().startswith("//")
            or l.strip().startswith("/*")
        )
        file_loc = metrics.get("loc", 0)
        file_qm = (
            compute_quality_metrics(
                metrics,
                file_smells,
                file_checks,
                file_loc,
                len([l for l in file_text.splitlines() if l.strip()]),
                comment_lines_f,
            )
            if file_smells or file_checks
            else {}
        )

        entry = {
            "file": rel_path,
            "language": fp.suffix or "?",
            "loc": metrics.get("loc", 0),
            "nlok": metrics.get("loc", 0),
            "cyclo": cyclo,
            "complexity": metrics.get("complexity", 0),
            "lcom": metrics.get("lcom", 0),
            "funcs": metrics.get("funcs", 0),
            "functions": funcs_data,
            "ce": coupl["ce"],
            "ca": coupl["ca"],
            "smells_count": len(file_smells),
            "smells": file_smells[:10],
            "checks_count": len(file_checks),
            "checks": [
                {k: v for k, v in c.items() if k != "file"} for c in file_checks[:20]
            ],
            "cross_findings": cross_findings,
            "deep_analysis": deep,
            "quality": file_qm,
        }
        results.append(entry)

    # ── Count by severity ──
    total_errors = sum(1 for c in all_checks if c.get("level") == "error")
    total_warnings = sum(1 for c in all_checks if c.get("level") == "warning")
    total_smells_val = sum(f["smells_count"] for f in results)

    output = {
        "qlty_available": QLTY_AVAILABLE,
        "ast_grep_available": AST_GREP_AVAILABLE,
        "files_analyzed": len(results),
        "files": results,
        "total_smells": total_smells_val,
        "total_checks": len(all_checks),
        "total_errors": total_errors,
        "total_warnings": total_warnings,
        "fixable_checks": sum(1 for c in all_checks if c["has_fix"]),
        "cross_findings": all_cross_findings,
    }
    print(_json.dumps(output, indent=2))

    # ── Evaluate quality gates ──
    gate_code, gate_reasons = _evaluate_quality_gates(
        all_checks, total_smells_val, results
    )
    if gate_code != 0:
        print(
            json.dumps(
                {"quality_gates": {"passed": False, "reasons": gate_reasons}}, indent=2
            ),
            file=sys.stderr,
        )
    return gate_code


if __name__ == "__main__":
    sys.exit(main())
