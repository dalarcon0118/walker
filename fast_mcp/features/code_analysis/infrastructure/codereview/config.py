"""Configuración y constantes para el sistema de code review.

Sin dependencia del binario externo qlty (ADR-WALKER-054): solo ast-grep
y los patrones deep propios.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from returns.pipeline import is_successful
from returns.result import safe

logger = logging.getLogger(__name__)


def _probe_binary(bin_name: str) -> bool:
    """Detecta si un binario está disponible en el PATH.

    Result pattern: el core @safe nunca lanza; ante fallo devuelve False
    y loggea en debug (convention-001).
    """
    result = _probe_binary_core(bin_name)
    if is_successful(result):
        return result.value_or(False)
    error = result.failure()
    logger.debug(
        "binario %s no disponible (%s: %s)",
        bin_name, type(error).__name__, error,
    )
    return False


@safe
def _probe_binary_core(bin_name: str) -> bool:
    """Core puro de _probe_binary (puede lanzar FileNotFoundError/TimeoutExpired)."""
    r = subprocess.run(
        [bin_name, "--version"], capture_output=True, text=True, timeout=5
    )
    return r.returncode == 0


# ── ast-grep binary ─────────────────────────────────────────
AST_GREP = os.environ.get("AST_GREP_BIN") or "ast-grep"
AST_GREP_AVAILABLE = _probe_binary(AST_GREP)


# ── Language map: file extension → ast-grep language ────────
from ..lang_map import LANG_MAP as _LANG_MAP


def ast_grep_lang(filepath: Path) -> str | None:
    """Map file extension to ast-grep language name."""
    return _LANG_MAP.get(filepath.suffix.lower())


# ── Deep analysis patterns ──────────────────────────────────
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
    # ── B1xx: hardcoded creds, assert, exec ──
    "B101": [
        {
            "id": "assert-used",
            "pattern": "assert $X",
            "label": "assert — se desactiva con -O (producción)",
            "severity": "medium",
        }
    ],
    "B102": [
        {
            "id": "exec-used",
            "pattern": "exec($X)",
            "label": "exec() — ejecución dinámica de código",
            "severity": "high",
        }
    ],
    "B105": [
        {
            "id": "hardcoded-password",
            "pattern": "$$$ = $$$password$$$",
            "label": "posible hardcoded password/secret",
            "severity": "high",
        }
    ],
    # ── B6xx: injection ──
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
            "pattern": "$$$.execute(f$$$$$$)",
            "label": "SQL query con f-string — SQL injection",
            "severity": "critical",
        },
        {
            "id": "sql-concat",
            "pattern": "$$$.execute($X + $Y)",
            "label": "SQL query con concatenación — SQL injection",
            "severity": "critical",
        },
    ],
    # ── E402: import not at top ──
    "E402": [
        {
            "id": "import-in-func",
            "pattern": "def $NAME:\n  $$$$$$$import $$$",
            "label": "import dentro de función",
            "severity": "low",
        },
        {
            "id": "import-in-class",
            "pattern": "class $NAME:\n  $$$$$$$import $$$",
            "label": "import dentro de clase",
            "severity": "low",
        },
        {
            "id": "import-in-except",
            "pattern": "except:\n  import $$$",
            "label": "import dentro de except (late import)",
            "severity": "low",
        },
    ],
    # ── F401: unused import ──
    "F401": [
        {
            "id": "unused-import-exact",
            "pattern": "import $X",
            "label": "import no usado — eliminar",
            "severity": "low",
        },
        {
            "id": "unused-from-import",
            "pattern": "from $X import $Y",
            "label": "from import no usado — eliminar",
            "severity": "low",
        },
    ],
    # ── F541: f-string without placeholders ──
    "F541": [
        {
            "id": "fstring-static",
            "pattern": "f'$$$'",
            "label": "f-string sin placeholders — usar str",
            "severity": "low",
        },
        {
            "id": "fstring-static-double",
            "pattern": 'f"$$$"',
            "label": "f-string sin placeholders — usar str",
            "severity": "low",
        },
        {
            "id": "fstring-format-like",
            "pattern": 'f"{$$$}"',
            "label": "f-string con solo variable — redundante",
            "severity": "info",
        },
        {
            "id": "fstring-format-like-single",
            "pattern": "f'{$$$}'",
            "label": "f-string con solo variable — redundante",
            "severity": "info",
        },
        {
            "id": "fstring-multi-stmt",
            "pattern": "f'{$$$}{$$$}'",
            "label": "f-string multicomponente sin texto estático",
            "severity": "info",
        },
        {
            "id": "fstring-expr",
            "pattern": "f'{$$$}'",
            "label": "f-string con expresión — evaluar si es legible",
            "severity": "info",
        },
        {
            "id": "fstring-url",
            "pattern": "f'https://$$$'",
            "label": "URL construida con f-string — preferir .format()",
            "severity": "info",
        },
    ],
    # ── Danger / security patterns ──
    "danger": [
        {
            "id": "print-prod",
            "pattern": "print($$$)",
            "label": "print en producción — logging?",
            "severity": "low",
        },
        {
            "id": "hardcoded-path",
            "pattern": "open('/$$$')",
            "label": "path hardcoded — usar config/Path",
            "severity": "low",
        },
        {
            "id": "shell-true",
            "pattern": "shell=True",
            "label": "shell=True — riesgo de shell injection",
            "severity": "high",
        },
        {
            "id": "no-timeout",
            "pattern": "requests.get($$$)",
            "label": "requests.get() sin timeout",
            "severity": "medium",
        },
        {
            "id": "no-timeout-post",
            "pattern": "requests.post($$$)",
            "label": "requests.post() sin timeout",
            "severity": "medium",
        },
        {
            "id": "mutable-default",
            "pattern": "def $NAME($$$=[]):",
            "label": "lista mutable como default param",
            "severity": "high",
        },
        {
            "id": "mutable-default-dict",
            "pattern": "def $NAME($$$={}):",
            "label": "dict mutable como default param",
            "severity": "high",
        },
        {
            "id": "eval-used",
            "pattern": "eval($X)",
            "label": "eval() — ejecución dinámica de código",
            "severity": "high",
        },
        {
            "id": "exec-used",
            "pattern": "exec($X)",
            "label": "exec() — ejecución dinámica de código",
            "severity": "high",
        },
        {
            "id": "pickle-load",
            "pattern": "pickle.load($X)",
            "label": "pickle.load() — deserialización insegura",
            "severity": "critical",
        },
        {
            "id": "yaml-load",
            "pattern": "yaml.load($X)",
            "label": "yaml.load() sin Loader — deserialización insegura",
            "severity": "high",
        },
        {
            "id": "except-pass",
            "pattern": "except:\n  pass",
            "label": "except vacío — traga errores",
            "severity": "high",
        },
        {
            "id": "os-call",
            "pattern": "os.system($X)",
            "label": "os.system() — shell injection",
            "severity": "high",
        },
        {
            "id": "eval-call",
            "pattern": "eval($X)",
            "label": "eval() — ejecución dinámica",
            "severity": "critical",
        },
        {
            "id": "pickle-load",
            "pattern": "pickle.load($X)",
            "label": "pickle.load() — deserialización insegura",
            "severity": "critical",
        },
        {
            "id": "try-bare",
            "pattern": "except:",
            "label": "except sin tipo — traga TODO",
            "severity": "medium",
        },
        {
            "id": "assert-used",
            "pattern": "assert $X",
            "label": "assert — se desactiva con -O",
            "severity": "medium",
        },
        {
            "id": "exec-used",
            "pattern": "exec($X)",
            "label": "exec() — código dinámico",
            "severity": "high",
        },
        {
            "id": "no-timeout-requests",
            "pattern": "requests.$METHOD($$$)",
            "label": "requests sin timeout",
            "severity": "high",
        },
        {
            "id": "sql-format",
            "pattern": "$$$.execute(f$$$$$$)",
            "label": "SQL con f-string",
            "severity": "critical",
        },
        {
            "id": "sql-concat",
            "pattern": "$$$.execute($X + $Y)",
            "label": "SQL con concatenación",
            "severity": "critical",
        },
    ],
    # ── High-cyclo patterns ──
    "high-cyclo": [
        {
            "id": "nested-if",
            "pattern": "if $A:\n  if $B:",
            "label": "if anidado a 2 niveles — considerar guard clause",
            "severity": "medium",
        },
        {
            "id": "nested-if-3",
            "pattern": "if $A:\n  if $B:\n    if $C:",
            "label": "if anidado a 3+ niveles — refactorizar",
            "severity": "high",
        },
        {
            "id": "if-elif-chain",
            "pattern": "if $A:\n  $$$$\\nelif $B:",
            "label": "cadena if-elif larga — usar dict dispatch",
            "severity": "medium",
        },
        {
            "id": "long-function",
            "pattern": "def $X($$$):\\n  $$$$\\n  $$$$\\n  $$$$\\n  $$$$\\n  $$$$\\n  $$$$\\n  $$$$\\n  $$$$\\n  $$$$\\n  $$$$\\n  $$$$\\n  $$$$\\n  $$$$\\n  $$$$\\n  $$$$",
            "label": "función larga (>15 líneas de cuerpo)",
            "severity": "low",
        },
        {
            "id": "loop-over-range",
            "pattern": "for $X in range($$$):",
            "label": "for con range(len()) — preferir enumerate",
            "severity": "low",
        },
    ],
    # ── TypeScript/JavaScript patterns ──
    "ts-js": [
        {
            "id": "any-type",
            "pattern": ": any",
            "label": "uso de any — tipado débil",
            "severity": "medium",
        },
        {
            "id": "non-null-assert",
            "pattern": "$X!",
            "label": "Non-null assertion — puede explotar en runtime",
            "severity": "medium",
        },
        {
            "id": "console-log",
            "pattern": "console.log($X)",
            "label": "console.log en producción",
            "severity": "low",
        },
        {
            "id": "loose-equality",
            "pattern": "$X == $V",
            "label": "== en vez de === — comparación débil",
            "severity": "medium",
        },
    ],
    # ── Unnecessary code patterns (always run) ──
    "unnecessary-code": [
        {
            "id": "todo-comment",
            "pattern": "TODO",
            "label": "TODO pendiente",
            "severity": "low",
            "lang": "python",
        },
        {
            "id": "fixme-comment",
            "pattern": "FIXME",
            "label": "FIXME pendiente",
            "severity": "medium",
            "lang": "python",
        },
        {
            "id": "pass-in-class",
            "pattern": "class $NAME:\\n  pass",
            "label": "clase vacía — stub?",
            "severity": "low",
            "lang": "python",
        },
        {
            "id": "debugger-stmt",
            "pattern": "debugger",
            "label": "debugger statement",
            "severity": "medium",
            "lang": "javascript",
        },
        {
            "id": "todo-ts",
            "pattern": "// TODO",
            "label": "TODO pendiente",
            "severity": "low",
            "lang": "typescript",
        },
        {
            "id": "todo-js",
            "pattern": "// TODO",
            "label": "TODO pendiente",
            "severity": "low",
            "lang": "javascript",
        },
        {
            "id": "todo-rust",
            "pattern": "// TODO",
            "label": "TODO pendiente",
            "severity": "low",
            "lang": "rust",
        },
        {
            "id": "todo-java",
            "pattern": "// TODO",
            "label": "TODO pendiente",
            "severity": "low",
            "lang": "java",
        },
    ],
    # ── Production bugs (always run, language-specific) ──
    "production-bugs": [
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
        {
            "id": "panic-call",
            "pattern": "panic($X)",
            "label": "panic() — puede terminar el programa inesperadamente",
            "severity": "high",
            "lang": "go",
        },
        {
            "id": "null-return",
            "pattern": "return null;",
            "label": "return null — puede causar NullPointerException",
            "severity": "high",
            "lang": "java",
        },
    ],
}


# ── Quality rating thresholds ────────────────────────────────
_ESTIMATED_MINUTES_PER_LOC = 0.06  # ~3.6s per LOC
_MAINTAINABILITY_THRESHOLDS = [
    (0.05, "A"),
    (0.10, "B"),
    (0.20, "C"),
    (0.50, "D"),
]
_SECURITY_THRESHOLDS = [
    (0, "A"),
    (1, "B"),
    (3, "C"),
    (8, "D"),
]
