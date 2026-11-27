"""fast_mcp/features/tasks/infrastructure/verify_engine.py — Motor unificado de verificación de predicates.

Port de ``to_clean_next/scripts/_verify_engine.py`` + ``ast_grep_utils.py``
(sección de detección). Sin dependencias de to_clean_next — puro stdlib.

Tipos de predicate soportados:
  - String (legacy): Lenguaje natural → _parse_predicate + ast-grep, o
    comando shell si _is_string_a_shell_command() lo detecta.
  - Dict typed:
      ``{"type": "symbol_exists", "symbol": "X", "file": "Y.py"}``
      ``{"type": "symbol_not_exists", "symbol": "X", "file": "Y.py"}``
      ``{"type": "command_exit", "command": "python3 script.py"}``
      ``{"type": "command_output", "command": "...", "contains": "text"}``
      ``{"type": "test_pass", "command": "pytest tests/test_x.py"}``
      ``{"type": "file_exists", "path": "src/x.py"}``
      ``{"type": "grep", "pattern": "foo", "include": "*.py"}``
      ``{"type": "manual", "description": "..."}``

Cache de sesión:
  ``verify_predicate()`` cachea resultados en memoria (TTL 5 min).
  ``verify_predicates_batch()`` permite verificación agrupada (batch ast-grep).
  ``invalidate_verify_cache()`` limpia la cache forzadamente.

API pública:
  verify_predicate(predicate, project_root) → (bool, dict)
  verify_predicates_batch(predicates, project_root) → list[(bool, dict)]
  invalidate_verify_cache() → None
  run_verification(loop_item, project_root, file_hint=None) → bool
  _parse_predicate(text) → (pattern, vtype, file_hint)   [uso interno/test]
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import time
from string import Template

# ── ast-grep binary (sg CLI) ────────────────────────────────────
# El motor usa la CLI ``sg`` (ast-grep v0.44+) con patrones AST a nivel
# de definición. Configurable vía AST_GREP_BIN (compat con el resto de features).
AST_GREP_BIN = os.environ.get("AST_GREP_BIN") or "sg"
_AST_GREP_AVAILABLE = False
try:
    _r = subprocess.run([AST_GREP_BIN, "--version"], capture_output=True, text=True, timeout=5)
    _AST_GREP_AVAILABLE = _r.returncode == 0
except (FileNotFoundError, subprocess.TimeoutExpired):
    _AST_GREP_AVAILABLE = False

# ═══════════════════════════════════════════════════════════════════
# CONSTANTES
# ═══════════════════════════════════════════════════════════════════

#: Timeout de un comando de verificación. El 30 s fijo medía mal: la suite de
#: walker tarda ~35 s y el predicado `python3 -m pytest -q` salía ROJO por
#: timeout aunque la suite pasara (medido 2026-11-21: 1.668 passed en 35,2 s).
#: `run_tests` ya usa 300 s; la verificación tiene que poder cubrir la misma
#: suite. Configurable para runners más lentos (ADR-084 F3b).
DEFAULT_VERIFY_TIMEOUT_S = 300


def verify_timeout_s() -> int:
    """Timeout efectivo de `command_exit`/`test_pass`/`shell` (env o 300 s)."""
    raw = os.environ.get("WALKER_VERIFY_TIMEOUT") or ""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_VERIFY_TIMEOUT_S
    return value if value > 0 else DEFAULT_VERIFY_TIMEOUT_S


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
    """Genera key determinística para cache de verification.

    Args:
        predicate: String o dict del predicate.
        project_root: Ruta absoluta del proyecto.
        extra: Contexto adicional (ej: id de tarea)

    Returns:
        MD5 hex digest como key.
    """
    if isinstance(predicate, dict):
        pred_str = json.dumps(predicate, sort_keys=True)
    else:
        pred_str = str(predicate)
    raw = f"{pred_str}|{project_root}|{extra}"
    return hashlib.md5(raw.encode()).hexdigest()


def invalidate_verify_cache() -> None:
    """Limpia toda la cache de verificación."""
    _verify_cache.clear()


def _prune_cache() -> None:
    """Elimina entradas expiradas de la cache."""
    now = time.time()
    expired = [k for k, (_, _, ts) in _verify_cache.items() if now - ts > CACHE_TTL]
    for k in expired:
        del _verify_cache[k]


def _cache_set(key: str, passed: bool, info: dict, now: float) -> None:
    """Guarda en cache solo si no hubo error."""
    if not info.get("error"):
        _verify_cache[key] = (passed, info, now)


# ═══════════════════════════════════════════════════════════════════
# HELPERS DE AST Y GREP
# ═══════════════════════════════════════════════════════════════════


def _build_grep_args(pattern, search_target):
    """Construye args de grep excluyendo directorios generados."""
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
    """Resuelve un file_hint a ruta real.

    Prueba: (1) path relativo a project_root; (2) búsqueda recursiva bajo
    projects/<pid>/; (3) fallback a project_root.
    """
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
    """Colecta archivos soportados por ast-grep desde search_target."""
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
    """Verificación AST-level con ast-grep (``sg`` CLI).

    Returns:
        True: definición/referencia encontrada a nivel AST.
        False: no encontrada a nivel AST.
        None: ast-grep no disponible o sin archivos soportados (fallback a grep).
    """
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
                        [AST_GREP_BIN, "-l", lang, "-p", pattern, "--json"] + lang_files,
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
                    [AST_GREP_BIN, "-l", lang, "-p", symbol, "--json"] + lang_files,
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
    """Extrae nombres de símbolo de un predicate para verificación AST."""
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
    """Verificación fallback por grep (cuando ast-grep no está disponible)."""
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
    """Resuelve file_hint a ruta absoluta. Retorna project_root si no resuelve."""
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
    """Parsea un predicate en lenguaje natural.

    Formatos:
      - "function X en Y" / "def X en Y" → must_exist
      - "class X en Y" → must_exist
      - "NO X en Y" / "sin X" / "0 X en Y" → must_not_exist
      - "X llamada en Y" / "X en Y" → must_exist
      - "X retorna Z" → must_exist

    Returns:
        (pattern, verification_type, file_hint)
    """
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
    """Ejecuta un check de verificación a nivel task loop.

    Usa AST-level (ast-grep) cuando está disponible (ignora comentarios/
    docstrings) y cae a grep para lenguajes no soportados o símbolos que
    los patrones AST no cubren (ej: ``const X = {...}``).

    Extrae símbolos primero (via _extract_symbol_names) para que el
    predicate "def foo" verifique el símbolo ``foo``, no el texto completo.

    Args:
        loop_item: dict con ``predicate`` y ``type`` (o string directo).
        project_root: Ruta absoluta del proyecto.
        file_hint: Path relativo para acotar la búsqueda.

    Returns:
        True si el check pasa, False en caso contrario.
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

    search_target = project_root
    if fhint:
        resolved = _resolve_search_target(fhint, project_root)
        if resolved != project_root:
            search_target = resolved

    symbols = _extract_symbol_names(predicate) if isinstance(predicate, str) else []
    if not symbols:
        symbols = [predicate]

    if vtype == "must_exist":
        for symbol in symbols:
            ast_result = _ast_verify(symbol, search_target, "must_exist")
            if ast_result is True:
                return True  # Definición real encontrada a nivel AST
            # Fallback grep SIEMPRE (patrones AST limitados):
            if _grep_verify([symbol], search_target, "must_exist"):
                return True
        # Dotted name (Class.method) → probar también la clase
        if isinstance(predicate, str) and "." in predicate:
            class_part = predicate.split(".")[0].strip()
            if _ast_verify(class_part, search_target, "must_exist") is True:
                return True
            if _grep_verify([class_part], search_target, "must_exist"):
                return True
        return False

    if vtype == "must_not_exist":
        for symbol in symbols:
            ast_result = _ast_verify(symbol, search_target, "must_not_exist")
            if ast_result is True:
                return False  # Símbolo en código real → must_not_exist FALLA
            if ast_result is False:
                continue  # No encontrado a nivel AST → pasa (ver siguiente)
            # None → fallback grep
            if not _grep_verify([symbol], search_target, "must_not_exist"):
                return False
        return True  # Todos los símbolos pasan must_not_exist

    if vtype == "test":
        # Test en el predicate → ejecuta el framework del proyecto con -k
        # (patrón por substring). Resuelto desde project.json (G-W6): ya no
        # hardcodea python3 -m pytest para proyectos no-Python.
        test_pattern = predicate.replace("test_", "").replace("()", "")
        try:
            argv = build_test_command(project_root, name=test_pattern)
            result = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=120,
                cwd=project_root,
            )
            return "PASSED" in result.stdout or result.returncode == 0
        except subprocess.TimeoutExpired:
            return False

    return False


