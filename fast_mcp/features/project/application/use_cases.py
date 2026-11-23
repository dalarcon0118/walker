"""application.use_cases — Los 3 use cases del workflow core (context PROJECT).

Port autónomo (ADR-WALKER-071 §0 #4/#5): prepare / list / status viven en
fast_mcp con el MISMO contrato de datos que el legacy. Cada use case recibe su
contexto cableado (`ProjectCtx`) por parámetro — la superficie nunca construye
dependencias (regla de coherencia §3bis 3).

Los checks de stale (files/CPG/CKG) llegan en Fase 2 junto con el port de
memorias — reportados como pendientes, nunca simulados.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from fast_mcp.features.project.infrastructure.detection import (
    code_detected_walk,
    detect_project,
)
from fast_mcp.features.project.infrastructure.layout import ProjectLayout
from fast_mcp.shared_kernel.registry_store import RegistryStore
from fast_mcp.shared_kernel import paths


@dataclass(frozen=True)
class ProjectCtx:
    """Contexto cableado del context project (composition.py lo construye)."""

    store: RegistryStore
    layout: ProjectLayout


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _walker_data_dir() -> Path:
    """`<walker_root>/data` — fuente del parser/outline-rules custom.

    Función aparte para poder inyectarla en tests sin depender de que el
    repo tenga los binarios descargados.
    """
    return paths.walker_root() / "data"


from fast_mcp.shared_kernel.sgconfig import (  # noqa: E402
    seed_root_sgconfig as seed_root_sgconfig,
    seed_sgconfig as seed_sgconfig,
)


def _read_json(path: Path) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:  # noqa: BLE001 - fail-open
        return {}


def refresh_manifest_identity(data_dir: Path, repo_path: str) -> dict:
    """Refresca `identity.{language,framework}` si la detección cambió.

    La idempotencia de `prepare` existe para no pisar lo curado, pero el
    lenguaje detectado no es curación: `client-chat` quedó registrado como
    `javascript` cuando el 82 % del código es PureScript, porque el detector
    solo miraba `package.json` (medido 2026-11-22). El proyecto ya preparado
    nunca se re-detectaba, así que el manifiesto envejecía en silencio.

    Solo escribe si la detección es concreta (≠ `unknown`) y distinta. Nunca
    toca otros campos del manifiesto.

    Returns:
        {campo: {"from": viejo, "to": nuevo}} — vacío si no hubo cambios.
    """
    manifest_path = data_dir / "project.json"
    manifest = _read_json(manifest_path)
    project = manifest.get("project") or {}
    identity = project.get("identity") or {}
    if not identity:
        return {}

    detected = detect_project(repo_path)
    changed: dict[str, dict] = {}
    for key in ("language", "framework"):
        new = str(detected.get(key) or "")
        old = str(identity.get(key) or "")
        if new and new != "unknown" and new != old:
            identity[key] = new
            changed[key] = {"from": old, "to": new}
    if not changed:
        return {}

    project["identity"] = identity
    meta = project.get("meta") or {}
    meta["updated_at"] = _now()
    project["meta"] = meta
    manifest["project"] = project
    try:
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    except OSError:
        return {}
    return changed


# Nombre del archivo de estado del watcher (SSOT del stale, auto-contenido).
# El FileWatcher (shared_kernel/project_support/watcher.py) persiste su estado
# en data_dir/change_detected.json + data_dir/.file_snapshot.json. El status
# stale se deriva AQUÍ desde los archivos del propio watcher — NO depende del
# CPG ni de su fingerprint. (Fase 2 pendiente en el comentario de este módulo.)
_CHANGE_FILE = "change_detected.json"
_SNAPSHOT_FILE = ".file_snapshot.json"


def _watcher_status(data_dir: Path) -> dict:
    """Lee el estado stale del watcher de forma auto-contenida.

    Devuelve un dict con la info que el FileWatcher ya persistió en sus propios
    archivos (change_detected.json + .file_snapshot.json), sin tocar el CPG:
      - total_changes / files_changed: archivos que difieren entre el snapshot
        anterior y el actual.
      - cpg_status: up_to_date | stale | pending (derivado del diff de
        snapshots si no hay info del CPG).
      - stale: bool, si hay archivos pendientes de re-indexar.

    Si el watcher nunca ha corrido (no hay archivos), reporta pending/unknown —
    nunca inventa un estado simulado (convention: no simular lo pendiente).
    """
    change = _read_json(data_dir / _CHANGE_FILE)
    snapshot = _read_json(data_dir / _SNAPSHOT_FILE)

    if not change:
        return {
            "available": False,
            "files_changed": [],
            "total_changes": 0,
            "new_changes": [],
            "cpg_status": "pending",
            "stale": False,
            "message": (
                "watcher: sin estado aún (no ha corrido scan/update). "
                "Construye el CPG (Fase 2) para generar el snapshot inicial."
            ),
        }

    files_changed = change.get("files_changed", [])
    total = change.get("total_changes", len(files_changed))
    total = int(total)

    # Deriva stale del diff de snapshots del propio watcher (no del CPG).
    # Si hay archivos que difieren entre snapshot anterior y actual → stale.
    cpg_status = change.get("cpg_status")
    if cpg_status not in ("up_to_date", "stale"):
        cpg_status = "stale" if total > 0 else "up_to_date"

    return {
        "available": True,
        "timestamp": change.get("timestamp", ""),
        "files_changed": files_changed,
        "total_changes": total,
        "new_changes": change.get("new_changes", []),
        "cpg_status": cpg_status,
        "stale": total > 0,
        "snapshot_files": len(snapshot),
    }


# ── Caso de uso 1: prepare ─────────────────────────────────────────


def prepare_use_case(
    ctx: ProjectCtx, project_path: str = "", args: dict | None = None
) -> dict:
    """Inicializa un proyecto: layout cognitivo + project.json + registro global."""
    args = dict(args or {})
    path = os.path.abspath(project_path or args.get("path") or os.getcwd())
    pid = os.path.basename(path)
    name = args.get("name", pid)

    # 0. Idempotencia (mismo guard que el legacy).
    # El layout sale del SSOT del kernel (`paths.compose_project_layout`), no
    # de un join a mano: con `project.cognitive_dir`/`project.data_dir`
    # configurables, hardcodear `.cognitive/.../data` creaba un segundo layout
    # que `resolve_paths` después no encontraba.
    data_dir = paths.compose_project_layout(path, pid)
    project_json = data_dir / "project.json"
    if project_json.exists():
        # El guard de idempotencia NO debe dejar proyectos huérfanos: si el
        # layout existe pero el registro global no (prepare interrumpido, o
        # memoria copiada de otra máquina), `list`/`status`/`memory_schema`
        # no lo encuentran y el proyecto queda inaccesible por API. Se
        # reconcilia SOLO el registro — el project.json curado no se toca.
        registered = pid in (ctx.store.read().get("projects") or {})
        registered_in = ""
        if not registered:
            registered_in = str(ctx.store.register(pid, name, path))
        # Un proyecto creado antes de existir el soporte de lenguajes custom
        # nunca recibía `sgconfig.yml`: el early return ocurría antes del paso
        # 5b. Se siembra acá también — solo si falta, nunca pisa lo curado.
        sgconfig_seeded = seed_sgconfig(data_dir)
        root_sgconfig_seeded = seed_root_sgconfig(path)
        # La detección de lenguaje no es curación: si el manifiesto dice
        # `javascript` y el repo es Spago, se corrige (ver el helper).
        identity_refreshed = refresh_manifest_identity(data_dir, path)
        return {
            "type": "already_exists",
            "status_code": 200,
            "project_id": pid,
            "path": path,
            "registered": not registered,
            "registered_in": registered_in,
            "sgconfig_seeded": sgconfig_seeded,
            "root_sgconfig_seeded": root_sgconfig_seeded,
            "identity_refreshed": identity_refreshed,
            "message": (
                f"Project '{pid}' is already prepared at {path}. "
                "Initialization skipped — existing configuration was NOT overwritten."
                + ("" if registered else " Global registry reconciled.")
                + (" Custom-language sgconfig.yml seeded." if sgconfig_seeded else "")
                + (
                    " Identity refreshed: "
                    + ", ".join(
                        f"{k} {v['from'] or '∅'}→{v['to']}"
                        for k, v in identity_refreshed.items()
                    )
                    if identity_refreshed else ""
                )
            ),
            "next_steps": [f"walker {pid}/status"],
        }

    # 1. Detección de propiedades.
    detections = detect_project(path)
    language = args.get("language") or detections["language"]
    framework = args.get("framework") or detections["framework"]
    # Tooling desde evidencia (P1.4): el proyecto nace con su constitución
    # mínima en vez de un `coding` vacío que obliga a preguntarlo después.
    from fast_mcp.shared_kernel.project_tooling import detect_tooling

    tooling = detect_tooling(path, language)

    setup_warnings: list[str] = []
    if not args.get("language") and language == "unknown":
        setup_warnings.append("language not detected (Fase 2: update_project lo fija)")
    if not args.get("framework") and framework == "unknown":
        setup_warnings.append("framework not detected (Fase 2: update_project lo fija)")

    # 2. Estructura del layout cognitivo del proyecto (ya resuelto arriba).
    data_dir.mkdir(parents=True, exist_ok=True)

    # 3. project.json (mismo schema que el legacy).
    now = _now()
    manifest = {
        "project": {
            "meta": {
                "project_id": pid,
                "version": "1.0",
                "created_at": now,
                "updated_at": now,
            },
            "identity": {"name": name, "language": language, "framework": framework},
            "location": {"repo_path": path},
            "design": {"patterns": [], "layer_rules": {}, "forbidden_dependencies": []},
            "architecture": {"module_organization": ""},
            "coding": {
                "naming_convention": "",
                "formatter": tooling.get("formatter", ""),
                "linter": tooling.get("linter", ""),
                "test_framework": tooling.get("test_framework", ""),
            },
            "quality_preferences": {},
            "team": {"owners": []},
            "dependencies": {"internal_modules": [], "external_services": []},
        }
    }
    with open(data_dir / "project.json", "w") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    # 4. Registro global (SSOT de rutas, R-05).
    registered_in = ctx.store.register(pid, name, path)

    # 5. .walkerignore template (best-effort).
    template = paths.walkerignore_template()
    dest = Path(path) / ".walkerignore"
    copied = False
    if template.exists() and not dest.exists():
        try:
            dest.write_text(template.read_text())
            copied = True
        except OSError:
            pass

    # 5b. sgconfig.yml para ast-grep (lenguajes custom como PureScript).
    # Misma siembra que el path de idempotencia: una sola implementación.
    sgconfig_copied = seed_sgconfig(data_dir)
    root_sgconfig_copied = seed_root_sgconfig(path)

    # 6. Detección de código (sin CPG — Fase 2).
    code_detected = code_detected_walk(path)

    return {
        "type": "prepare_result",
        "status_code": 200,
        "project_id": pid,
        "path": path,
        "cognitive_dir": str(Path(path) / paths.project_cognitive_dir()),
        "project_json": str(data_dir / "project.json"),
        "registered_in": str(registered_in),
        "code_detected": code_detected,
        "cpg_built": 0,
        "cpg_pending": "Fase 2 (memorias) construye el CPG",
        "watcher_installed": False,
        "walkerignore_copied": copied,
        "sgconfig_copied": sgconfig_copied,
        "root_sgconfig_copied": root_sgconfig_copied,
        "detected": detections,
        "setup_warnings": setup_warnings,
        "next_steps": [f"walker {pid}/status"],
    }


# ── Caso de uso 2: list ────────────────────────────────────────────


def list_use_case(ctx: ProjectCtx, args: dict | None = None) -> dict:
    """Lista los proyectos del registro global."""
    projects = []
    for pid, info in ctx.store.read().get("projects", {}).items():
        projects.append(
            {
                "project_id": pid,
                "name": info.get("name", pid),
                "repo_path": info.get("repo_path", ""),
                "active": bool(info.get("active", False)),
                "registered_at": info.get("registered_at", ""),
            }
        )
    return {
        "type": "projects_list",
        "status_code": 200,
        "message": f"{len(projects)} registered project(s)",
        "data": {"projects": projects, "registry": str(paths.registry_path())},
    }


# ── Caso de uso 3: status ──────────────────────────────────────────


def status_use_case(ctx: ProjectCtx, args: dict | None = None) -> dict:
    """Health + workflow status del proyecto (port autónomo del contrato)."""
    args = dict(args or {})
    pid = (
        args.get("_project_id")
        or args.get("project_id")
        or ctx.store.resolve_from_cwd()
    )
    if not pid:
        projects = list_use_case(ctx)
        return {
            "type": "projects_list",
            "status_code": 200,
            "message": "No project detected at current directory",
            "data": projects.get("data", {}),
            "prompt": "Register a project → walker prepare <path>",
        }

    entry = ctx.store.resolve(pid)
    repo_path = entry.get("repo_path", "")
    data_dir = ctx.layout.data_dir(pid, repo_path)
    manifest = _read_json(data_dir / "project.json").get("project", {})
    identity = manifest.get("identity", {})

    health: dict = {
        "type": "project_health",
        "status_code": 200,
        "project_id": pid,
        "warnings": [],
        "checks": {},
        "project": {
            "name": identity.get("name", pid),
            "language": identity.get("language", ""),
            "framework": identity.get("framework", ""),
            "repo_path": identity.get("location", {}).get("repo_path", repo_path),
        },
    }

    if manifest:
        health["checks"]["project_json"] = "ok"
    else:
        health["warnings"].append(
            f"project.json: not found in {data_dir} — ¿proyecto preparado?"
        )

    # Estado stale del código — entregado desde el WATCHER de forma
    # auto-contenida (change_detected.json + .file_snapshot.json), sin depender
    # del CPG ni de su fingerprint (Fase 2 pendiente en el header del módulo).
    health["watcher"] = _watcher_status(data_dir)
    health["checks"]["watcher"] = (
        "ok" if health["watcher"]["available"] else "pending"
    )

    # Sprint activo (best-effort: el DeltaGraph completo llega en Fase 2/3).
    sprint_state = ctx.layout.find_sprint_state(data_dir)
    if sprint_state:
        health["sprint"] = {
            "sprint_id": sprint_state.get("sprint_id", ""),
            "node": sprint_state.get("current_node", sprint_state.get("node", "?")),
            "sub_state": sprint_state.get("current_sub_state", "?"),
            "history_len": len(sprint_state.get("history", []) or []),
        }
    else:
        health["sprint"] = None
        health["message"] = f"Project '{pid}' — no active sprint"

    health["next_steps"] = [
        f"walker {pid}/prepare" if not manifest else f"walker {pid}/status",
    ]
    return health
