"""_verify_engine.py — Motor unificado de verificación de predicates.

Reemplaza _internal/verification.py + _internal/advance.py (sección verification).
Sin dependencias circulares — puro stdlib + ast_grep_utils.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from string import Template

from ast_grep_utils import AST_GREP_AVAILABLE as _AST_GREP_AVAILABLE

# ═══════════════════════════════════════════════════════════════════
# CONSTANTES
# ═══════════════════════════════════════════════════════════════════

_AST_LANG_MAP: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".rb": "ruby",
    ".kt": "kotlin",
    ".swift": "swift",
}

_AST_DEF_PATTERNS: dict[str, list[str]] = {
    "python": ["def {name}($$$)", "class {name}", "async def {name}($$$)"],
    "typescript": [
        "function {name}($$$)",
        "class {name}",
        "const {name} = ($$$)",
        "function *{name}($$$)",
    ],
    "javascript": [
        "function {name}($$$)",
        "class {name}",
        "const {name} = ($$$)",
        "function *{name}($$$)",
    ],
    "rust": ["fn {name}($$$)", "struct {name}", "enum {name}", "trait {name}"],
    "go": ["func {name}($$$)", "type {name} struct", "type {name} interface"],
    "java": ["class {name}", "interface {name}", "{name}($$$)"],
    "ruby": ["def {name}", "class {name}", "module {name}"],
}

EXCLUDE_DIRS = [
    ".git",
    "node_modules",
    "__pycache__",
    ".cognitive",
    ".agents",
    ".opencode",
    "dist",
    "build",
    ".venv",
    "venv",
    "*.egg-info",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
]

_EXECUTABLE_PREFIXES = (
    "python3",
    "python",
    "bash",
    "sh",
    "zsh",
    "test",
    "[",
    "grep",
    "rg",
    "ag",
    "find",
    "cat",
    "head",
    "tail",
    "diff",
    "cmp",
    "wc",
    "ls",
    "echo",
    "git",
    "npm",
    "npx",
    "node",
    "docker",
    "make",
    "pytest",
    "which",
    "command",
)
_SHELL_OPERATORS = ("|", "&&", "||", ";", "2>&1", "`")
_NATURAL_LANG_TRIGGERS = frozenset(
    {
        "muestra",
        "retorna",
        "contiene",
        "genera",
        "incluye",
        "tiene",
        "describe",
        "agrega",
        "crea",
        "verifica",
        "valida",
        "lista",
        "show",
        "returns",
        "contains",
        "generates",
        "includes",
        "adds",
        "creates",
        "verifies",
        "validates",
        "lists",
        "describes",
        "debe",
        "should",
        "must",
        "puede",
        "can",
        "cada",
        "los",
        "las",
        "una",
        "un",
        "el",
        "la",
        "al",
        "del",
        "para",
        "con",
        "sin",
        "entre",
        "despues",
        "intentar",
        "modificar",
        "después",
        "usar",
        "no",
    }
)


# ═══════════════════════════════════════════════════════════════════
# CACHE DE VERIFICACIÓN (sesión, TTL 5 min)
# ═══════════════════════════════════════════════════════════════════

_verify_cache: dict[str, tuple[bool, dict, float]] = {}
CACHE_TTL = 300


def _cache_key(predicate, project_root: str, extra: str = "") -> str:
    if isinstance(predicate, dict):
        pred_str = json.dumps(predicate, sort_keys=True)
    else:
        pred_str = str(predicate)
    raw = f"{pred_str}|{project_root}|{extra}"
    return hashlib.md5(raw.encode()).hexdigest()


def invalidate_verify_cache() -> None:
    _verify_cache.clear()


def _prune_cache() -> None:
    now = time.time()
    expired = [k for k, (_, _, ts) in _verify_cache.items() if now - ts > CACHE_TTL]
    for k in expired:
        del _verify_cache[k]


def _cache_set(key: str, passed: bool, info: dict, now: float) -> None:
    if not info.get("error"):
        _verify_cache[key] = (passed, info, now)


# ═══════════════════════════════════════════════════════════════════
# HELPERS DE AST Y GREP
# ═══════════════════════════════════════════════════════════════════


def _build_grep_args(pattern, search_target):
    if os.path.isfile(search_target):
        return ["grep", "-n", pattern, search_target]
    args = [
        "grep",
        "-rn",
        pattern,
        search_target,
        "--include=*.py",
        "--include=*.md",
        "--include=*.json",
        "--include=*.ts",
        "--include=*.tsx",
        "--include=*.js",
        "--include=*.jsx",
    ]
    for d in EXCLUDE_DIRS:
        args.extend(["--exclude-dir", d])
    return args


def _resolve_search_target(search_hint: str, project_root: str) -> str:
    candidate = os.path.join(project_root, search_hint)
    if os.path.exists(candidate):
        return candidate
    projects_dir = os.path.join(project_root, "projects")
    if os.path.isdir(projects_dir):
        for sub in os.listdir(projects_dir):
            sub_path = os.path.join(projects_dir, sub)
            if not os.path.isdir(sub_path):
                continue
            for root, dirs, files in os.walk(sub_path):
                dirs[:] = [
                    d
                    for d in dirs
                    if d not in ("__pycache__", ".git", "node_modules", ".venv")
                ]
                if os.path.basename(search_hint) in files:
                    return os.path.join(root, os.path.basename(search_hint))
    return project_root


def _collect_ast_files(search_target: str) -> list[str]:
    if os.path.isfile(search_target):
        ext = os.path.splitext(search_target)[1].lower()
        if ext in _AST_LANG_MAP:
            return [search_target]
        return []
    files = []
    for root, dirs, fnames in os.walk(search_target):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS and not d.startswith(".")]
        for fname in fnames:
            ext = os.path.splitext(fname)[1].lower()
            if ext in _AST_LANG_MAP:
                files.append(os.path.join(root, fname))
    return files


def _ast_verify(
    symbol: str, search_target: str, vtype: str = "must_exist"
) -> bool | None:
    if not _AST_GREP_AVAILABLE:
        return None
    ast_files = _collect_ast_files(search_target)
    if not ast_files:
        return None

    by_lang: dict[str, list[str]] = {}
    for fpath in ast_files:
        ext = os.path.splitext(fpath)[1].lower()
        lang = _AST_LANG_MAP.get(ext, "python")
        by_lang.setdefault(lang, []).append(fpath)

    if vtype == "must_exist":
        for lang, lang_files in by_lang.items():
            patterns = _AST_DEF_PATTERNS.get(lang, ["def {name}($$$)", "class {name}"])
            for tmpl in patterns:
                pattern = tmpl.format(name=symbol)
                try:
                    result = subprocess.run(
                        ["sg", "-l", lang, "-p", pattern, "--json"] + lang_files,
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    if result.returncode == 0 and result.stdout.strip():
                        try:
                            matches = json.loads(result.stdout)
                            if isinstance(matches, list) and len(matches) > 0:
                                return True
                        except json.JSONDecodeError:
                            continue
                except (subprocess.TimeoutExpired, FileNotFoundError):
                    continue
        return False
    else:  # must_not_exist
        for lang, lang_files in by_lang.items():
            try:
                result = subprocess.run(
                    ["sg", "-l", lang, "-p", symbol, "--json"] + lang_files,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if result.returncode == 0 and result.stdout.strip():
                    try:
                        matches = json.loads(result.stdout)
                        if isinstance(matches, list) and len(matches) > 0:
                            return True
                    except json.JSONDecodeError:
                        continue
            except (subprocess.TimeoutExpired, FileNotFoundError):
                continue
        return False


def _extract_symbol_names(predicate: str) -> list[str]:
    symbols: list[str] = []
    def_class_match = re.match(
        r"(?:function|class|def|struct|enum|trait|fn|func)\s+(\w+)", predicate
    )
    if def_class_match:
        symbols.append(def_class_match.group(1))
        return symbols
    if "." in predicate:
        parts = predicate.split(".")
        if len(parts) >= 3 or predicate.startswith("cognitive_v2."):
            return [predicate.strip()]
        symbols.append(parts[0].strip())
        next_part = parts[1].split()[0].strip().rstrip("()")
        if next_part:
            symbols.append(next_part)
        return symbols
    cleaned = predicate.strip().rstrip("()")
    words = re.split(r"[\s,;:]+", cleaned)
    skip_words = {
        "llamada",
        "llamado",
        "retorna",
        "incluye",
        "expone",
        "tiene",
        "produce",
        "funciona",
        "marca",
        "contiene",
        "llama",
        "crea",
        "implementa",
    }
    for w in words:
        w = w.strip("'\"`()")
        if w and len(w) > 2 and w.lower() not in skip_words:
            symbols.append(w)
            break
    if not symbols:
        symbols.append(cleaned)
    return symbols


def _grep_verify(
    patterns: list[str], search_target: str, vtype: str = "must_exist"
) -> bool:
    if vtype == "must_exist":
        for pattern in patterns:
            try:
                result = subprocess.run(
                    _build_grep_args(pattern, search_target),
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if result.returncode == 0:
                    return True
            except subprocess.TimeoutExpired:
                continue
        return False
    else:  # must_not_exist
        try:
            result = subprocess.run(
                _build_grep_args(patterns[0] if patterns else "", search_target),
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                return True
            lines = result.stdout.splitlines()
            actual_calls = []
            for line in lines:
                parts = line.split(":", 2)
                content = parts[2] if len(parts) >= 3 else line
                content = content.strip()
                if (
                    content.startswith("def ")
                    or content.startswith("class ")
                    or content.startswith("async def ")
                ):
                    continue
                if (
                    content.startswith("#")
                    or content.startswith('"""')
                    or content.startswith("'''")
                    or "``" in content
                    or "`" in content
                ):
                    continue
                actual_calls.append(line)
            return len(actual_calls) == 0
        except subprocess.TimeoutExpired:
            return False


