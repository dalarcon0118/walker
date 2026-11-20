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


def _read_json(path: Path) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:  # noqa: BLE001 - fail-open
        return {}


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
    project_json = Path(path) / ".cognitive" / "projects" / pid / "data" / "project.json"
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
        return {
            "type": "already_exists",
            "status_code": 200,
            "project_id": pid,
            "path": path,
            "registered": not registered,
            "registered_in": registered_in,
            "message": (
                f"Project '{pid}' is already prepared at {path}. "
                "Initialization skipped — existing configuration was NOT overwritten."
                + ("" if registered else " Global registry reconciled.")
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

    # 2. Estructura del layout cognitivo del proyecto.
    data_dir = Path(path) / ".cognitive" / "projects" / pid / "data"
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

    # 6. Detección de código (sin CPG — Fase 2).
    code_detected = code_detected_walk(path)

    return {
        "type": "prepare_result",
        "status_code": 200,
        "project_id": pid,
        "path": path,
        "cognitive_dir": str(Path(path) / ".cognitive"),
        "project_json": str(data_dir / "project.json"),
        "registered_in": str(registered_in),
        "code_detected": code_detected,
        "cpg_built": 0,
        "cpg_pending": "Fase 2 (memorias) construye el CPG",
        "watcher_installed": False,
        "walkerignore_copied": copied,
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
