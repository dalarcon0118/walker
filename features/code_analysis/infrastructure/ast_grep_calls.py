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
import logging
import os
import subprocess
from pathlib import Path

from returns.pipeline import is_successful
from returns.result import safe

from features.dependency_checker import ensure_tools

logger = logging.getLogger(__name__)

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

    # Ejecutar ast-grep para encontrar TODAS las llamadas a funciones.
    # _run_ast_grep_calls ya devuelve la lista extraida (wrapper con log).
    calls = _run_ast_grep_calls(file_path, lang)
    if not calls:
        return []

    # Para cada llamada, encontrar qué función la contiene
    # comparando el número de línea con los rangos de definiciones
    result_calls = []
    for call in calls:
        call_line = call["line"]
        # Buscar la definición que contiene esta línea
        caller = _find_enclosing_function(call_line, definitions)
        if not caller:
            continue
        callee = call["name"]
        if caller == callee:
            continue  # Skip recursive calls a sí misma
        result_calls.append(
            {
                "caller": caller,
                "callee": callee,
                "line": call_line,
            }
        )

    return result_calls


def _run_ast_grep_calls(file_path: str, lang: str) -> list[dict]:
    """Ejecuta ast-grep run para encontrar llamadas a funciones.

    El pattern $FUNC($$$) captura cualquier llamada a función/método
    en cualquier lenguaje: func(), obj.method(), Class.method(), etc.

    Returns:
        [{"name": "function_name", "line": N}, ...]

    Result pattern: el core @safe nunca lanza; ante fallo devuelve []
    y loggea el error (convention-001).
    """
    result = _run_ast_grep_calls_core(file_path, lang)
    if is_successful(result):
        return result.value_or([])
    error = result.failure()
    logger.error(
        "ast-grep calls en %s fallo (%s: %s); sin calls detectados",
        file_path, type(error).__name__, error,
    )
    return []


@safe
def _run_ast_grep_calls_core(file_path: str, lang: str) -> list[dict]:
    """Core puro de _run_ast_grep_calls (puede lanzar)."""
    ensure_tools(["ast-grep"])
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


# ── Caché incremental de calls por archivo (mtime/size) ─────────
# Optimización del CFG scan: evita re-ejecutar ast-grep sobre archivos
# que no cambiaron desde el último análisis. Se persiste en data_dir.


def _file_signature(file_path: str) -> dict | None:
    """Snapshot mtime+size del archivo. None si no existe.

    Result pattern: el core @safe nunca lanza; ante fallo devuelve None
    y loggea en debug (convention-001).
    """
    result = _file_signature_core(file_path)
    if is_successful(result):
        return result.value_or(None)
    error = result.failure()
    logger.debug(
        "no se pudo obtener firma de %s (%s: %s)",
        file_path, type(error).__name__, error,
    )
    return None


@safe
def _file_signature_core(file_path: str) -> dict | None:
    """Core puro de _file_signature (puede lanzar OSError)."""
    st = os.stat(file_path)
    return {"mtime": st.st_mtime, "size": st.st_size}


class CallsCache:
    """Cache de calls por archivo (clave = realpath + mtime/size)."""

    def __init__(self, cache_path: str | None = None):
        self._cache_path = cache_path
        self._data: dict = self._load()
        self._hits = 0

    def _load(self) -> dict:
        if not self._cache_path or not os.path.exists(self._cache_path):
            return {"version": 1, "files": {}}
        result = _load_cache_core(self._cache_path)
        if is_successful(result):
            return result.value_or({"version": 1, "files": {}})
        error = result.failure()
        logger.warning(
            "CallsCache ilegible (%s: %s); se reinicia el cache",
            type(error).__name__, error,
        )
        return {"version": 1, "files": {}}

    def save(self) -> None:
        if not self._cache_path:
            return
        result = _save_cache_core(self._cache_path, self._data)
        if is_successful(result):
            return
        error = result.failure()
        logger.error(
            "CallsCache.save fallo (%s: %s); el cache no se persiste",
            type(error).__name__, error,
        )

    def get(self, file_path: str) -> list[dict] | None:
        """Retorna calls cacheados si el archivo no ha cambiado."""
        key = os.path.realpath(file_path)
        entry = self._data["files"].get(key)
        if not entry:
            return None
        sig = _file_signature(file_path)
        if sig is None:
            return None
        stored = entry.get("_file")
        if (
            stored
            and stored.get("mtime") == sig["mtime"]
            and stored.get("size") == sig["size"]
        ):
            self._hits += 1
            return entry.get("calls", [])
        return None

    def set(self, file_path: str, calls: list[dict]) -> None:
        key = os.path.realpath(file_path)
        sig = _file_signature(file_path)
        if sig is None:
            return
        self._data["files"][key] = {"_file": sig, "calls": calls}

    @property
    def stats(self) -> dict:
        return {
            "cached_files": len(self._data.get("files", {})),
            "hits": self._hits,
        }


@safe
def _load_cache_core(cache_path: str) -> dict:
    """Core puro de CallsCache._load (puede lanzar JSONDecodeError/OSError)."""
    with open(cache_path) as f:
        data = json.load(f)
    if data.get("version") == 1:
        return data
    return {"version": 1, "files": {}}


@safe
def _save_cache_core(cache_path: str, data: dict) -> None:
    """Core puro de CallsCache.save (puede lanzar OSError)."""
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    tmp = cache_path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, cache_path)


def detect_calls_cached(
    file_path: str,
    definitions: list[dict],
    cache: CallsCache | None = None,
) -> tuple[list[dict], bool]:
    """detect_calls con caché incremental.

    Returns (calls, from_cache): from_cache=True si vino del caché.
    """
    if cache is not None:
        cached = cache.get(file_path)
        if cached is not None:
            return cached, True
    calls = detect_calls(file_path, definitions)
    if cache is not None:
        cache.set(file_path, calls)
    return calls, False