def _resolve_target(file_hint: str, project_root: str) -> str:
    if not file_hint:
        return project_root
    candidate = os.path.join(project_root, file_hint)
    if os.path.exists(candidate):
        return os.path.abspath(candidate)
    if os.path.exists(file_hint):
        return os.path.abspath(file_hint)
    return project_root


# ═══════════════════════════════════════════════════════════════════
# PARSEO DE PREDICATE EN LENGUAJE NATURAL
# ═══════════════════════════════════════════════════════════════════


def _parse_predicate(predicate_text):
    text = predicate_text.strip()
    vtype = "must_exist"
    file_hint = None

    if text.upper().startswith("NO "):
        vtype = "must_not_exist"
        text = text[3:].strip()
    elif text.upper().startswith("SIN "):
        vtype = "must_not_exist"
        text = text[4:].strip()
    elif re.match(r"^0\s+", text):
        vtype = "must_not_exist"
        text = re.sub(r"^0\s+", "", text)

    file_match = re.search(
        r"\s+en\s+((?:cognitive_v2|cognitive|projects|src|\.claude|features|app|api|infrastructure|domain|application)[^\s,;]*"
        r"|[\w/.]+\.\w+)",
        text,
    )
    if file_match:
        file_hint = file_match.group(1)
        text = text[: file_match.start()].strip()

    pattern = text
    def_match = re.match(r"(def|class|function|async\s+def)\s+(\w+)", text)
    if def_match:
        prefix = def_match.group(1)
        name = def_match.group(2)
        pattern = f"{'def' if prefix == 'function' else prefix} {name}"
    else:
        words = re.split(r"[\s,;:]+", text)
        skip_words = {
            "llamada",
            "llamado",
            "llamadas",
            "llamados",
            "retorna",
            "retornar",
            "incluye",
            "incluir",
            "tiene",
            "tener",
            "marca",
            "marcar",
            "produce",
            "producir",
            "expone",
            "exponer",
            "funciona",
            "funcionar",
            "contiene",
            "contener",
            "llama",
            "llamar",
            "crea",
            "crear",
            "implementa",
            "implementar",
        }
        meaningful = [
            w
            for w in words
            if w
            and (w[0].isalnum() or w[0] == "_")
            and len(w) > 2
            and w.lower() not in skip_words
        ]
        if meaningful:
            pattern = meaningful[0]
            if vtype == "must_not_exist" and re.match(r"^[a-zA-Z_]\w*$", pattern):
                pattern = pattern + "("
    return pattern, vtype, file_hint


