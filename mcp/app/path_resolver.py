"""
path_resolver.py — Resolucion de paths del Walker v3.

UNICO modulo responsable de traducir identificadores (project_id, chain_dir)
a rutas del sistema de archivos. NADIE debe construir paths con .cognitive/
directamente —TODO pasa por aquí.

Fuentes de config (desde config/config.json):
  - projects_dir       → override de .cognitive/projects/
  - sessions_dir       → override de .cognitive/sessions/
  - data_dir           → nombre del subdirectorio data (default: "data")
  - workflow.manifest  → manifiesto canónico del flujo (config central, ADR-061)

Jerarquía de resolución (convención SSOT de layout):

  LAYER 1 — DENTRO del repo de cada proyecto (se crea con `walker prepare`,
  análogo a `git init`). Contiene TODO el espacio cognitivo del proyecto:
  su manifiesto (project.json), sus memorias (ckg/trace/cpg) y el estado
  del watcher. Resolver = get_project_dir(pid) vía projects.json:
    <repo_path>/                                  ← repo del proyecto
    └── .cognitive/
        └── projects/
            └── {project_id}/                     ← DIRECTORIO COGNITIVO (project_root)
                ├── data/                         ← TODAS las memorias del proyecto
                │   ├── project.json              ← manifiesto (identidad + location)
                │   ├── ckg.json.gz               ← memoria semántica (CKG)
                │   ├── trace.json.gz             ← trazabilidad (TraceGraph)
                │   ├── cpg.json.gz               ← grafo de código (CPG)
                │   ├── change_detected.json      ← estado del watcher
                │   └── .file_snapshot.json       ← baseline del watcher
                ├── delta/                        ← sprints (springs) del proyecto
                │   └── {sprint_id}/
                └── traces/                       ← trace logs por sprint

  LAYER 2 — RAÍZ del walker (el administrador). Contiene EL REGISTRO de
  todos los proyectos que administra (incluido él mismo) y su config base.
  Resolver = get_walker_root() / data/...:
    <walker_root>/data/
    ├── projects.json          ← REGISTRO GLOBAL: project_id → repo_path
    │                            (índice de referencias, NO contiene memorias)
    ├── projects.json.bak      ← backup de escritura atómica
    ├── .walkerignore          ← patrones que walker no escanea
    └── change_detected.json   ← watcher sobre el propio repo del walker

  CLAVE (la distinción): el REGISTRO GLOBAL solo RESUELVE rutas; la
  identidad y las memorias de cada proyecto viven en su propio
  .cognitive/projects/<pid>/ dentro de su repo. Si mañana se prepara un
  proyecto React "pepe", se creará <pepe>/.cognitive/projects/pepe/data/
  con su project.json + memorias, y solo se añadirá la referencia
  pepe → <pepe> en data/projects.json del walker.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# ── Acceso a config sin pasar por config.get() (evita recursion circular) ──


def _read_config_file() -> dict:
    """Lee config en cascada: .cognitive/config.json del proyecto + walker/config.json.

    El orden de precedencia (de mayor a menor):
    1. .cognitive/config.json del proyecto activo (detectado desde CWD)
    2. config/config.json del walker (base)

    Esto permite que cada proyecto tenga sus propios overrides.
    """
    result: dict = {}

    # Base: config del walker
    walker_cfg = get_walker_root() / "config" / "config.json"
    if walker_cfg.is_file():
        try:
            result.update(json.loads(walker_cfg.read_text()))
        except Exception:
            pass

    # Override: .cognitive/config.json del proyecto activo (desde CWD)
    active_cog = find_cognitive_root()
    if active_cog is not None:
        project_cfg = active_cog / "config.json"
        if project_cfg.is_file():
            try:
                result.update(json.loads(project_cfg.read_text()))
            except Exception:
                pass

    return result


def _get_cfg(key: str, default: Any = None) -> Any:
    """Valor de config con override directo del archivo + fallback a config.get.

    Para keys que CAUSAN recursion (projects_dir, sessions_dir) se usa solo
    el override directo del archivo. Para el resto delega en config.get().
    """
    # Leer override directo del archivo (evita recursion con config.get)
    cfg = _read_config_file()
    if key in cfg:
        return cfg[key]

    # Para keys no-path, delegar en config.get() (no causan recursion)
    if key not in ("projects_dir", "sessions_dir", "scripts_dir"):
        from shared.core.engine.infrastructure.config import get as cfg_get

        return cfg_get(key, default)

    return None  # Sin override → quien llama usa su default


# ═══════════════════════════════════════════════════════════════
# 1. Raíz del proyecto walker
# ═══════════════════════════════════════════════════════════════


def get_walker_root() -> Path:
    """Raíz del proyecto walker.

    Camina hacia arriba desde scripts/_walker/shared/path_resolver.py
    hasta encontrar config/config.json.
    """
    this_dir = Path(
        os.path.dirname(os.path.abspath(__file__))
    )  # .../scripts/_walker/shared/
    candidate = this_dir
    for _ in range(8):
        if (candidate / "config" / "config.json").is_file():
            return candidate.resolve()
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent
    return Path.cwd().resolve()


# ═══════════════════════════════════════════════════════════════
# 2. Detección del proyecto activo (CWD → .cognitive/)
# ═══════════════════════════════════════════════════════════════


def find_cognitive_root(start: str | None = None) -> Path | None:
    """Busca el .cognitive/ más cercano caminando hacia arriba desde CWD.

    Como git encuentra .git/, el walker encuentra .cognitive/.

    Args:
        start: Directorio donde empezar la búsqueda (default: os.getcwd()).

    Returns:
        Path al directorio .cognitive/ si se encuentra, None si no.
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


