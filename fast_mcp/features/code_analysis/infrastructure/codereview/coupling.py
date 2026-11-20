"""Coupling metrics (Ce + Ca) multi-lenguaje.

Ce = imports externos (qué importa este archivo)
Ca = archivos que importan este módulo (quién lo importa)

La decisión "local vs externo" vive en `lang_imports` (un adapter por
lenguaje). Antes había dos hardcodes: una lista de prefijos de proyecto
(`cognitive_v2`, `mcp`, …) y Ca que solo miraba `*.py` con módulos punteados.
En un repo JS/Go, Ce contaba cualquier import como externo y Ca devolvía 0
como si nadie importara el archivo.

Cuando un lenguaje no tiene resolver, `ca_status` lo dice (`not_checked`) en
vez de devolver un cero que parece un dato.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from ..lang_imports import (
    detect_language,
    extract_imports,
    go_module,
    is_js,
    is_local_import,
    resolve_relative,
)

logger = logging.getLogger(__name__)

# Extensiones de los archivos que podrían importar al objetivo (por lenguaje).
_IMPORTER_SUFFIXES: dict[str, tuple[str, ...]] = {
    "python": (".py",),
    "javascript": (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"),
    "typescript": (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"),
    "go": (".go",),
    "rust": (".rs",),
    "java": (".java",),
    "kotlin": (".kt",),
}


def _project_root_for(filepath: Path) -> Path:
    for parent in [filepath.parent, *filepath.parents]:
        if (parent / ".git").exists() or (parent / ".cognitive").exists():
            return parent
    return filepath.parent


def _grep(root: Path, needle: str, suffixes: tuple[str, ...]) -> list[str]:
    """Archivos que contienen `needle` con alguna de esas extensiones."""
    if not needle:
        return []
    args = ["grep", "-rl", needle, str(root)]
    args.extend(f"--include=*{sfx}" for sfx in suffixes)
    result = subprocess.run(args, capture_output=True, text=True, timeout=10)
    return [line for line in (result.stdout or "").strip().split("\n") if line]


def _imported_by(filepath: Path, root: Path, language: str) -> tuple[list[str], str]:
    """Quién importa este archivo, con el método del lenguaje.

    Returns:
        (imported_by, ca_status) donde ca_status es "checked", "approximate"
        o "not_checked" (lenguaje sin resolver de Ca).
    """
    suffixes = _IMPORTER_SUFFIXES.get(language, ())
    if not suffixes:
        return [], "not_checked"

    try:
        if language == "python":
            try:
                rel = filepath.resolve().relative_to(root.resolve())
            except ValueError:
                return [], "not_checked"
            module = str(rel.with_suffix("")).replace("/", ".")
            if module.endswith(".__init__"):
                module = module[: -len(".__init__")]
            hits = _grep(root, module, suffixes)
            return [h for h in hits if h != str(filepath)], "approximate"

        if is_js(language):
            # El stem es el needle barato; la confirmación es resolver el
            # specifier relativo de cada candidato contra el archivo objetivo.
            target = filepath.resolve()
            confirmed: list[str] = []
            for hit in _grep(root, filepath.stem, suffixes):
                hit_path = Path(hit)
                if hit_path.resolve() == target:
                    continue
                for imp in extract_imports(hit_path, language):
                    spec = str(imp.get("module", ""))
                    if not spec.startswith("."):
                        continue
                    resolved = resolve_relative(spec, hit_path)
                    if resolved is not None and resolved.resolve() == target:
                        confirmed.append(hit)
                        break
            return confirmed, "checked"

        if language == "go":
            module = go_module(root)
            if not module:
                return [], "not_checked"
            rel = filepath.parent.resolve().relative_to(root.resolve())
            pkg = module if str(rel) == "." else f"{module}/{rel.as_posix()}"
            hits = _grep(root, pkg, suffixes)
            return [h for h in hits if h != str(filepath)], "approximate"
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        logger.warning(
            "Cálculo de Ca fallo para %s (%s: %s)", filepath, type(exc).__name__, exc
        )
        return [], f"unavailable: {type(exc).__name__}"

    return [], "not_checked"


def coupling_metrics(
    filepath: Path, compute_ca: bool = False, project_root: str | Path | None = None
) -> dict:
    """Ce y Ca del archivo, con el adapter de imports de su lenguaje.

    Args:
        filepath: Ruta al archivo a analizar.
        compute_ca: Si True, busca project-wide quién lo importa (lento).
        project_root: Raíz del proyecto; si falta, se sube buscando
            `.git`/`.cognitive`.
    """
    language = detect_language(filepath)
    if language is None or not filepath.exists():
        return {
            "ce": 0,
            "ca": 0,
            "imports": [],
            "imported_by": [],
            "status": "not_checked",
            "reason": (
                f"sin adapter de imports para "
                f"{filepath.suffix or '(sin extensión)'}"
            ),
            "ca_status": "not_checked",
        }

    root = Path(project_root) if project_root else _project_root_for(filepath)

    external: set[str] = set()
    local: set[str] = set()
    unknown: set[str] = set()
    for imp in extract_imports(filepath, language):
        spec = str(imp.get("module", ""))
        if not spec:
            continue
        verdict = is_local_import(spec, filepath, root)
        if verdict is True:
            local.add(spec)
        elif verdict is False:
            external.add(spec)
        else:
            unknown.add(spec)

    imported_by: list[str] = []
    ca_status = "not_computed"
    if compute_ca:
        imported_by, ca_status = _imported_by(filepath, root, language)

    return {
        "ce": len(external),
        "ca": len(imported_by),
        "imports": sorted(external),
        "imported_by": imported_by,
        "local_imports": sorted(local),
        "not_checked": sorted(unknown),
        "status": "partial" if unknown else "checked",
        "ca_status": ca_status,
    }