# ═══════════════════════════════════════════════════════════════════
# RUN VERIFICATION (TASK LOOP level)
# ═══════════════════════════════════════════════════════════════════


def run_verification(loop_item, project_root, file_hint=None):
    """Run a single TASK LOOP verification check.

    Uses AST-level verification (ast-grep) when available.
    """
    predicate = loop_item["predicate"] if isinstance(loop_item, dict) else loop_item
    vtype = (
        loop_item.get("type", "must_exist")
        if isinstance(loop_item, dict)
        else "must_exist"
    )
    fhint = file_hint or (
        loop_item.get("file_hint") if isinstance(loop_item, dict) else None
    )

    # Determine search target
    search_target = project_root
    if fhint:
        resolved = _resolve_search_target(fhint, project_root)
        if resolved != project_root:
            search_target = resolved

    # Step 1: AST-level verification (ignores comments/docstrings)
    ast_result = _ast_verify(predicate, search_target, vtype)
    if ast_result is not None:
        return ast_result

    # Step 2: Fallback to grep verification
    symbols = _extract_symbol_names(predicate) if isinstance(predicate, str) else []
    if symbols:
        return _grep_verify(symbols, search_target, vtype)

    return _grep_verify([predicate], search_target, vtype)


# ═══════════════════════════════════════════════════════════════════
# SHELL DETECTION
# ═══════════════════════════════════════════════════════════════════


