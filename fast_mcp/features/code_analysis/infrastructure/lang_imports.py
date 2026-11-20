"""infrastructure/lang_imports.py — resolución de imports por LENGUAJE.

El análisis de imports no puede asumir Python: cada lenguaje expresa lo mismo
de forma distinta (módulos punteados, rutas relativas, module path de Go,
`crate::` de Rust). Este módulo centraliza dos decisiones que antes estaban
repartidas entre `hallucination.py` y `coupling.py`:

  1. Extraer los specifiers de import de un archivo.
  2. Decidir si un specifier apunta DENTRO del proyecto.

Contrato importante: `None` significa "no sé" (lenguaje sin adapter), nunca
`False` por defecto. El caller decide si eso se reporta como `not_checked`;
devolver `False` convertiría la ignorancia en "import externo" y el silencio
en una métrica falsa.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

from .lang_map import language_for_extension

# `.mjs`/`.cjs` son JavaScript aunque ast-grep solo mapee `.js`.
_EXT_ALIASES: dict[str, str] = {".mjs": "javascript", ".cjs": "javascript"}

JS_LANGUAGES = {"javascript", "typescript", "tsx", "jsx"}

# Extensiones que puede tener el archivo destino de un import relativo JS/TS.
_JS_RESOLVE_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".json")

# Prefijos de alias habituales (`@/components`, `~/lib`). Sin leer tsconfig no
# se pueden resolver: se reportan como `not_checked`, no como alucinación.
_ALIAS_PREFIXES = ("@/", "~/")


def detect_language(path: str | Path) -> str | None:
    """Lenguaje por extensión (ast-grep + alias de JS moderno)."""
    p = Path(path)
    return _EXT_ALIASES.get(p.suffix.lower()) or language_for_extension(p)


def is_js(language: str | None) -> bool:
    return language in JS_LANGUAGES


def package_name(spec: str) -> str:
    """Nombre de paquete de un specifier (`@scope/pkg/sub` → `@scope/pkg`)."""
    if spec.startswith("@"):
        return "/".join(spec.split("/")[:2])
    return spec.split("/")[0]


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def project_package_names(project_root: str | Path) -> set[str]:
    """Nombres por los que el propio proyecto se puede importar (JS/TS).

    Incluye el `name` del package.json raíz y el de cada workspace declarado
    (monorepos). Sin esto, `import x from "@miapp/ui"` parecía externo.
    """
    root = Path(project_root)
    names: set[str] = set()
    pkg = _load_json(root / "package.json")
    if isinstance(pkg.get("name"), str):
        names.add(pkg["name"])
    workspaces = pkg.get("workspaces")
    dirs: list[str] = []
    if isinstance(workspaces, list):
        dirs = [str(w) for w in workspaces]
    elif isinstance(workspaces, dict) and isinstance(workspaces.get("packages"), list):
        dirs = [str(w) for w in workspaces["packages"]]
    for rel in dirs:
        if "*" in rel:
            for child in root.glob(rel):
                child_pkg = _load_json(child / "package.json")
                if isinstance(child_pkg.get("name"), str):
                    names.add(child_pkg["name"])
        else:
            child_pkg = _load_json(root / rel / "package.json")
            if isinstance(child_pkg.get("name"), str):
                names.add(child_pkg["name"])
    return names


def go_module(project_root: str | Path) -> str:
    """Module path declarado en go.mod ('' si no hay)."""
    try:
        for line in (Path(project_root) / "go.mod").read_text().splitlines():
            line = line.strip()
            if line.startswith("module "):
                return line.split(None, 1)[1].strip()
    except OSError:
        return ""
    return ""


def declared_dependency(spec: str, project_root: str | Path, language: str) -> bool:
    """True si el proyecto DECLARA la dependencia externa.

    Declarada-pero-no-instalada no es una alucinación: es un `npm install`
    pendiente. Se reporta como warning en el detector.
    """
    if is_js(language):
        if spec.startswith("node:"):
            return True
        root = Path(project_root)
        pkg = _load_json(root / "package.json")
        declared: set[str] = set()
        for key in (
            "dependencies",
            "devDependencies",
            "peerDependencies",
            "optionalDependencies",
        ):
            declared.update((pkg.get(key) or {}).keys())
        if package_name(spec) in declared:
            return True
        workspaces = pkg.get("workspaces")
        rels = (
            workspaces
            if isinstance(workspaces, list)
            else (workspaces.get("packages", []) if isinstance(workspaces, dict) else [])
        )
        for rel in rels:
            children = (
                list(root.glob(str(rel))) if "*" in str(rel) else [root / str(rel)]
            )
            for child in children:
                child_pkg = _load_json(child / "package.json")
                deps = {
                    **(child_pkg.get("dependencies") or {}),
                    **(child_pkg.get("devDependencies") or {}),
                }
                if package_name(spec) in deps:
                    return True
        return False
    if language == "go":
        try:
            return spec in (Path(project_root) / "go.mod").read_text()
        except OSError:
            return False
    return False


def is_alias_import(spec: str, project_root: str | Path) -> bool:
    """Alias de path (`@/…`, `~/…`, `#internal`) que no se puede resolver acá."""
    if spec.startswith(_ALIAS_PREFIXES):
        return True
    if spec.startswith("#"):
        pkg = _load_json(Path(project_root) / "package.json")
        imports = pkg.get("imports")
        if isinstance(imports, dict):
            return any(
                spec == key or spec.startswith(key.rstrip("*")) for key in imports
            )
    return False


def resolve_relative(spec: str, source: str | Path) -> Path | None:
    """Resuelve un import relativo JS/TS a un archivo real (None si no existe)."""
    base = (Path(source).parent / spec).resolve()
    candidates = [base]
    if base.suffix not in _JS_RESOLVE_SUFFIXES:
        candidates.extend(base.with_name(base.name + sfx) for sfx in _JS_RESOLVE_SUFFIXES)
        candidates.extend(base / f"index{sfx}" for sfx in _JS_RESOLVE_SUFFIXES)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def is_local_import(
    spec: str, source: str | Path, project_root: str | Path
) -> bool | None:
    """¿El specifier apunta a código DENTRO del proyecto?

    Returns:
        True  → local (relative, first-party package, módulo de Go, crate::).
        False → externo (terceros/stdlib).
        None  → el lenguaje no tiene adapter / el specifier no es resoluble
                sin más contexto (alias de paths) → `not_checked`, no mentira.
    """
    if not spec:
        return False
    language = detect_language(source)
    if language is None:
        return None

    if spec.startswith("."):
        return True  # relativo = local en todos los lenguajes que lo usan

    root = Path(project_root)

    if is_js(language):
        if spec.startswith("node:"):
            return False
        if is_alias_import(spec, root):
            return None
        return package_name(spec) in project_package_names(root)

    if language == "python":
        top = spec.lstrip(".").split(".")[0]
        if not top:
            return True
        return (
            (root / top).exists()
            or (root / f"{top}.py").exists()
            or (root / "src" / top).exists()
            or (root / "lib" / top).exists()
        )

    if language == "go":
        module = go_module(root)
        return bool(module) and (spec == module or spec.startswith(module + "/"))

    if language == "rust":
        return spec.startswith(("crate::", "self::", "super::"))

    if language in ("java", "kotlin", "scala", "csharp"):
        parts = [p for p in re.split(r"[.:]", spec) if p]
        if not parts:
            return False
        rel = Path(*parts)
        for base in (
            "src/main/java",
            "src/main/kotlin",
            "src/main/scala",
            "src/test/java",
            "src/test/kotlin",
            "src",
            "",
        ):
            if (root / base / rel).exists():
                return True
        return False

    if language == "ruby":
        return False  # `require` usa load path; `require_relative` ya es "."

    # Lenguaje conocido por ast-grep pero sin adapter de imports (c/cpp/php/…).
    return None


# ═══════════════════════════════════════════════════════════════
# Extracción de specifiers
# ═══════════════════════════════════════════════════════════════

_JS_IMPORT_RE = (
    re.compile(r"""\bimport\s+(?:[^'"]*?\s+from\s+)?['"]([^'"]+)['"]"""),
    re.compile(r"""\bexport\s+[^'"]*?\s+from\s+['"]([^'"]+)['"]"""),
    re.compile(r"""\brequire\s*\(\s*['"]([^'"]+)['"]\s*\)"""),
    re.compile(r"""\bimport\s*\(\s*['"]([^'"]+)['"]\s*\)"""),
)


