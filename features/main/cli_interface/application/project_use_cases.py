"""
application/project_use_cases.py — Cross-feature project orchestration.

Use cases that coordinate MULTIPLE features (code_analysis, cognitive_knowledge,
engine) for project-level operations like prepare and health checks.

These belong in cli_interface because they are cross-feature orchestrators,
NOT single-responsibility project domain logic.

SRP: Each use case orchestrates other features' services.
Moved from features/project/application/use_cases.py (SRP violation fix).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from returns.pipeline import is_successful
from returns.result import safe

logger = logging.getLogger(__name__)


class PrepareProjectUseCase:
    """Initialize a project: detect properties, create .cognitive/, register, scan CPG, setup watcher.

    SRP: Single use case = prepare a project for first use.
    Cross-feature: coordinates ProjectDetector, CPGBuilder, CheckCkgHealthUseCase, FileWatcher.
    """

    @staticmethod
    def execute(project_path: str, args_flags: dict) -> dict:
        from features.project import ProjectDetector
        from features.project import FileWatcher
        from shared.core.path_resolver import get_walker_root as _get_walker_root

        pid = os.path.basename(os.path.abspath(project_path))
        name = args_flags.get("name", pid)
        walker_root = str(_get_walker_root())

        # 0. Idempotency guard
        existing_project_json = os.path.join(
            project_path, ".cognitive", "projects", pid, "data", "project.json"
        )
        if os.path.exists(existing_project_json):
            return {
                "type": "already_exists",
                "project_id": pid,
                "path": project_path,
                "project_json": existing_project_json,
                "message": (
                    f"Project '{pid}' is already prepared at {project_path}. "
                    "Initialization skipped — existing configuration was NOT "
                    "overwritten."
                ),
                "next_steps": [f"walker {pid}/status"],
                "_warnings": [
                    "prepare is idempotent: existing project.json was left untouched."
                ],
            }

        # 1. Detect project properties
        detections = ProjectDetector.detect_all(project_path)
        final_language = args_flags.get("language", detections["language"])
        final_framework = args_flags.get("framework", detections["framework"])

        # 2. Setup warnings
        setup_warnings: list[str] = []
        if not args_flags.get("language") and detections["language"] == "unknown":
            setup_warnings.append(
                f"language not detected. Set with: walker {pid}/task/update_project"
            )
        if not args_flags.get("framework") and detections["framework"] == "unknown":
            setup_warnings.append(
                f"framework not detected. Set with: walker {pid}/task/update_project"
            )
        if not detections["code_paths"]:
            setup_warnings.append(
                f"code_paths not detected. Set with: walker {pid}/task/update_project"
            )
        else:
            setup_warnings.append(
                f"detected code paths: {detections['code_paths']}. "
                f"Verify with: walker {pid}/task/update_project"
            )

        # 3. Create .cognitive directory structure
        cognitive_dir = os.path.join(project_path, ".cognitive")
        projects_data_dir = os.path.join(cognitive_dir, "projects", pid, "data")
        os.makedirs(projects_data_dir, exist_ok=True)

        # 4. Write project.json
        _write_project_json(
            projects_data_dir,
            pid,
            name,
            final_language,
            final_framework,
            project_path,
            detections["code_paths"],
        )

        # 5. Register in global registry
        global_registry_path = os.path.join(walker_root, "data", "projects.json")
        _register_in_global_registry(global_registry_path, pid, name, project_path)

        # 6. Copy .walkerignore template
        _copy_walkerignore(walker_root, project_path)

        # 7. Auto-scan CPG
        code_detected, cpg_nodes = _auto_scan_cpg(project_path)

        # 8. Check CKG status
        from features.cognitive_knowledge.application.use_cases import (
            CheckCkgHealthUseCase,
        )

        ckg_health_result = CheckCkgHealthUseCase.execute(projects_data_dir)
        ckg_health = (
            ckg_health_result.value_or({}) if is_successful(ckg_health_result) else {}
        )
        if (
            ckg_health.get("node_count", 0) == 0
            and ckg_health.get("status") != "not scanned"
        ):
            setup_warnings.append("CKG is empty. Agent should capture knowledge")
        elif ckg_health.get("status") == "empty":
            setup_warnings.append("CKG not initialized. Agent should capture knowledge")

        # 9. Init watcher baseline
        # data_dir canónico = <repo>/.cognitive/projects/<pid>/data (fuera del
        # árbol escaneado). Si el baseline queda en <repo>/data, el status
        # (que lee el change_detected canónico) lo pierde y reporta todo el
        # repo como cambiado.
        watcher = FileWatcher(
            project_path,
            code_paths=detections["code_paths"] or None,
            data_dir=projects_data_dir,
        )
        watcher.scan()
        _reset_watcher_baseline(watcher)

        return {
            "type": "prepare_result",
            "project_id": pid,
            "path": project_path,
            "cognitive_dir": cognitive_dir,
            "project_json": os.path.join(projects_data_dir, "project.json"),
            "registered_in": global_registry_path,
            "code_detected": code_detected,
            "cpg_built": cpg_nodes,
            "watcher_installed": True,
            "files_indexed": watcher.get_status().get("total_changes", 0),
            "setup_warnings": setup_warnings,
            "detected": detections,
            "next_steps": [
                f"walker {pid}/task/update_project",
                f"walker {pid}/status",
            ],
            "_warnings": setup_warnings,
        }


class CheckHealthUseCase:
    """Check project health: project.json, watcher, CPG, CKG, sprint, next steps.

    SRP: Single use case = produce a complete health report for a project.
    Cross-feature: coordinates code_analysis, cognitive_knowledge, engine, watcher.
    """

    @staticmethod
    def execute(project_id: str, repo_path: str, sprint_id: str = "") -> dict:
        from features.project import HealthReportAnalyzer
        from features.code_analysis import CheckCpgHealthUseCase
        from features.cognitive_knowledge.application.use_cases import (
            CheckCkgHealthUseCase,
        )
        from core.engine.application.use_cases import CheckSprintStatusUseCase

        health: dict = {
            "type": "project_health",
            "project_id": project_id,
            "warnings": [],
            "checks": {},
        }

        projects_data_dir = os.path.join(
            repo_path, ".cognitive", "projects", project_id, "data"
        )
        proj_json = os.path.join(projects_data_dir, "project.json")

        # 1. Check project.json
        _check_project_json(proj_json, project_id, repo_path, health)

        # 2. Watcher: detect changes (read-only)
        watcher_outcome = _scan_watcher_core(project_id, repo_path)
        if is_successful(watcher_outcome):
            changes = watcher_outcome.value_or({})
            new_changes = changes.get("new_changes", [])
            total = changes.get("total_changes", 0)
            cpg_status = changes.get("cpg_status", "up_to_date")

            health["watcher"] = {
                "status": "clean" if total == 0 else f"{total} changed",
                "total_changes": total,
                "files_changed": changes.get("files_changed", []),
                "new_changes": new_changes,
                "cpg_status": cpg_status,
                "ckg_status": changes.get("ckg_status", "up_to_date"),
                "timestamp": changes.get("timestamp", ""),
            }

            if total > 0:
                health["checks"]["files"] = f"{total} changed since last CPG build"
                if cpg_status == "stale":
                    health["warnings"].append(
                        f"CPG is stale: {total} file(s) changed. "
                        "Run update_cpg to rebuild the code index."
                    )
            else:
                health["checks"]["files"] = "up to date"
        else:
            error = watcher_outcome.failure()
            health["warnings"].append(f"watcher: {error}")

        # 3. Check CPG
        cpg_health = CheckCpgHealthUseCase.execute(projects_data_dir)
        health["checks"]["cpg"] = cpg_health["status"]
        if cpg_health["status"] == "not scanned":
            health["warnings"].append(
                "CPG not scanned. Run walker prepare or update_cpg"
            )

        # 4. Check CKG
        ckg_health_result = CheckCkgHealthUseCase.execute(projects_data_dir)
        ckg_health = (
            ckg_health_result.value_or({}) if is_successful(ckg_health_result) else {}
        )
        health["checks"]["ckg"] = ckg_health.get("status", "unknown")
        if (
            ckg_health.get("node_count", 0) == 0
            and ckg_health.get("status") != "not scanned"
        ):
            health["warnings"].append("CKG is empty. Agent should capture knowledge")
        elif ckg_health.get("status") == "empty":
            health["warnings"].append(
                "CKG not initialized. Agent should capture knowledge"
            )

        # 5. Check Sprint
        chain_dir = ""
        if sprint_id:
            from shared.core.path_resolver import get_sprint_dir

            chain_dir = str(get_sprint_dir(project_id, sprint_id))
        else:
            from features.project import ProjectService

            chain_dir = ProjectService.find_active_sprint(project_id) or ""

        sprint_health = CheckSprintStatusUseCase.execute(chain_dir, project_id)
        if sprint_health["active"]:
            health["sprint"] = {
                "node": sprint_health.get("node", "?"),
                "sub_state": sprint_health.get("sub_state", "?"),
                "history": sprint_health.get("history", []),
                "gates": sprint_health.get("gates", []),
                "prompt": sprint_health.get("prompt", ""),
                "prompt_id": sprint_health.get("prompt_id", ""),
                "required_fields": sprint_health.get("required_fields", []),
                "execution_type": sprint_health.get("execution_type", ""),
                "graph_progress": sprint_health.get("graph_progress", {}),
                "informal": sprint_health.get("informal", False),
                "sprint_id": sprint_health.get("sprint_id", ""),
            }
        else:
            health["sprint"] = None
            if sprint_health.get("error"):
                health["warnings"].append(f"sprint: {sprint_health['error']}")

        # 6. Proactive next steps
        task_catalog = []
        catalog_outcome = _discover_task_catalog_core()
        if is_successful(catalog_outcome):
            task_catalog = catalog_outcome.value_or([])
        else:
            error = catalog_outcome.failure()
            health.setdefault("warnings", []).append(
                f"task catalog discover failed: {error}"
            )
        health["next_steps"] = HealthReportAnalyzer.analyze(
            health, project_id, chain_dir, task_catalog=task_catalog
        )
        return health


# ── Module-level helpers ──


def _write_project_json(
    data_dir: str,
    pid: str,
    name: str,
    language: str,
    framework: str,
    repo_path: str,
    code_paths: list[str],
) -> None:
    project_json = {
        "project": {
            "meta": {
                "project_id": pid,
                "version": "1.0",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            "identity": {"name": name, "language": language, "framework": framework},
            "location": {"repo_path": repo_path, "code_paths": code_paths},
            "design": {"patterns": [], "layer_rules": {}, "forbidden_dependencies": []},
            "architecture": {"module_organization": ""},
            "coding": {
                "naming_convention": "",
                "formatter": "",
                "linter": "",
                "test_framework": "",
            },
            "quality_preferences": {},
            "team": {"owners": []},
            "dependencies": {"internal_modules": [], "external_services": []},
        }
    }
    with open(os.path.join(data_dir, "project.json"), "w") as f:
        json.dump(project_json, f, indent=2, ensure_ascii=False)


def _register_in_global_registry(
    registry_path: str,
    pid: str,
    name: str,
    repo_path: str,
) -> None:
    registry: dict = {"projects": {}}
    if os.path.exists(registry_path):
        read_outcome = _read_registry_core(registry_path)
        if is_successful(read_outcome):
            registry = read_outcome.value_or({})
        else:
            error = read_outcome.failure()
            logger.warning("registry read failed (will overwrite): %s", error)
    now = datetime.now(timezone.utc).isoformat()
    registry.setdefault("projects", {})[pid] = {
        "project_id": pid,
        "name": name,
        "repo_path": repo_path,
        "active": True,
        "registered_at": now,
        "last_accessed": now,
    }
    registry["updated_at"] = now
    os.makedirs(os.path.dirname(registry_path), exist_ok=True)
    with open(registry_path, "w") as f:
        json.dump(registry, f, indent=2, ensure_ascii=False)


def _copy_walkerignore(walker_root: str, project_path: str) -> None:
    template = os.path.join(walker_root, "data", ".walkerignore")
    dest = os.path.join(project_path, ".walkerignore")
    if not os.path.exists(dest) and os.path.exists(template):
        copy_outcome = _copy_walkerignore_core(template, dest)
        if not is_successful(copy_outcome):
            error = copy_outcome.failure()
            logger.warning(".walkerignore copy failed: %s", error)


def _auto_scan_cpg(project_path: str) -> tuple[bool, int]:
    from features.project import IgnoreRules

    code_detected = False
    ign = IgnoreRules(project_path)
    for root, dirs, files in os.walk(project_path):
        dirs[:] = ign.filter_dirs(dirs)
        for f in files:
            rel = os.path.relpath(os.path.join(root, f), project_path)
            if ign.is_ignored(rel):
                continue
            if f.endswith(
                (
                    ".py",
                    ".js",
                    ".ts",
                    ".rs",
                    ".go",
                    ".java",
                    ".tsx",
                    ".jsx",
                    ".css",
                    ".json",
                    ".md",
                )
            ):
                code_detected = True
                break
        if code_detected:
            break

    cpg_nodes = 0
    if code_detected:
        scan_outcome = _auto_scan_cpg_core(project_path)
        if is_successful(scan_outcome):
            cpg_nodes = scan_outcome.value_or(0)
        else:
            error = scan_outcome.failure()
            logger.warning("auto CPG scan failed: %s", error)
            cpg_nodes = 0
    return code_detected, cpg_nodes


def _reset_watcher_baseline(watcher: Any) -> None:
    """After prepare, reset change_detected.json to zero."""
    baseline = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "files_changed": [],
        "total_changes": 0,
        "new_changes": [],
        "fingerprint": "",
        "ckg_status": "up_to_date",
        "cpg_status": "up_to_date",
    }
    with open(watcher.change_path, "w") as f:
        json.dump(baseline, f, indent=2)


def _check_project_json(
    proj_json: str, project_id: str, repo_path: str, health: dict
) -> None:
    from features.project.domain.schema import ProjectSchemaValidator

    if not os.path.exists(proj_json):
        health["warnings"].append("project.json: not found in data/")
        return

    proj_outcome = _read_registry_core(proj_json)
    if not is_successful(proj_outcome):
        error = proj_outcome.failure()
        health["warnings"].append(f"project.json: error reading: {error}")
        return
    pd = proj_outcome.value_or({})
    pdata = pd.get("project", pd)
    identity = pdata.get("identity", {})
    location = pdata.get("location", {})
    health["project"] = {
        "name": identity.get("name", project_id),
        "language": identity.get("language", ""),
        "framework": identity.get("framework", ""),
        "repo_path": location.get("repo_path", repo_path),
        "code_paths": location.get("code_paths", []),
    }
    schema_errors = ProjectSchemaValidator.validate(pd)
    if schema_errors:
        health["warnings"].extend([f"project.json: {e}" for e in schema_errors[:3]])
    else:
        health["checks"]["project_json"] = "ok"


# ── Cores @safe (IO) ──────────────────────────────────────────────


@safe
def _scan_watcher_core(project_id: str, repo_path: str) -> dict:
    """Core puro: escanea cambios con FileWatcher (puede lanzar).

    Usa el data_dir canónico (.cognitive/projects/<pid>/data, fuera del árbol
    escaneado) para evitar la auto-invalidación del watcher en cada scan, y
    acota el scope a code_paths reales (sin copia-cog/sessions/etc).
    """
    from features.project import FileWatcher
    from shared.core.path_resolver import get_data_dir, get_code_paths

    return FileWatcher(
        repo_path,
        code_paths=get_code_paths(project_id) or None,
        data_dir=str(get_data_dir(project_id)),
    ).scan()


@safe
def _discover_task_catalog_core() -> list:
    """Core puro: descubre las tasks registradas (puede lanzar)."""
    from features.main.bootstrap import compose_app

    app = compose_app()
    return app.task_registry.discover().get("tasks", [])


@safe
def _read_registry_core(registry_path: str) -> dict:
    """Core puro: lee y parsea un JSON (puede lanzar)."""
    with open(registry_path) as f:
        return json.load(f)


@safe
def _copy_walkerignore_core(template: str, dest: str) -> None:
    """Core puro: copia .walkerignore (puede lanzar OSError)."""
    with open(template) as f_src, open(dest, "w") as f_dst:
        f_dst.write(f_src.read())


@safe
def _auto_scan_cpg_core(project_path: str) -> int:
    """Core puro: construye el CPG y devuelve nodos indexados (puede lanzar)."""
    from features.code_analysis import cpg_service

    builder = cpg_service(project_dir=project_path)
    if builder is None:
        return 0
    result = builder.update() if hasattr(builder, "update") else builder.build()
    if isinstance(result, dict):
        stats = result.get("stats", result)
        return stats.get("total_nodes", stats.get("nodes", 0))
    return 0