def _is_string_a_shell_command(predicate_text: str) -> bool:
    text = predicate_text.strip()
    if not text:
        return False
    words = text.split()
    first_word = words[0] if words else ""
    if first_word.startswith("/"):
        return True
    if any(
        first_word == p or first_word.startswith(p + "/") for p in _EXECUTABLE_PREFIXES
    ):
        if any(w in _NATURAL_LANG_TRIGGERS for w in words[1:]):
            return False
        return True
    if any(op in text for op in _SHELL_OPERATORS):
        return True
    return False


# ═══════════════════════════════════════════════════════════════════
# HANDLERS DE STRING
# ═══════════════════════════════════════════════════════════════════


def _verify_natural_language(text: str, project_root: str) -> tuple[bool, dict]:
    try:
        pattern, vtype, fhint = _parse_predicate(text)
    except Exception as exc:
        return False, {
            "method": "ast-grep",
            "error": f"no se pudo parsear: {exc}",
            "label": text[:70],
        }
    check_type = vtype if vtype in ("must_exist", "must_not_exist") else "must_exist"
    try:
        passed = run_verification(
            {"predicate": pattern, "type": check_type}, project_root, file_hint=fhint
        )
    except Exception as exc:
        return False, {
            "method": "ast-grep",
            "error": f"error en verificación: {exc}",
            "label": text[:70],
        }
    return passed, {
        "method": "ast-grep",
        "pattern": pattern,
        "type": check_type,
        "file_hint": fhint,
        "label": text[:70],
    }


def _verify_shell_string(text: str, project_root: str) -> tuple[bool, dict]:
    try:
        result = subprocess.run(
            text,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=project_root,
        )
        passed = result.returncode == 0
        return passed, {
            "method": "shell",
            "command": text[:80],
            "exit_code": result.returncode,
            "stdout": result.stdout[:200] or "",
            "stderr": result.stderr[:200] or "",
            "label": text[:70],
        }
    except subprocess.TimeoutExpired:
        return False, {
            "method": "shell",
            "command": text[:80],
            "error": "timeout (30s)",
            "label": text[:70],
        }
    except Exception as exc:
        return False, {
            "method": "shell",
            "command": text[:80],
            "error": str(exc),
            "label": text[:70],
        }


# ═══════════════════════════════════════════════════════════════════
# HANDLERS ESTRUCTURADOS
# ═══════════════════════════════════════════════════════════════════


