"""orchestrators/prepare_project_service.py — PrepareProjectService.

Cross-feature orchestrator: inicializa un proyecto (detecta propiedades,
crea .cognitive/, registra, escanea CPG, instala watcher).

SRP: orquesta ProjectDetector, CPGBuilder, CheckCkgHealthUseCase, FileWatcher.
No contiene lógica de negocio de un feature individual.
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


class PrepareProjectService:
    """Initialize a project: detect properties, create .cognitive/, register, scan CPG, setup watcher."""

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
        from features.cognitive_knowledge.application.services.ckg_service import (
            CkgApplicationService,
        )

        ckg_health_result = CkgApplicationService.check_health(projects_data_dir)
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


# ── Cores @safe (IO) ──────────────────────────────────────────────


@safe
def _scan_watcher_core(project_id: str, repo_path: str) -> dict:
    """Core puro: escanea cambios con FileWatcher (puede lanzar)."""
    from features.project import FileWatcher
    from shared.core.path_resolver import get_data_dir, get_code_paths

    return FileWatcher(
        repo_path,
        code_paths=get_code_paths(project_id) or None,
        data_dir=str(get_data_dir(project_id)),
    ).scan()


@safe
def _discover_task_catalog_core() -> list:
    """Core puro: descubre las tasks registradas via DI (puede lanzar).

    ADR-051 §2.6: orchestration/ no importa features.main; el registry
    llega por service locator (clave "task_registry").
    """
    from core.engine.bootstrap import resolve_service

    registry = resolve_service("task_registry")
    if registry is None:
        return []
    return registry.discover().get("tasks", [])


@safe
def _read_registry_core(registry_path: str) -> dict:
    """Core puro: lee y parsea un JSON (puede lanzar)."""
    with open(registry_path) as f:
        return json.load(f)


@safe
def _copy_walkerignore_core(template: str, dest: str) -> None:
    """Core puro: copia .walkerignore (puede lanzar OSError)."""
    with open(template) as f_src, open(dest) as f_dst:
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
