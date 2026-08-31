"""fast_mcp.core — Núcleo autónomo del server FastMCP (ADR-WALKER-071).

AUTOCONTENIDO: este paquete NO importa nada de `mcp/` (ni `app/`, `features/`,
`shared/`, `protocols/` — que viven ahí como fuente transitoria). Solo usa
stdlib + la lib oficial `mcp.server.fastmcp` + `fast_mcp.*`. La migración por
fases PORTA el código necesario a `fast_mcp/` (§0 #4: "migrando lo que existe
en mcp/ hacia ahí sin dejar shims"), de modo que al cierre `mcp/` se elimina
por completo (§0 #5) y `fast_mcp/` queda sirviendo el dominio.

Este módulo porta las piezas de base que los 3 casos de uso de la Fase 1
(prepare / list / status) necesitan:

  - Raíz del walker (donde vive `config/config.json` y `data/projects.json`).
  - Registro global de proyectos (`data/projects.json`) — mismo formato que el
    legacy (SSOT del contrato de datos, §2.2 R-05: `data/projects.json`).
  - Detección básica de proyecto (language/framework) para `prepare`.

Sin frameworks: json + pathlib + os (funciones puras o casi puras, convention).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path


# ── Raíz del walker (SSOT de rutas del registro global) ────────────


def walker_root() -> Path:
    """Raíz del proyecto walker (donde vive data/projects.json).

    1. Camina hacia arriba desde este archivo buscando `config/config.json`
       (port de app/path_resolver.get_walker_root).
    2. Fallback estable: el padre del paquete `fast_mcp/` — el ADR-071 §2 fija
       el layout con `fast_mcp/` en la raíz de walker. (No se usa el CWD como
       fallback: el server puede arrancar desde cualquier directorio.)
    """
    candidate = Path(__file__).resolve().parent
    for _ in range(8):
        if (candidate / "config" / "config.json").is_file():
            return candidate.resolve()
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent
    return Path(__file__).resolve().parent.parent


def registry_path() -> Path:
    """Registro global de proyectos: <walker_root>/data/projects.json."""
    return walker_root() / "data" / "projects.json"


def walkerignore_template() -> Path:
    return walker_root() / "data" / ".walkerignore"


# ── Registro global (data/projects.json — SSOT, R-05) ──────────────


def read_registry() -> dict:
    """Lee data/projects.json; {} si no existe o es inválido (fail-open)."""
    path = registry_path()
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:  # noqa: BLE001 - fail-open visible vía warning del caller
        return {}


def register_project(pid: str, name: str, repo_path: str) -> Path:
    """Registra/actualiza un proyecto en el registro global (mismo formato legacy).

    Formato SSOT (identico a app/orchestration/prepare_project_service):
        {"projects": {pid: {project_id, name, repo_path, active,
                            registered_at, last_accessed}}, "updated_at": ...}
    """
    registry = read_registry()
    now = datetime.now(timezone.utc).isoformat()
    projects = registry.setdefault("projects", {})
    existing = projects.get(pid, {})
    projects[pid] = {
        "project_id": pid,
        "name": name,
        "repo_path": repo_path,
        "active": True,
        "registered_at": existing.get("registered_at", now),
        "last_accessed": now,
    }
    registry["updated_at"] = now
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(registry, f, indent=2, ensure_ascii=False)
    return path


def resolve_project(pid: str) -> dict:
    """Resuelve la entrada del registro global para un project_id ({} si no está)."""
    return read_registry().get("projects", {}).get(pid, {})


def resolve_project_from_cwd(start: str | None = None) -> str:
    """Git-like: resuelve project_id desde el CWD (o `start`).

    1. .cognitive/projects/<pid>/ más cercano caminando hacia arriba.
    2. Fallback: el proyecto activo del registro global cuyo repo_path
       contenga el directorio de arranque.
    """
    base = Path(start or os.getcwd()).resolve()
    for _ in range(10):
        cog = base / ".cognitive"
        if cog.is_dir():
            projects_dir = cog / "projects"
            if projects_dir.is_dir():
                for entry in sorted(projects_dir.iterdir()):
                    if (entry / "data" / "project.json").is_file():
                        return entry.name
        parent = base.parent
        if parent == base:
            break
        base = parent
    # Fallback registro global: repo_path que contenga el start.
    start_str = str(Path(start or os.getcwd()).resolve())
    for pid, info in read_registry().get("projects", {}).items():
        repo = info.get("repo_path", "")
        if repo and start_str.startswith(repo):
            return pid
    return ""


# ── Directorios cognitivos de un proyecto ──────────────────────────


def project_cognitive_dir(pid: str, repo_path: str = "") -> Path:
    """<repo_path>/.cognitive/projects/<pid>/ (o bajo el walker root)."""
    repo = repo_path or resolve_project(pid).get("repo_path", "")
    if repo:
        cog = Path(repo) / ".cognitive" / "projects" / pid
        if cog.is_dir():
            return cog
    return walker_root() / ".cognitive" / "projects" / pid


def project_data_dir(pid: str, repo_path: str = "") -> Path:
    """Data del proyecto: <cognitive_dir>/data/ (donde vive project.json)."""
    return project_cognitive_dir(pid, repo_path) / "data"


# ── Detección de proyecto (port de ProjectDetector, versión autónoma) ──


def detect_project(project_path: str) -> dict:
    """Detecta (language, framework) por archivos marcadores del repo."""
    p = Path(project_path)
    language = "unknown"
    framework = "unknown"

    if (p / "pyproject.toml").is_file() or (p / "requirements.txt").is_file():
        language = "python"
    elif (p / "package.json").is_file():
        language = "javascript"
    elif (p / "Cargo.toml").is_file():
        language = "rust"
    elif (p / "go.mod").is_file():
        language = "go"
    elif (p / "pom.xml").is_file() or (p / "build.gradle").is_file():
        language = "java"

    if language == "javascript" or (p / "package.json").is_file():
        pkg = p / "package.json"
        try:
            pkg_data = json.loads(pkg.read_text())
            deps = {**pkg_data.get("dependencies", {}), **pkg_data.get("devDependencies", {})}
            if "react-native" in deps:
                framework = "react-native"
            elif "next" in deps:
                framework = "next"
            elif "react" in deps:
                framework = "react"
            elif "vue" in deps:
                framework = "vue"
            elif "express" in deps:
                framework = "express"
        except Exception:  # noqa: BLE001
            pass
    elif language == "python":
        if (p / "manage.py").is_file():
            framework = "django"
        elif any(
            (p / f).is_file() for f in ("fastapi_main.py", "main.py")
        ) and _mentions(p / "pyproject.toml", "fastapi"):
            framework = "fastapi"

    return {"language": language, "framework": framework}


def _mentions(file: Path, token: str) -> bool:
    try:
        return token in file.read_text()
    except Exception:  # noqa: BLE001
        return False


_CODE_EXTS = (
    ".py", ".js", ".ts", ".rs", ".go", ".java", ".tsx", ".jsx", ".css", ".json", ".md",
)
_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".cognitive", "data"}


def code_detected_walk(project_path: str) -> bool:
    """¿Hay código fuente en el repo? (walk simple, sin CPG — Fase 2)."""
    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for f in files:
            if f.endswith(_CODE_EXTS):
                return True
    return False