def _verify_symbol_exists(predicate: dict, project_root: str) -> tuple[bool, dict]:
    symbol = predicate.get("symbol", "")
    file_hint = predicate.get("file")
    if not symbol:
        return False, {
            "method": "symbol_exists",
            "error": "campo 'symbol' requerido",
            "label": "symbol_exists: campo 'symbol' requerido",
        }
    try:
        passed = run_verification(
            {"predicate": symbol, "type": "must_exist"},
            project_root,
            file_hint=file_hint,
        )
    except Exception as exc:
        label = f"{symbol} en {file_hint}" if file_hint else symbol
        return False, {
            "method": "symbol_exists",
            "symbol": symbol,
            "file": file_hint,
            "error": str(exc),
            "label": label,
        }
    label = f"{symbol} en {file_hint}" if file_hint else symbol
    return passed, {
        "method": "symbol_exists",
        "symbol": symbol,
        "file": file_hint,
        "label": label,
    }


def _verify_symbol_not_exists(predicate: dict, project_root: str) -> tuple[bool, dict]:
    symbol = predicate.get("symbol", "")
    file_hint = predicate.get("file")
    if not symbol:
        return False, {
            "method": "symbol_not_exists",
            "error": "campo 'symbol' requerido",
            "label": "symbol_not_exists: campo 'symbol' requerido",
        }
    try:
        passed = not run_verification(
            {"predicate": symbol, "type": "must_not_exist"},
            project_root,
            file_hint=file_hint,
        )
    except Exception as exc:
        label = f"NO {symbol} en {file_hint}" if file_hint else f"NO {symbol}"
        return False, {
            "method": "symbol_not_exists",
            "symbol": symbol,
            "file": file_hint,
            "error": str(exc),
            "label": label,
        }
    label = f"NO {symbol} en {file_hint}" if file_hint else f"NO {symbol}"
    return passed, {
        "method": "symbol_not_exists",
        "symbol": symbol,
        "file": file_hint,
        "label": label,
    }


def _verify_command_exit(predicate: dict, project_root: str) -> tuple[bool, dict]:
    command = predicate.get("command", "")
    if not command:
        return False, {
            "method": "command_exit",
            "error": "campo 'command' requerido",
            "label": "?",
        }
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=project_root,
        )
        return result.returncode == 0, {
            "method": "command_exit",
            "command": command[:80],
            "exit_code": result.returncode,
            "label": command[:70],
        }
    except subprocess.TimeoutExpired:
        return False, {
            "method": "command_exit",
            "command": command[:80],
            "error": "timeout (30s)",
            "label": command[:70],
        }
    except Exception as exc:
        return False, {
            "method": "command_exit",
            "command": command[:80],
            "error": str(exc),
            "label": command[:70],
        }


def _verify_command_output(predicate: dict, project_root: str) -> tuple[bool, dict]:
    command = predicate.get("command", "")
    contains = predicate.get("contains", "")
    if not command or not contains:
        return False, {
            "method": "command_output",
            "error": "campos 'command' y 'contains' requeridos",
            "label": "command_output: campos requeridos",
        }
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=project_root,
        )
        combined = (result.stdout or "") + (result.stderr or "")
        found = contains in combined
        return found, {
            "method": "command_output",
            "command": command[:80],
            "contains": contains,
            "found": found,
            "exit_code": result.returncode,
            "label": command[:70],
        }
    except subprocess.TimeoutExpired:
        return False, {
            "method": "command_output",
            "command": command[:80],
            "contains": contains,
            "error": "timeout",
            "label": command[:70],
        }
    except Exception as exc:
        return False, {
            "method": "command_output",
            "command": command[:80],
            "contains": contains,
            "error": str(exc),
            "label": command[:70],
        }


def _verify_test_pass(predicate: dict, project_root: str) -> tuple[bool, dict]:
    command = predicate.get("command", "")
    if not command:
        return False, {
            "method": "test_pass",
            "error": "campo 'command' requerido",
            "label": "test_pass: campo 'command' requerido",
        }
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=project_root,
        )
        return result.returncode == 0, {
            "method": "test_pass",
            "command": command[:80],
            "exit_code": result.returncode,
            "label": command[:70],
        }
    except subprocess.TimeoutExpired:
        return False, {
            "method": "test_pass",
            "command": command[:80],
            "error": "timeout (60s)",
            "label": command[:70],
        }
    except Exception as exc:
        return False, {
            "method": "test_pass",
            "command": command[:80],
            "error": str(exc),
            "label": command[:70],
        }


# ═══════════════════════════════════════════════════════════════════
# BATCH VERIFICATION (ast-grep agrupado)
# ═══════════════════════════════════════════════════════════════════