def _extract_js(path: Path) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return out
    for line_num, line in enumerate(lines, 1):
        for pattern in _JS_IMPORT_RE:
            for match in pattern.finditer(line):
                spec = match.group(1)
                if spec in seen:
                    continue
                seen.add(spec)
                out.append(
                    {"module": spec, "symbols": [], "line": line_num, "kind": "import"}
                )
    return out


def _extract_python(path: Path) -> list[dict]:
    try:
        tree = ast.parse(path.read_text(errors="replace"))
    except (SyntaxError, OSError):
        return []
    out: list[dict] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.append(
                    {"module": alias.name, "symbols": [], "line": node.lineno, "kind": "import"}
                )
        elif isinstance(node, ast.ImportFrom):
            out.append(
                {
                    "module": "." * node.level + (node.module or ""),
                    "symbols": [a.name for a in node.names],
                    "line": node.lineno,
                    "kind": "from",
                }
            )
    return out


def _extract_go(path: Path) -> list[dict]:
    out: list[dict] = []
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return out
    for match in re.finditer(r'^\s*(?:import\s+)?(?:\w+\s+)?"([^"]+)"', text, re.M):
        out.append({"module": match.group(1), "symbols": [], "line": 0, "kind": "import"})
    return out


def _extract_rust(path: Path) -> list[dict]:
    out: list[dict] = []
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return out
    for match in re.finditer(r"^\s*use\s+([A-Za-z_][\w:]*)", text, re.M):
        out.append({"module": match.group(1), "symbols": [], "line": 0, "kind": "use"})
    return out


def _extract_jvm(path: Path) -> list[dict]:
    out: list[dict] = []
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return out
    for match in re.finditer(r"^\s*import\s+([\w.]+)", text, re.M):
        out.append({"module": match.group(1), "symbols": [], "line": 0, "kind": "import"})
    return out


_EXTRACTORS = {
    "python": _extract_python,
    "go": _extract_go,
    "rust": _extract_rust,
    "java": _extract_jvm,
    "kotlin": _extract_jvm,
    "scala": _extract_jvm,
}


def extract_imports(path: str | Path, language: str | None = None) -> list[dict]:
    """Specifiers importados por un archivo, normalizados.

    Cada item: {"module": str, "symbols": list[str], "line": int, "kind": str}.
    Lista vacía = el lenguaje no tiene extractor (el caller reporta
    `not_checked`, no "sin imports").
    """
    p = Path(path)
    lang = language or detect_language(p)
    if lang is None:
        return []
    if is_js(lang):
        return _extract_js(p)
    extractor = _EXTRACTORS.get(lang)
    if extractor is None:
        return []
    return extractor(p)