# ═══════════════════════════════════════════════════════════════════
# SHELL DETECTION
# ═══════════════════════════════════════════════════════════════════


def _is_string_a_shell_command(predicate_text: str) -> bool:
    """Detecta si un predicate string es un comando shell ejecutable."""
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
# TEST FRAMEWORK RESOLUTION (project.json aware)
# ═══════════════════════════════════════════════════════════════════

class TestRunnerUnavailable(RuntimeError):
    """El proyecto no declara un test framework con runner conocido.

    Es un error de CONTEXTO, no un test rojo: antes se caía a pytest y un
    proyecto JS/Go ejecutaba una suite que no existe (o la equivocada).
    """


# Mostly-keyword → runner base. Normalizada en _normalize_test_framework().
_TEST_RUNNERS: dict[str, list[str]] = {
    "pytest": ["python3", "-m", "pytest"],
    "unittest": ["python3", "-m", "unittest"],
    "jest": ["npx", "jest"],
    "vitest": ["npx", "vitest", "run"],
    "mocha": ["npx", "mocha"],
    "go": ["go", "test"],
    "golang": ["go", "test"],
    "cargo": ["cargo", "test"],
    "gradle": ["./gradlew", "test"],
    "maven": ["mvn", "test"],
    "dotnet": ["dotnet", "test"],
    "phpunit": ["./vendor/bin/phpunit"],
    "rspec": ["bundle", "exec", "rspec"],
    # PureScript (Spago): el runner vive en node_modules/.bin, así que `npx`
    # lo resuelve sin depender del PATH global. Sin esta entrada, un proyecto
    # PureScript solo podía declarar un framework ajeno (mentira) o quedarse
    # sin `run_tests` (medido 2026-11-22 en client-chat).
    "spago": ["npx", "spago", "test"],
}

