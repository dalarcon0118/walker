"""Coupling metrics (Ce + Ca) multi-lenguaje via ast-grep.

Ce = external imports (qué importa este archivo)
Ca = files importing this module (quién importa este archivo)

Return pattern: la rama Ca (grep project-wide) falla abierto con log
(convention-001: nunca except silencioso).
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from .ast_grep import find_imports, ast_grep_lang

logger = logging.getLogger(__name__)

# Prefixes conocidos de proyectos que NO son externos
_PROJECT_PREFIXES = {
    "cognitive_v2",
    "mcp",
    "fastmcp",
    "helpers",
    "session",
    "claude",
    "sqlalchemy",
    "pydantic",
    "fastapi",
}


def coupling_metrics(filepath: Path, compute_ca: bool = False) -> dict:
    """Ce y Ca para cualquier lenguaje via ast-grep.

    Ce: imports externos detectados con ast-grep.
    Ca: quién importa este archivo (grep project-wide, caro).

    Args:
        filepath: Ruta al archivo a analizar
        compute_ca: Si True, corre grep project-wide (lento)
    """
    lang = ast_grep_lang(filepath)
    if not lang or not filepath.exists():
        return {"ce": 0, "ca": 0, "imports": [], "imported_by": []}

    # Ce: imports desde ast-grep
    imports = find_imports(filepath, lang)
    external = {i for i in imports if i not in _PROJECT_PREFIXES}

    # Ca: project-wide grep (solo si se pide)
    imported_by: list[str] = []
    if compute_ca:
        try:
            # Walk up to find project root
            project_root = filepath
            for parent in [filepath] + list(filepath.parents):
                if (parent / ".git").exists() or (parent / ".cognitive").exists():
                    project_root = parent
                    break
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
        except (ValueError, OSError, subprocess.TimeoutExpired) as e:
            logger.warning(
                "Cálculo de Ca fallo para %s (%s: %s); Ca=0",
                filepath, type(e).__name__, e,
            )

    return {
        "ce": len(external),
        "ca": len(imported_by),
        "imports": sorted(external),
        "imported_by": imported_by,
    }