def get_active_cognitive_dir() -> Path:
    """Retorna el .cognitive/ del walker (registro central de proyectos).

    El walker tiene su propio .cognitive/ con:
      - config.json          → configuración del walker
      - projects/{pid}/      → manifests con repo_path al código real
      - sessions/            → sesiones de agentes

    Si CWD tiene un .cognitive/ propio, se usa como override
    (para proyectos autocontenidos con walker prepare).
    """
    walker_cognitive = get_cognitive_dir()
    project_cognitive = find_cognitive_root()

    # Si CWD tiene .cognitive/ propio, usarlo como override
    if project_cognitive is not None and project_cognitive != walker_cognitive:
        return project_cognitive

    return walker_cognitive


# ═══════════════════════════════════════════════════════════════
# 3. Directorios base del sistema cognitivo
# ═══════════════════════════════════════════════════════════════


def get_cognitive_dir() -> Path:
    """Directorio .cognitive/ del walker, desde su raíz detectada por __file__.

    Es el registro central: contiene config.json, projects/{pid}, sessions/.
    """
    return get_walker_root() / ".cognitive"


def get_projects_dir() -> Path:
    """Directorio de proyectos: <cognitive>/projects/ (o override).

    cognitive = .cognitive/ detectado desde CWD o fallback al del walker.
    """
    cfg = _get_cfg("projects_dir")
    if cfg:
        return Path(cfg).expanduser().resolve()
    return get_active_cognitive_dir() / "projects"


def get_sessions_dir() -> Path:
    """Directorio de sesiones: <cognitive>/sessions/ (o override).

    cognitive = .cognitive/ detectado desde CWD o fallback al del walker.
    Si hay WALKER_AGENT_ID, retorna .../agents/{agent_id}/
    """
    cfg = _get_cfg("sessions_dir")
    if cfg:
        base = Path(cfg).expanduser().resolve()
    else:
        base = get_active_cognitive_dir() / "sessions"
    agent = os.environ.get("WALKER_AGENT_ID", "")
    return (base / "agents" / agent) if agent else base


# ═══════════════════════════════════════════════════════════════
# 3. Proyectos: project_id → paths específicos
#
# DISTINCIÓN: data/projects.json (REGISTRO GLOBAL, walker root) es el
# índice de TODOS los proyectos (project_id → repo_path). El MANIFIESTO de
# un proyecto es .cognitive/{pid}/data/project.json (identidad + location),
# dentro del proyecto que se está trabajando. get_project_dir usa el
# registro global para RESOLVER dónde vive el .cognitive/ de cada proyecto.
# ═══════════════════════════════════════════════════════════════