# Alias declarables → key canónica de runner. `purescript` es el LENGUAJE, no
# el runner: mapearlo evita exigirle al manifiesto un nombre de herramienta.
_TEST_FRAMEWORK_ALIASES: dict[str, str] = {
    "purescript": "spago",
}

# Flag de filtro por nombre según runner (pytest -k, jest -t, go -run, ...).
_TEST_NAME_FLAG: dict[str, list[str]] = {
    "pytest": ["-k"],
    "unittest": ["-k"],
    "jest": ["-t"],
    "vitest": ["-t"],
    "mocha": ["--grep"],
    "go": ["-run"],
    "cargo": [],
    "gradle": ["--tests"],
    "dotnet": ["--filter"],
    # `spago test` no tiene flag de filtro por nombre; el argumento posicional
    # es un módulo, no un patrón. Vacío = se anexa tal cual (como cargo), nunca
    # un `-k` heredado del default.
    "spago": [],
}


def _normalize_test_framework(framework: str) -> str:
    """Normaliza un framework/language declarado a la key canónica del runner.

    Maneja variantes: "go test", "Golang", "jest (ts)", "pytest ~8", etc.
    """
    if not framework:
        return ""
    text = framework.strip().lower()

    def _matches(name: str) -> bool:
        # Palabra completa, no subcadena: `"go" in "spago"` devolvía el runner
        # de Go para un proyecto PureScript (medido 2026-11-22).
        return re.search(
            rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])", text
        ) is not None

    for alias, key in _TEST_FRAMEWORK_ALIASES.items():
        if _matches(alias):
            return key
    for key in _TEST_RUNNERS:
        if _matches(key):
            return key
    return ""


