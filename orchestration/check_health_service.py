"""orchestration/check_health_service.py — CheckHealthService.

Cross-feature orchestrator: produce un reporte completo de salud del proyecto
(project.json, watcher, CPG, CKG, sprint, next steps).

SRP: orquesta HealthReportAnalyzer, CpgService, CkgApplicationService,
WorkflowStatusService, FileWatcher.
No contiene lógica de negocio de un feature individual.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from returns.pipeline import is_successful
from returns.result import safe

logger = logging.getLogger(__name__)


class CheckHealthService:
    """Check project health: project.json, watcher, CPG, CKG, sprint, next steps."""

    @staticmethod
    def execute(project_id: str, repo_path: str, sprint_id: str = "") -> dict:
        from features.project import HealthReportAnalyzer
        from features.code_analysis.application.services.cpg_service import CpgService
        from features.cognitive_knowledge.application.services.ckg_service import (
            CkgApplicationService,
        )
        from core.engine.application.services.status_service import (
            WorkflowStatusService,
        )

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
        cpg_health = CpgService.check_health(project_id, projects_data_dir)
        health["checks"]["cpg"] = cpg_health["status"]
        if cpg_health["status"] == "not scanned":
            health["warnings"].append(
                "CPG not scanned. Run walker prepare or update_cpg"
            )

        # 4. Check CKG
        ckg_health_result = CkgApplicationService.check_health(projects_data_dir)
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

        sprint_health = WorkflowStatusService.check_sprint_status(chain_dir, project_id)
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