def _batch_verify_symbols(
    indices: list[int],
    symbol_preds: list[dict],
    project_root: str,
    all_predicates: list,
) -> None:
    batched: list[tuple[int, str, str, dict]] = []
    individual: list[int] = []

    for idx, p in zip(indices, symbol_preds):
        symbol = p.get("symbol", "") or p.get("_original", "")
        if not symbol:
            continue
        file_hint = p.get("file", "")
        if file_hint:
            resolved = _resolve_target(file_hint, project_root)
            if resolved != project_root and os.path.isfile(resolved):
                batched.append((idx, resolved, symbol, p))
                continue
        individual.append(idx)

    by_file: dict[str, list[tuple[int, str, dict]]] = {}
    for idx, fpath, symbol, p in batched:
        by_file.setdefault(fpath, []).append((idx, symbol, p))

    for fpath, items in by_file.items():
        ext = os.path.splitext(fpath)[1].lower()
        lang = _AST_LANG_MAP.get(ext, "python")
        patterns = _AST_DEF_PATTERNS.get(lang, ["def {name}($$$)", "class {name}"])
        symbols_in_file = [s for _, s, _ in items]
        symbol_found: dict[str, bool] = {s: False for s in symbols_in_file}

        for tmpl in patterns:
            if all(symbol_found.values()):
                break
            remaining = [s for s in symbols_in_file if not symbol_found[s]]
            if not remaining:
                continue
            if _AST_GREP_AVAILABLE:
                sg_args = ["sg", "-l", lang, "--json"]
                for s in remaining:
                    sg_args.extend(["-p", tmpl.format(name=s)])
                sg_args.append(fpath)
                try:
                    result = subprocess.run(
                        sg_args, capture_output=True, text=True, timeout=10
                    )
                    if result.returncode == 0 and result.stdout.strip():
                        try:
                            matches = json.loads(result.stdout)
                            if isinstance(matches, list):
                                for match in matches:
                                    match_text = match.get("text", "")
                                    for s in remaining:
                                        if not symbol_found.get(s, False) and (
                                            f"def {s}(" in match_text
                                            or f"class {s}" in match_text
                                        ):
                                            symbol_found[s] = True
                        except json.JSONDecodeError:
                            pass
                except (subprocess.TimeoutExpired, FileNotFoundError):
                    pass
            for s in remaining:
                if not symbol_found.get(s, False):
                    try:
                        r = subprocess.run(
                            ["grep", "-qn", f"def {s}(", fpath],
                            capture_output=True,
                            timeout=5,
                        )
                        if r.returncode == 0:
                            symbol_found[s] = True
                        else:
                            r2 = subprocess.run(
                                ["grep", "-qn", f"class {s}", fpath],
                                capture_output=True,
                                timeout=5,
                            )
                            if r2.returncode == 0:
                                symbol_found[s] = True
                    except (subprocess.TimeoutExpired, FileNotFoundError):
                        pass

        now = time.time()
        for idx, s, p in items:
            passed = symbol_found.get(s, False)
            label = f"{s} en {os.path.basename(fpath)}"
            info = {
                "method": "ast-grep",
                "symbol": s,
                "file": fpath,
                "found": passed,
                "label": label,
            }
            key = _cache_key(all_predicates[idx], project_root)
            _verify_cache[key] = (passed, info, now)

    for idx in individual:
        try:
            pos = indices.index(idx)
            orig_pred = symbol_preds[pos]
            orig_raw = orig_pred.get("_original", "")
            if orig_raw:
                passed, info = verify_predicate(orig_raw, project_root)
            else:
                passed, info = verify_predicate(orig_pred, project_root)
        except ValueError:
            passed, info = (
                False,
                {"method": "error", "error": "idx not found", "label": "?"},
            )
        key = _cache_key(all_predicates[idx], project_root)
        _cache_set(key, passed, info, time.time())


# ═══════════════════════════════════════════════════════════════════
# API PÚBLICA
# ═══════════════════════════════════════════════════════════════════