def _unavailable(declared: str) -> TestRunnerUnavailable:
    if not declared:
        return TestRunnerUnavailable(
            "el proyecto no declara coding.test_framework en project.json "
            "(no se asume pytest); declaralo con project_init/apply_project_config "
            "o editá el manifiesto"
        )
    return TestRunnerUnavailable(
        f"test_framework declarado {declared!r} no tiene runner conocido; "
        f"soportados: {', '.join(sorted(_TEST_RUNNERS))}"
    )


def resolve_test_framework(project_root: str) -> str:
    """Key canónica del runner declarado (pytest, jest, go, custom, ...).

    Raises:
        TestRunnerUnavailable: sin declaración o con un framework desconocido.
            Es fail-fast a propósito: inventar pytest convertía el contexto
            ausente en una suite que no corresponde al proyecto.
    """
    if _read_declared_test_command(project_root):
        # El proyecto declara SU gate completo; no hace falta adivinar runner.
        return "custom"
    declared = _read_declared_framework(project_root)
    key = _normalize_test_framework(declared)
    if not key:
        raise _unavailable(declared)
    return key


def _resolve_test_command(project_root: str) -> list[str]:
    """Comando base de tests del framework DECLARADO por el proyecto."""
    command = _read_declared_test_command(project_root)
    if command:
        return shlex.split(command)
    return list(_TEST_RUNNERS[resolve_test_framework(project_root)])


def build_test_command(
    project_root: str,
    *,
    target: str | None = None,
    name: str | None = None,
) -> list[str]:
    """Construye un comando de test con el framework del proyecto.

    - `coding.test_command` declarado: se usa literal (sin filtros ajenos).
    - target: path de archivo/dir → se anexa al runner (pytest tests/test_x.py).
    - name  : nombre de test → se aplica la flag de filtro (pytest -k test_x).
    """
    declared_command = _read_declared_test_command(project_root)
    if declared_command:
        return shlex.split(declared_command)
    runner = _resolve_test_command(project_root)
    key = _normalize_test_framework(
        _read_declared_framework(project_root)
    )
    cmd = list(runner)
    if name:
        flag = _TEST_NAME_FLAG.get(key, ["-k"])
        if flag:
            cmd += flag + [name]
        else:  # cargo: el nombre va directo como argumento de filtro
            cmd += [name]
    elif target:
        cmd += [target]
    return cmd


def _read_declared_framework(project_root: str) -> str:
    """`coding.test_framework` del manifiesto, resuelto por ProjectService.

    Antes leía `<root>/data/project.json`: con el layout real
    (`.cognitive/projects/<pid>/data/project.json`) esa ruta no existe y todo
    caía al default pytest. Medido: un proyecto con `vitest` declarado
    ejecutaba `python3 -m pytest -k ...`.
    """
    declared = ""
    for path in _manifest_candidates(project_root):
        if not os.path.isfile(path):
            continue
        try:
            with open(path) as f:
                manifest = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        project = manifest.get("project", manifest)
        coding = project.get("coding", {}) or {}
        # Solo la declaración explícita. `identity.framework`/`language` NO son
        # un runner (django/fastapi/python no dicen cómo se corren los tests).
        declared = str(coding.get("test_framework") or "").strip()
        break
    return declared


