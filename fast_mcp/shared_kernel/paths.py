"""shared_kernel.paths — SSOT de rutas del walker (port de app/path_resolver).

Port fiel al paquete autónomo (ADR-071 §0 #4): mismas funciones y semántica
que app/path_resolver del legacy. Única adaptación: la delegación a
`shared.core.engine.infrastructure.config.get` se reemplaza por lectura local
de config/config.json (mismo archivo; el engine config completo se porta en
Fase 3).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# ── Acceso a config (local; el engine config completo llega en Fase 3) ──


def _read_config_file() -> dict:
    """Lee config en cascada: .cognitive/config.json del proyecto + walker config."""
    result: dict = {}
    walker_cfg = walker_root() / "config" / "config.json"
    if walker_cfg.is_file():
        try:
            result.update(json.loads(walker_cfg.read_text()))
        except Exception:  # noqa: BLE001
            pass
    active_cog = find_cognitive_root()
    if active_cog is not None:
        project_cfg = active_cog / "config.json"
        if project_cfg.is_file():
            try:
                result.update(json.loads(project_cfg.read_text()))
            except Exception:  # noqa: BLE001
                pass
    return result


def _get_cfg(key: str, default: Any = None) -> Any:
    """Valor de config: override del archivo; para keys sin-path, config local."""
    cfg = _read_config_file()
    if key in cfg:
        return cfg[key]
    if key not in ("projects_dir", "sessions_dir", "scripts_dir"):
        return cfg.get(key, default)
    return None


# ── 1. Raíz del proyecto walker ────────────────────────────────────


def walker_root() -> Path:
    """Raíz del proyecto walker: camina hacia arriba hasta config/config.json."""
    this_dir = Path(os.path.dirname(os.path.abspath(__file__)))
    candidate = this_dir
    for _ in range(8):
        if (candidate / "config" / "config.json").is_file():
            return candidate.resolve()
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent
    return Path.cwd().resolve()


def registry_path(root: Path | None = None) -> Path:
    """Registro global de proyectos: <walker_root>/data/projects.json."""
    base = root if root is not None else walker_root()
    return base / "data" / "projects.json"


def walkerignore_template(root: Path | None = None) -> Path:
    """Template .walkerignore que prepare copia a cada proyecto."""
    base = root if root is not None else walker_root()
    return base / "data" / ".walkerignore"


# ── 2. Detección del proyecto activo (CWD → .cognitive/) ───────────


def find_cognitive_root(start: str | None = None) -> Path | None:
    """Busca el directorio cognitivo más cercano caminando hacia arriba desde CWD.

    Como git encuentra .git/, el walker encuentra .cognitive/.
    """
    candidate = Path(start or os.getcwd()).resolve()
    for _ in range(10):
        cognitive = candidate / ".cognitive"
        if cognitive.is_dir():
            return cognitive
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent
    return None


def get_cognitive_dir() -> Path:
    """Directorio cognitivo del walker, desde su raíz detectada por __file__."""
    return walker_root() / ".cognitive"


def get_active_cognitive_dir() -> Path:
    """El directorio cognitivo activo (override por CWD para self-hosted)."""
    walker_cognitive = get_cognitive_dir()
    project_cognitive = find_cognitive_root()
    if project_cognitive is not None and project_cognitive != walker_cognitive:
        return project_cognitive
    return walker_cognitive


# ── 3. Directorios base del sistema cognitivo ──────────────────────


def get_projects_dir() -> Path:
    """Directorio de proyectos: <cognitive>/projects/ (o override)."""
    cfg = _get_cfg("projects_dir")
    if cfg:
        return Path(cfg).expanduser().resolve()
    return get_active_cognitive_dir() / "projects"


def get_sessions_dir() -> Path:
    """Directorio de sesiones: <cognitive>/sessions/ (o override; agent id)."""
    cfg = _get_cfg("sessions_dir")
    if cfg:
        base = Path(cfg).expanduser().resolve()
    else:
        base = get_active_cognitive_dir() / "sessions"
    agent = os.environ.get("WALKER_AGENT_ID", "")
    return (base / "agents" / agent) if agent else base


# ── 4. Proyectos: project_id → paths específicos ───────────────────
#
# data/projects.json (REGISTRO GLOBAL) resuelve rutas; la identidad vive en
# el manifiesto project.json dentro del layout cognitivo del proyecto.


def get_project_dir(project_id: str) -> Path:
    """Directorio cognitivo de un proyecto (registro global → fallback central)."""
    repo_cog = _project_dir_from_registry(project_id)
    if repo_cog:
        return repo_cog
    return get_projects_dir() / project_id


def _project_dir_from_registry(project_id: str) -> Path | None:
    """Busca el dir cognitivo del proyecto desde el registro global."""
    reg_path = walker_root() / "data" / "projects.json"
    if not reg_path.exists():
        return None
    try:
        with open(reg_path) as f:
            reg = json.load(f).get("projects", {}).get(project_id, {})
        repo = reg.get("repo_path", "")
        if not repo:
            return None
        cog = os.path.join(repo, ".cognitive", "projects", project_id)
        if os.path.isdir(cog):
            return Path(cog)
    except Exception:  # noqa: BLE001
        pass
    return None


def get_data_dir(project_id: str) -> Path:
    """Datos de un proyecto: <project_dir>/data/ (o override data_dir)."""
    return get_project_dir(project_id) / _get_cfg("data_dir", "data")


def get_data_path(project_id: str, filename: str) -> Path:
    """Archivo en data/: <project_dir>/data/<filename>"""
    return get_data_dir(project_id) / filename


def get_delta_dir(project_id: str) -> Path:
    """Sprints de un proyecto: <project_dir>/<sprint_dir>/ (config sprint_dir).

    - "spring" → <project_dir>/data/spring/
    - "data/spring" → <project_dir>/data/spring/
    - "delta" → <project_dir>/delta/
    """
    sprint_dir_cfg = _get_cfg("sprint_dir")
    if sprint_dir_cfg is None:
        raise FileNotFoundError(
            f"sprint_dir no configurado para proyecto '{project_id}'. "
            "Define 'sprint_dir' en config.json"
        )
    if "/" not in sprint_dir_cfg:
        return get_data_dir(project_id) / sprint_dir_cfg
    return get_project_dir(project_id) / sprint_dir_cfg


def get_sprint_dir(project_id: str, sprint_id: str) -> Path:
    """Directorio de un sprint: <project_dir>/<sprint_dir>/<sprint_id>/"""
    return get_delta_dir(project_id) / sprint_id


def get_state_path(project_id: str) -> Path:
    """Estado del workflow de un proyecto: <project_dir>/state"""
    return get_project_dir(project_id) / "state"


def get_engine_workflow_dir() -> Path:
    """Directorio workflow canónico del motor (SSOT) — CÓDIGO, no data.

    Sube desde este archivo hasta la raíz del repo (dir con features/).
    """
    current = Path(__file__).resolve()
    while current != current.parent:
        if (current / "features").is_dir():
            return current / "data" / "workflow"
        current = current.parent
    raise RuntimeError(
        "No se encontró la raíz del walker (directorio con features/) "
        f"subiendo desde {Path(__file__).resolve()!s}"
    )


def get_engine_workflow_path() -> Path:
    """Ruta al workflow.json canónico del motor."""
    return get_engine_workflow_dir() / "workflow.json"


# ── 5. Archivos de datos específicos (CPG, CKG, trace) ─────────────


def get_cpg_path(project_id: str) -> Path:
    """Ruta a cpg.json.gz dentro de data/ del proyecto."""
    return get_data_path(project_id, "cpg.json.gz")


def get_ckg_path(project_id: str) -> Path:
    """Ruta a ckg.json.gz dentro de data/ del proyecto."""
    return get_data_path(project_id, "ckg.json.gz")


def get_trace_dir(project_id: str) -> Path:
    """Directorio de traces: <project_dir>/data/traces/"""
    return get_data_dir(project_id) / "traces"


def get_trace_path(project_id: str, sprint_id: str) -> Path:
    """Ruta a un trace: <project_dir>/data/traces/<sprint_id>.json.gz"""
    return get_trace_dir(project_id) / f"{sprint_id}.json.gz"


# ── 6. Manifiesto del proyecto (project.json) ──────────────────────


def get_manifest_path(project_id: str) -> Path:
    """Ruta a project.json de un proyecto (canónico: <project_dir>/data/)."""
    path = get_data_dir(project_id) / "project.json"
    if not path.exists():
        raise FileNotFoundError(
            f"project.json no encontrado en '{path}'. "
            "El manifiesto del proyecto debe estar en <project_dir>/data/project.json"
        )
    return path


def read_project_manifest(project_id: str) -> dict:
    """Lee y retorna project.json. Dict vacío si no existe."""
    path = get_manifest_path(project_id)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def get_repo_path(project_id: str) -> str:
    """Obtiene repo_path desde project.json."""
    manifest = read_project_manifest(project_id)
    loc = manifest.get("project", manifest).get("location", {})
    return loc.get("repo_path", "")


def resolve_source_code_dirs(project_id: str) -> list[Path]:
    """Paths absolutos al código fuente desde project.json (scope = repo)."""
    repo = get_repo_path(project_id)
    if repo and os.path.isdir(repo):
        return [Path(repo).resolve()]
    return [get_project_dir(project_id)]


# ── 7. Resolución desde chain_dir (sprint → project) ───────────────


def resolve_project_root(chain_dir: str) -> str:
    """Deriva el directorio del proyecto desde un chain_dir (busca project.json).

    Returns the REPO ROOT (where code lives), not the layout directory.
    Reads project.json to extract repo_path when available.
    """
    if not chain_dir:
        raise FileNotFoundError("chain_dir vacío — no se puede resolver project root")
    candidate = str(chain_dir)
    for _ in range(10):
        pj = os.path.join(candidate, "data", "project.json")
        if os.path.isfile(pj):
            if os.path.basename(candidate) == "data":
                candidate = os.path.dirname(candidate)
                pj = os.path.join(candidate, "data", "project.json")
            # Read repo_path from manifest (the actual code root)
            repo = _read_repo_path(pj)
            if repo:
                return repo
            return candidate
        pj2 = os.path.join(candidate, "project.json")
        if os.path.isfile(pj2):
            if os.path.basename(candidate) != "data":
                repo = _read_repo_path(pj2)
                if repo:
                    return repo
                return candidate
        cognitive_manifest = _find_cognitive_manifest(candidate)
        if cognitive_manifest:
            pj3 = os.path.join(cognitive_manifest, "data", "project.json")
            repo = _read_repo_path(pj3) if os.path.isfile(pj3) else ""
            if repo:
                return repo
            return cognitive_manifest
        parent = os.path.dirname(candidate)
        if parent == candidate:
            break
        candidate = parent
    raise FileNotFoundError(
        f"No se pudo resolver project root desde chain_dir='{chain_dir}'. "
        "Ningún directorio en la jerarquía contiene project.json ni data/project.json."
    )


def _read_repo_path(manifest_path: str) -> str:
    """Extract repo_path from project.json manifest. Returns '' if unavailable."""
    try:
        with open(manifest_path) as f:
            data = json.load(f)
        repo = data.get("project", {}).get("location", {}).get("repo_path", "")
        if repo and os.path.isdir(repo):
            return repo
    except Exception:  # noqa: BLE001
        pass
    return ""


def _find_cognitive_manifest(candidate: str) -> str:
    """Dir cognitivo del proyecto si hay project.json bajo
    <candidate>/.cognitive/projects/<pid>/data/project.json ('' si no)."""
    import glob

    matches = sorted(
        glob.glob(
            os.path.join(candidate, ".cognitive", "projects", "*", "data", "project.json")
        )
    )
    if not matches:
        return ""
    return os.path.dirname(os.path.dirname(matches[0]))


def resolve_project_id(chain_dir: str) -> str:
    """Deriva project_id desde chain_dir."""
    root = resolve_project_root(chain_dir)
    return os.path.basename(root) if root else ""


@dataclass(frozen=True)
class MemoryContext:
    """Raíz de memorias canónica de un proyecto (primitiva R1, ADR-068 Fase 4)."""

    project_id: str
    repo_root: str
    data_dir: str


def resolve_memory_context(chain_dir: str) -> MemoryContext:
    """Resuelve (project_id, repo_root, data_dir) desde un chain_dir.

    data_dir SIEMPRE = <project_root>/data (no via registry global — los
    proyectos descentralizados se romperían). repo_path del manifest gana si
    existe y es un dir válido.
    """
    if not chain_dir:
        return MemoryContext(project_id="", repo_root="", data_dir="")
    try:
        root = resolve_project_root(chain_dir)
        pid = resolve_project_id(root) or os.path.basename(root)
        data_dir = os.path.join(root, "data")
        repo = root
        manifest_path = os.path.join(data_dir, "project.json")
        if os.path.isfile(manifest_path):
            try:
                with open(manifest_path) as _f:
                    _proj = json.load(_f).get("project", {})
                rp = (_proj.get("location") or {}).get("repo_path", "")
                if rp and os.path.isdir(rp):
                    repo = rp
            except Exception:  # noqa: BLE001
                pass
        return MemoryContext(project_id=pid, repo_root=repo, data_dir=data_dir)
    except Exception:  # noqa: BLE001
        candidate = os.path.join(chain_dir, "data")
        return MemoryContext(
            project_id=os.path.basename(os.path.normpath(chain_dir)),
            repo_root=chain_dir,
            data_dir=candidate if os.path.isdir(candidate) else chain_dir,
        )


def resolve_data_dir(chain_dir: str) -> str:
    """Solo el data_dir canónico (atajo común)."""
    return resolve_memory_context(chain_dir).data_dir


def resolve_real_project_dir(chain_dir: str) -> str:
    """Resuelve el directorio REAL del código fuente desde chain_dir."""
    cognitive_dir = resolve_project_root(chain_dir)
    manifest_path = os.path.join(cognitive_dir, "data", "project.json")
    if not os.path.exists(manifest_path):
        manifest_path = os.path.join(cognitive_dir, "project.json")
        if not os.path.exists(manifest_path):
            raise FileNotFoundError(
                f"project.json no encontrado en '{cognitive_dir}/data/' ni en '{cognitive_dir}'"
            )
    try:
        with open(manifest_path) as f:
            data = json.load(f)
        loc = data.get("project", data).get("location", {})
        repo_path = loc.get("repo_path", "")
        project_id = data.get("project", data).get("meta", {}).get("project_id", "")
        if not repo_path or not project_id:
            raise FileNotFoundError(
                f"project.json en '{manifest_path}' no contiene 'location.repo_path' o 'meta.project_id'"
            )
        return os.path.join(repo_path, "projects", project_id)
    except (json.JSONDecodeError, KeyError, OSError) as e:
        raise FileNotFoundError(f"Error leyendo project.json en '{manifest_path}': {e}")


def resolve_data_path(chain_dir: str, filename: str) -> str:
    """Resuelve ruta a un archivo de datos desde chain_dir (<root>/data/<f>[.gz])."""
    project_root = resolve_project_root(chain_dir)
    data_dir = os.path.join(project_root, _get_cfg("data_dir", "data"))
    data_file = os.path.join(data_dir, filename + ".gz")
    if os.path.exists(data_file):
        return data_file
    data_file_plain = os.path.join(data_dir, filename)
    if os.path.exists(data_file_plain):
        return data_file_plain
    raise FileNotFoundError(
        f"Archivo de datos '{filename}' no encontrado en '{data_dir}'. "
        "Debe estar en <project_root>/data/<filename>[.gz]."
    )


def resolve_trace_path(chain_dir: str) -> str:
    """Resuelve ruta al trace del sprint desde chain_dir."""
    if not chain_dir:
        raise FileNotFoundError("chain_dir vacío — no se puede resolver trace path")
    sprint_id = os.path.basename(chain_dir)
    project_root = resolve_project_root(chain_dir)
    return os.path.join(
        project_root, _get_cfg("data_dir", "data"), "traces", f"{sprint_id}.json.gz"
    )