def verify_predicate(predicate, project_root: str) -> tuple[bool, dict]:
    """Verifica un predicate contra código real.

    Args:
        predicate: String (lenguaje natural o comando shell) o dict estructurado.
        project_root: Ruta absoluta a la raíz del proyecto.

    Returns:
        (passed: bool, info: dict)
    """
    key = _cache_key(predicate, project_root)
    now = time.time()
    _prune_cache()

    if key in _verify_cache:
        passed, info, _ts = _verify_cache[key]
        return passed, dict(info)

    if isinstance(predicate, str):
        if _is_string_a_shell_command(predicate):
            passed, info = _verify_shell_string(predicate, project_root)
        else:
            passed, info = _verify_natural_language(predicate, project_root)
            print(
                Template('  Predicate en lenguaje natural: "$pred"').substitute(
                    pred=predicate[:60]
                )
            )
            print(
                '     Usa formato estructurado: {"type": "symbol_exists", "symbol": "...", "file": "..."}'
            )
        _cache_set(key, passed, info, now)
        return passed, info

    if not isinstance(predicate, dict):
        return (
            False,
            {
                "method": "unknown",
                "error": f"tipo inválido: {type(predicate).__name__}",
                "label": f"tipo inválido: {type(predicate).__name__}",
            },
        )

    ptype = predicate.get("type", "")
    handlers = {
        "symbol_exists": _verify_symbol_exists,
        "symbol_not_exists": _verify_symbol_not_exists,
        "command_exit": _verify_command_exit,
        "command_output": _verify_command_output,
        "test_pass": _verify_test_pass,
    }
    if ptype == "file_exists":
        path = predicate.get("path", "")
        passed = os.path.exists(os.path.join(project_root, path)) if path else False
        info = {"method": "file_exists", "path": path, "label": f"Archivo: {path}"}
    elif ptype == "grep":
        pattern = predicate.get("pattern", "")
        inc = predicate.get("include", "*")
        r = subprocess.run(
            ["grep", "-rl", pattern, "--include=" + inc, project_root],
            capture_output=True,
            text=True,
            timeout=10,
        )
        passed = r.returncode == 0 and r.stdout.strip() != ""
        info = {
            "method": "grep",
            "pattern": pattern,
            "files": r.stdout[:200] if passed else "",
            "label": f"grep: {pattern}",
        }
    elif ptype == "manual":
        desc = predicate.get("description", predicate.get("label", ""))
        passed = True
        info = {
            "method": "manual",
            "description": desc,
            "note": "Verificacion manual requerida",
            "label": f"[manual] {desc[:80]}",
        }
    elif ptype in handlers:
        passed, info = handlers[ptype](predicate, project_root)
    else:
        return False, {
            "method": "unknown",
            "type": ptype,
            "error": f"tipo de predicate desconocido: {ptype}",
            "label": f"tipo desconocido: {ptype}",
        }

    _cache_set(key, passed, info, now)
    return passed, info


def verify_predicates_batch(
    predicates: list, project_root: str
) -> list[tuple[bool, dict]]:
    """Verifica múltiples predicates en batch con optimización ast-grep."""
    _prune_cache()

    symbol_indices: list[int] = []
    symbol_preds: list[dict] = []
    other: list[tuple[int, object]] = []

    for idx, p in enumerate(predicates):
        key = _cache_key(p, project_root)
        if key in _verify_cache:
            continue
        if isinstance(p, dict) and p.get("type") == "symbol_exists":
            symbol_indices.append(idx)
            symbol_preds.append(p)
        elif isinstance(p, str) and not _is_string_a_shell_command(p):
            symbol_indices.append(idx)
            symbol_preds.append({"type": "symbol_exists", "symbol": p, "_original": p})
        else:
            other.append((idx, p))

    if symbol_preds:
        _batch_verify_symbols(symbol_indices, symbol_preds, project_root, predicates)

    results: dict[int, tuple[bool, dict]] = {}
    for idx, p in other:
        results[idx] = verify_predicate(p, project_root)

    for idx, p in enumerate(predicates):
        if idx not in results:
            key = _cache_key(p, project_root)
            if key in _verify_cache:
                passed, info, _ = _verify_cache[key]
                results[idx] = (passed, dict(info))
            else:
                results[idx] = verify_predicate(p, project_root)

    return [results[i] for i in range(len(predicates))]
