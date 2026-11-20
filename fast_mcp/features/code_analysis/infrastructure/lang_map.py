"""infrastructure/lang_map.py — SSOT extensión → lenguaje.

Antes esta tabla estaba duplicada en `codereview/config.py` y
`ast_grep_adapter.py` (idénticas). Dos copias del mismo dato divergen: una
agrega `.mjs` y la otra no, y el análisis empieza a ver lenguajes distintos
según el camino. Esta es la única fuente.

Es un módulo hoja (no importa nada del paquete) para poder usarlo desde
`lang_imports` sin ciclo con `codereview/__init__`.
"""
from __future__ import annotations

from pathlib import Path

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
    ".sh": "bash",
    ".bash": "bash",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".css": "css",
    ".html": "html",
    ".md": "markdown",
}


def language_for_extension(path: str | Path) -> str | None:
    """Lenguaje ast-grep de un path (None si la extensión no es de código)."""
    return LANG_MAP.get(Path(path).suffix.lower())