def _read_declared_test_command(project_root: str) -> str:
    """`coding.test_command` del manifiesto: el gate del proyecto, literal.

    Hay proyectos cuyo runner no está en `_TEST_RUNNERS` (Django `manage.py
    test`, un script propio, un Makefile target). Sin esta vía solo quedaban
    dos salidas: mentir declarando un framework ajeno o no poder certificar.
    El comando se ejecuta con `shlex.split`, tal cual lo declaró el proyecto.
    """
    for path in _manifest_candidates(project_root):
        if not os.path.isfile(path):
            continue
        try:
            with open(path) as f:
                manifest = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        project = manifest.get("project", manifest)
        coding = project.get("coding", {}) or {}
        return str(coding.get("test_command") or "").strip()
    return ""


def _manifest_candidates(project_root: str) -> list[str]:
    """Rutas del manifiesto en orden de precedencia (layout v2 y legacy)."""
    candidates: list[str] = []
    try:
        from fast_mcp.shared_kernel.project_support import ProjectService

        paths = ProjectService.resolve_paths(project_root)
        data_dir = str(getattr(paths, "data_dir", "") or "")
        if data_dir:
            candidates.append(os.path.join(data_dir, "project.json"))
    except Exception:  # noqa: BLE001 — sin registro se prueba el legacy
        pass
    # Legacy: manifiesto en la raíz del repo (proyectos sin layout v2) y en
    # `<root>/data/project.json` (layout anterior a `.cognitive/`).
    candidates.append(os.path.join(project_root, "project.json"))
    candidates.append(os.path.join(project_root, "data", "project.json"))
    return candidates


def _looks_like_test_target(command: str) -> str | None:
    """Devuelve target/name si ``command`` es test target puro; None si es comando.

    Un test target es: un nombre (test_x, TestFoo, app_test) o un path
    relativo de archivo (tests/test_x.py, src/x.test.js). Un comando real
    (con espacios, flags, pipes) se ejecuta tal cual.
    """
    text = command.strip()
    if not text or any(ch in text for ch in " \t\n"):
        return None
    if "/" in text or "." in text:  # path relativo → target
        return "target"
    if re.match(r"^(test_|Test|it_)", text):  # nombre de test → filtro -k/-t
        return "name"
    return None


# ═══════════════════════════════════════════════════════════════════
# HANDLERS DE STRING
# ═══════════════════════════════════════════════════════════════════