def get_project_dir(project_id: str) -> Path:
    """Directorio cognitivo de un proyecto.

    1. Si el proyecto tiene repo_path en data/projects.json (registro global)
       y existe .cognitive/projects/<pid>/ bajo ese repo, usa esa ruta.
    2. Si no, usa la ruta centralizada: <walker_root>/.cognitive/projects/<pid>/.

    El registro global (projects.json) únicamente RESUELVE rutas; la
    identidad del proyecto se lee del manifiesto (.cognitive/<pid>/data/project.json).
    """
    repo_cog = _project_dir_from_registry(project_id)
    if repo_cog:
        return repo_cog
    return get_projects_dir() / project_id


def _project_dir_from_registry(project_id: str) -> Path | None:
    """Busca el directorio cognitivo del proyecto desde el registro global.

    Lee data/projects.json (registro global del walker) y construye
    <repo_path>/.cognitive/projects/<pid>/.
    """
    import json
    import os

    reg_path = get_walker_root() / "data" / "projects.json"
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
    except Exception:
        pass
    return None


def get_delta_dir(project_id: str) -> Path:
    """Sprints de un proyecto: <project_dir>/<sprint_dir>/.

    sprint_dir se configura en config.json (clave "sprint_dir").
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
    # Si es relativo simple, resolver bajo data/
    if "/" not in sprint_dir_cfg:
        return get_data_dir(project_id) / sprint_dir_cfg
    return get_project_dir(project_id) / sprint_dir_cfg


def get_sprint_dir(project_id: str, sprint_id: str) -> Path:
    """Directorio de un sprint: <project_dir>/<sprint_dir>/<sprint_id>/"""
    return get_delta_dir(project_id) / sprint_id


def get_data_dir(project_id: str) -> Path:
    """Data de un proyecto: <project_dir>/data/"""
    return get_project_dir(project_id) / _get_cfg("data_dir", "data")


def memory_data_dir(chain_dir: str) -> str:
    """Raíz canónica de memorias (data/) para un chain_dir (ADR-WALKER-054 D4).

    SSOT de rutas (INV-W068-06/B4): Única fuente de verdad:
    .cognitive/projects/<pid>/data (resuelto por ProjectService.resolve_paths).
    Si chain_dir ya es la raíz data, se usa tal cual; si es el dir del proyecto
    u otro, se deriva. Fallback defensivo (tests/proyectos sin manifest).
    """
    if not chain_dir:
        return ""
    try:
        from features.project import ProjectService

        paths = ProjectService.resolve_paths(chain_dir)
        if paths.data_dir:
            return str(paths.data_dir)
    except Exception:
        pass
    if os.path.isfile(os.path.join(chain_dir, "project.json")):
        return chain_dir
    data_dir = os.path.join(chain_dir, "data")
    return data_dir if os.path.isdir(data_dir) else chain_dir


@dataclass(frozen=True)
class MemoryContext:
    """Raíz de memorias canónica de un proyecto (primitiva R1, ADR-068 Fase 4)."""

    project_id: str
    repo_root: str
    data_dir: str


def resolve_memory_context(chain_dir: str) -> MemoryContext:
    """Resuelve (project_id, repo_root, data_dir) desde un chain_dir.

    - data_dir: .cognitive/projects/<pid>/data (donde viven las memorias).
    - repo_root: raíz real del código (para métricas/scope).
    - project_id: id lógico del proyecto (manifest) o basename del dir.

    Fallback defensivo (proyectos sin manifest/tests): si <chain_dir>/data
    existe, ese es el data_dir; si no, chain_dir.
    """
    if not chain_dir:
        return MemoryContext(project_id="", repo_root="", data_dir="")

    try:
        root = resolve_project_root(chain_dir)
        pid = resolve_project_id(root) or os.path.basename(root)
        # data_dir SIEMPRE = <root>/data (root ya es el dir del proyecto,
        # .cognitive/projects/<pid>). NO usar get_data_dir/get_repo_path:
        # resuelven por el registry GLOBAL (walker) y rompen descentralizados.
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
            except Exception:
                pass
        return MemoryContext(project_id=pid, repo_root=repo, data_dir=data_dir)
    except Exception:
        candidate = os.path.join(chain_dir, "data")
        return MemoryContext(
            project_id=os.path.basename(os.path.normpath(chain_dir)),
            repo_root=chain_dir,
            data_dir=candidate if os.path.isdir(candidate) else chain_dir,
        )


def resolve_data_dir(chain_dir: str) -> str:
    """Solo el data_dir canónico (atajo común)."""
    return resolve_memory_context(chain_dir).data_dir


def get_data_path(project_id: str, filename: str) -> Path:
    """Archivo en data/: <project_dir>/data/<filename>"""
    return get_data_dir(project_id) / filename


def get_state_path(project_id: str) -> Path:
    """Estado del workflow de un proyecto: <project_dir>/state"""
    return get_project_dir(project_id) / "state"


# ELIMINADO (ADR-WALKER-061, cero legacy): get_workflow_dir() apuntaba a
# <project_dir>/workflow.v3/ — búsqueda legacy por proyecto. El manifiesto
# del flujo es UNO global y canónico: config.get_workflow_manifest_path()
# (key 'workflow.manifest' → data/workflow/workflow.json del motor).

def get_engine_workflow_dir() -> Path:
    """Directorio workflow canónico del motor (SSOT) — CÓDIGO, no data.

    El workflow es maquinaria interna del motor walker, NO config de un
    proyecto: vive versionado en data/workflow/ del repo.
    (.cognitive/projects/<pid>/data/ es solo data de cada proyecto.)
    NO hay fallback: si no existe, los llamadores deben ERROR.

    Resolución robusta: sube desde este archivo hasta la raíz del repo
    (donde vive features/), sin depender del índice posicional (parents[N])
    que cambió al reorganizar libs/ → shared/core/.
    """
    current = Path(__file__).resolve()
    # Sube hasta encontrar el dir que contiene features/ + shared/ (raíz del repo)
    while current != current.parent:
        if (current / "features").is_dir() and (current / "shared").is_dir():
            return current / "data" / "workflow"
        current = current.parent
    # Sin raíz reconocible: error claro (no IndexError por parents[0] en /).
    # ADR-WALKER-061: cero fallback — los llamadores deben ERROR.
    raise RuntimeError(
        "No se encontró la raíz del walker (directorio con features/ + shared/) "
        f"subiendo desde {Path(__file__).resolve()!s}"
    )


def get_engine_workflow_path() -> Path:
    """Ruta al workflow.json canónico del motor."""
    return get_engine_workflow_dir() / "workflow.json"


# ═══════════════════════════════════════════════════════════════
# 4. Archivos de datos específicos (CPG, CKG, etc.)
# ═══════════════════════════════════════════════════════════════


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


# ═══════════════════════════════════════════════════════════════
# 5. Manifiesto del proyecto (project.json)
# ═══════════════════════════════════════════════════════════════


def get_manifest_path(project_id: str) -> Path:
    """Ruta a project.json de un proyecto.

    Busca en: <project_dir>/data/project.json (canónico).
    Si no existe, raise FileNotFoundError.
    """
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
    """Paths absolutos al código fuente desde project.json.

    ADR-WALKER-054 D1: sin code_paths — el scope es el repo completo.
    """
    repo = get_repo_path(project_id)
    if repo and os.path.isdir(repo):
        return [Path(repo).resolve()]
    return [get_project_dir(project_id)]


# ═══════════════════════════════════════════════════════════════
# 6. Resolución desde chain_dir (sprint → project)
# ═══════════════════════════════════════════════════════════════


def resolve_project_root(chain_dir: str) -> str:
    """Deriva el directorio del proyecto desde un chain_dir.

    Camina hacia arriba desde chain_dir buscando project.json.
    El directorio que contiene project.json es el project root.

    Busca en tres ubicaciones en cada nivel:
    1. <candidate>/data/project.json (canónico, todo en data/)
    2. <candidate>/project.json (legacy, project root)
    3. <candidate>/.cognitive/projects/*/data/project.json (layout cognitivo
       nuevo: el manifest del proyecto vive bajo .cognitive/, hijo de la raíz
       del repo). En ese caso el project root cognitivo es
       <candidate>/.cognitive/projects/<pid> (que contiene data/project.json).

    IMPORTANTE: Se prefiere data/project.json sobre el directo, y se EVITA
    que un directorio llamado 'data' sea identificado como project root
    (bug del "double data/": chain_dir dentro de .../data/spring/xxx/).
    Si el candidato se llama 'data', se retorna su padre.

    El check del layout cognitivo (3) permite resolver proyectos self-hosted
    (como el propio walker) cuyo project.json NO está en la raíz del repo
    sino en <repo>/.cognitive/projects/<pid>/data/project.json: caminando
    hacia arriba nunca se encuentra "data/project.json" en la raíz, pero el
    manifest SÍ existe como hijo (descendente) de la raíz vía .cognitive/.

    Raises:
        FileNotFoundError: si no encuentra project.json en ningún nivel.
    """
    if not chain_dir:
        raise FileNotFoundError("chain_dir vacío — no se puede resolver project root")
    cd = str(chain_dir)
    candidate = cd
    for _ in range(10):
        # Buscar project.json en data/ (canónico) — PREFERIDO
        if os.path.isfile(os.path.join(candidate, "data", "project.json")):
            # Si el candidato se llama 'data', el padre es el project root
            if os.path.basename(candidate) == "data":
                parent = os.path.dirname(candidate)
                if parent != candidate:
                    return parent
            return candidate
        # Buscar project.json directo (legacy)
        if os.path.isfile(os.path.join(candidate, "project.json")):
            # Si el candidato se llama 'data', ignorarlo y seguir subiendo
            if os.path.basename(candidate) != "data":
                return candidate
        # Layout cognitivo nuevo: <candidate>/.cognitive/projects/<pid>/data/project.json
        cognitive_manifest = _find_cognitive_manifest(candidate)
        if cognitive_manifest:
            return cognitive_manifest
        parent = os.path.dirname(candidate)
        if parent == candidate:
            break
        candidate = parent
    raise FileNotFoundError(
        f"No se pudo resolver project root desde chain_dir='{chain_dir}'. "
        "Ningún directorio en la jerarquía contiene project.json ni data/project.json."
    )


def _find_cognitive_manifest(candidate: str) -> str:
    """Retorna el dir cognitivo del proyecto si existe un project.json

    bajo <candidate>/.cognitive/projects/<pid>/data/project.json.

    Es el layout del manifest de un proyecto self-hosted/descentralizado:
    <repo>/.cognitive/projects/<pid>/data/project.json. El retorno es el
    <project_root> cognitivo (directorio que contiene data/project.json),
    no la raíz del repo (para <repo>/.cognitive/projects/<pid>/data/project.json
    retorna <repo>/.cognitive/projects/<pid>).

    Solo soporta UN proyecto por repo (primera coincidencia de glob).
    Retorna '' si no hay ningún manifest cognitivo bajo el candidato.
    """
    import glob

    matches = sorted(
        glob.glob(os.path.join(candidate, ".cognitive", "projects", "*", "data", "project.json"))
    )
    if not matches:
        return ""
    # <dir>/data/project.json → <dir>  (quitar /data/project.json)
    return os.path.dirname(os.path.dirname(matches[0]))


def resolve_project_id(chain_dir: str) -> str:
    """Deriva project_id desde chain_dir."""
    root = resolve_project_root(chain_dir)
    return os.path.basename(root) if root else ""


def resolve_real_project_dir(chain_dir: str) -> str:
    """Resuelve el directorio REAL del código fuente desde chain_dir.

    Lee project.json del cognitive_dir para obtener repo_path y construye:
    <repo_path>/projects/<project_id>/

    Busca project.json en data/ primero (canónico), luego directo (legacy).

    Raises:
        FileNotFoundError: si no encuentra project.json o repo_path.
    """
    cognitive_dir = resolve_project_root(chain_dir)
    # Buscar en data/ primero (canónico), luego directo (legacy)
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
    """Resuelve ruta a un archivo de datos desde chain_dir.

    Busca SOLO en: <project_root>/data/<filename>.gz
    Si no existe, ERROR.
    """
    project_root = resolve_project_root(chain_dir)
    data_dir = os.path.join(project_root, _get_cfg("data_dir", "data"))
    data_file = os.path.join(data_dir, filename + ".gz")
    if os.path.exists(data_file):
        return data_file
    # Intentar sin .gz
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
