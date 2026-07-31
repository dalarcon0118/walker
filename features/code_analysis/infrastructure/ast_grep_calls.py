"""infrastructure/ast_grep_calls.py — CALLS edge detection multi-lenguaje.

Usa ast-grep para detectar llamadas a funciones en cualquier lenguaje.
Reemplaza a ast_analyzer.py (Python-only con ast).

Cómo funciona:
  1. Para cada archivo, ejecuta ast-grep run con pattern '$FUNC($$$)'
     que captura TODAS las llamadas a funciones en cualquier lenguaje.
  2. Para cada llamada, detecta qué función CONTIENE esa llamada
     comparando el número de línea con los rangos de las definiciones.
  3. Devuelve caller, callee, line para crear CALLS edges en el CPG.
"""

from __future__ import annotations
import json
import os
import subprocess
from pathlib import Path

from features.dependency_checker import ensure_tools

AST_GREP_BIN = os.environ.get("AST_GREP_BIN") or "ast-grep"

# Mapeo extensión → lenguaje ast-grep
LANG_MAP: dict[str, str] = {
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
}


def detect_calls(file_path: str, definitions: list[dict]) -> list[dict]:
    """Detecta llamadas a funciones en un archivo multi-lenguaje.

    Args:
        file_path: Ruta absoluta al archivo.
        definitions: Lista de definiciones del formato:
            [{"name": "func_name", "line": N, "end_line": N}, ...]
            Obtenidas de ast-grep outline.

    Returns:
        [{"caller": "func_name", "callee": "called_name", "line": N}, ...]
    """
    lang = _detect_lang(file_path)
    if not lang or not os.path.exists(file_path):
        return []

    # Ejecutar ast-grep para encontrar TODAS las llamadas a funciones
    calls = _run_ast_grep_calls(file_path, lang)
    if not calls:
        return []

    # Para cada llamada, encontrar qué función la contiene
    # comparando el número de línea con los rangos de definiciones
    result = []
    for call in calls:
        call_line = call["line"]
        # Buscar la definición que contiene esta línea
        caller = _find_enclosing_function(call_line, definitions)
        if not caller:
            continue
        callee = call["name"]
        if caller == callee:
            continue  # Skip recursive calls a sí misma
        result.append(
            {
                "caller": caller,
                "callee": callee,
                "line": call_line,
            }
        )

    return result


def _run_ast_grep_calls(file_path: str, lang: str) -> list[dict]:
    """Ejecuta ast-grep run para encontrar llamadas a funciones.

    El pattern $FUNC($$$) captura cualquier llamada a función/método
    en cualquier lenguaje: func(), obj.method(), Class.method(), etc.

    Returns:
        [{"name": "function_name", "line": N}, ...]
    """
    ensure_tools(["ast-grep"])
    try:
        r = subprocess.run(
            [AST_GREP_BIN, "run", "--json", "-l", lang, "-p", "$FUNC($$$)", file_path],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if r.returncode != 0:
            return []
        data = json.loads(r.stdout)
        if not isinstance(data, list):
            return []

        results = []
        for m in data:
            # Extraer el nombre de la función llamada de metaVariables
            mv = m.get("metaVariables", {})
            single = mv.get("single", {})
            func_var = single.get("FUNC", {})
            func_name = func_var.get("text", "")

            if not func_name:
                # Fallback: extraer del texto
                text = m.get("text", "")
                func_name = text.split("(")[0].strip()

            if not func_name or func_name in ("?", ""):
                continue

            rng = m.get("range", {})
            start = rng.get("start", {})
            results.append(
                {
                    "name": func_name,
                    "line": start.get("line", 0) + 1,
                }
            )

        return results
    except (FileNotFoundError, json.JSONDecodeError, subprocess.TimeoutExpired):
        return []


def _find_enclosing_function(line: int, definitions: list[dict]) -> str | None:
    """Encuentra qué función contiene una línea dada.

    definitions es una lista de dicts con name, line, end_line.
    end_line puede ser None (se asume que termina donde empieza la siguiente).
    """
    best = None
    best_distance = float("inf")

    # Ordenar definiciones por línea ascendente para calcular end_line implícito
    sorted_defs = sorted(definitions, key=lambda d: d.get("line", 0))
    for i, d in enumerate(sorted_defs):
        def_start = d.get("line", 0)
        # end_line implícito: siguiente definición o infinito
        def_end = d.get("end_line", float("inf"))
        if def_end is None or def_end == 0:
            if i + 1 < len(sorted_defs):
                def_end = sorted_defs[i + 1].get("line", float("inf")) - 1
            else:
                def_end = float("inf")

        if def_start <= line <= def_end:
            distance = line - def_start
            if distance < best_distance:
                best_distance = distance
                best = d["name"]

    return best


def _detect_lang(file_path: str) -> str | None:
    return LANG_MAP.get(Path(file_path).suffix.lower())