def _verify_natural_language(text: str, project_root: str) -> tuple[bool, dict]:
    """Verifica un predicate de lenguaje natural (ast-grep)."""
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
    """Ejecuta un predicate string como comando shell."""
    try:
        result = subprocess.run(
            text,
            shell=True,
            capture_output=True,
            text=True,
            timeout=verify_timeout_s(),
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
            "error": f"timeout ({verify_timeout_s()}s)",
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
    """symbol_exists: verifica que un símbolo exista en un archivo (ast-grep)."""
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
    """symbol_not_exists: verifica que un símbolo NO exista (ast-grep)."""
    symbol = predicate.get("symbol", "")
    file_hint = predicate.get("file")
    if not symbol:
        return False, {
            "method": "symbol_not_exists",
            "error": "campo 'symbol' requerido",
            "label": "symbol_not_exists: campo 'symbol' requerido",
        }
    try:
        passed = run_verification(
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
    """command_exit: ejecuta un comando y verifica exit code 0."""
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
            timeout=verify_timeout_s(),
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
            "error": f"timeout ({verify_timeout_s()}s)",
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
    """command_output: ejecuta un comando y verifica que contenga texto."""
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
            timeout=verify_timeout_s(),
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
    """test_pass: ejecuta el test framework del proyecto y verifica que pase.

    G-W6 (framework-aware): el comando se interpreta según su forma, nunca
    ejecutando un nombre de test como binario shell:

      - "test_foo" / "TestFoo"     → {runner} -k <name>   (pytest -k, jest -t...)
      - "tests/test_x.py"          → {runner} <path>
      - comando completo con args  → se ejecuta tal cual (autoridad del autor)
    """
    command = predicate.get("command", "")
    if not command:
        return False, {
            "method": "test_pass",
            "error": "campo 'command' requerido",
            "label": "test_pass: campo 'command' requerido",
        }

    interpretation = _looks_like_test_target(command)
    try:
        if interpretation == "name":
            argv = build_test_command(project_root, name=command)
        elif interpretation == "target":
            argv = build_test_command(project_root, target=command)
        else:
            argv = [command]  # comando explícito del autor → shell tal cual
    except TestRunnerUnavailable as exc:
        return False, {
            "method": "test_pass",
            "error": str(exc),
            "label": str(exc)[:70],
        }

    label = " ".join(argv)[:70]
    try:
        result = subprocess.run(
            argv,
            shell=(interpretation is None),  # targets/names → exec directo
            capture_output=True,
            text=True,
            timeout=120,
            cwd=project_root,
        )
        return result.returncode == 0, {
            "method": "test_pass",
            "command": label,
            "exit_code": result.returncode,
            "label": label,
        }
    except subprocess.TimeoutExpired:
        return False, {
            "method": "test_pass",
            "command": label,
            "error": "timeout (120s)",
            "label": label,
        }
    except Exception as exc:
        return False, {
            "method": "test_pass",
            "command": label,
            "error": str(exc),
            "label": label,
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
    """Ejecuta ast-grep batch para múltiples symbol_exists predicates.

    Estrategia:
      1. Agrupa símbolos por file hint resuelto.
      2. Ejecuta ``sg`` UNA VEZ por archivo con todos los símbolos.
      3. Símbolos sin file_hint resoluble → verificación individual.
    """
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
        _verify_symbols_in_file(fpath, items, project_root, all_predicates)

    for idx in individual:
        _verify_individual_symbol(idx, indices, symbol_preds, project_root, all_predicates)


def _verify_symbols_in_file(
    fpath: str,
    items: list[tuple[int, str, dict]],
    project_root: str,
    all_predicates: list,
) -> None:
    """Verifica símbolos agrupados de un archivo via ast-grep batch."""
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
            sg_args = [AST_GREP_BIN, "-l", lang, "--json"]
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
                _grep_fallback_symbol(s, fpath, symbol_found)

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


def _grep_fallback_symbol(s: str, fpath: str, symbol_found: dict) -> None:
    """Fallback con grep -n para símbolos no detectados por ast-grep."""
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


def _verify_individual_symbol(
    idx: int,
    indices: list[int],
    symbol_preds: list[dict],
    project_root: str,
    all_predicates: list,
) -> None:
    """Verifica un símbolo sin file_hint resoluble de forma individual."""
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

    Utiliza cache de sesión (TTL 5 min). Los comandos shell se cachean
    (idempotentes por contrato del motor).

    Args:
        predicate: String (lenguaje natural o comando shell) o dict estructurado.
        project_root: Ruta absoluta a la raíz del proyecto.

    Returns:
        (passed: bool, info: dict with method, detail, error)
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
    """Verifica múltiples predicates en batch con optimización ast-grep.

    Para ``symbol_exists`` agrupa símbolos por archivo y ejecuta ``sg``
    una vez por grupo. Para otros tipos delega en ``verify_predicate()``
    (que ya usa cache).

    Args:
        predicates: Lista de predicates (strings o dicts).
        project_root: Ruta absoluta del proyecto.

    Returns:
        Lista de (passed, info) en el mismo orden que la entrada.
    """
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


def current_revision(project_root: str = "") -> str:
    """SHA corto de HEAD (o '' si no hay git) — para manifests/stale."""
    try:
        import subprocess

        proc = subprocess.Popen(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=project_root or ".",
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        out, _ = proc.communicate(timeout=5)
        if proc.returncode == 0:
            return (out or "").strip()
        return 
    except Exception:  # noqa: BLE001
        return ""
