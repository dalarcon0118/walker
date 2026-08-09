"""Legacy mapper tasks — thin adapters to project/ use cases.

Result pattern (dry-python/returns): los cores con @safe nunca lanzan; los
wrappers deciden con is_successful y hacen fallback explícito con log
(convention-001: nunca except silencioso).
"""
from __future__ import annotations
import logging
import os
from typing import Any
from returns.pipeline import is_successful
from returns.result import safe
from libs.shared_domain import ExecutionContext, TaskResult
from features.project import ProjectService

logger = logging.getLogger(__name__)


@safe
def _detect_legacy_core(path: str) -> dict:
    """Core puro: detección de proyecto legacy (puede lanzar)."""
    return ProjectService.detect_legacy(path)


@safe
def _import_legacy_core(path: str, project_id: str, chains: list) -> dict:
    """Core puro: importación de historial legacy (puede lanzar)."""
    return ProjectService.import_legacy_history(path, project_id, chains)


def detect_legacy(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    path = data.get("path", ctx.chain_dir or os.getcwd())
    result = _detect_legacy_core(path)
    if is_successful(result):
        return TaskResult.success(data=result.value_or({}))
    error = result.failure()
    logger.warning(
        "detect_legacy fallo (%s: %s); degrada a is_legacy=False",
        type(error).__name__, error,
    )
    return TaskResult.success(data={"is_legacy": False, "error": str(error)})


def map_legacy_workflow(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    return TaskResult.success(data={"mapped": True, "workflow_version": "4.0"})


def import_legacy_history(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    path = data.get("path", ctx.chain_dir or os.getcwd())
    project_id = data.get("project_id", "")
    chains = data.get("chains", [])
    result = _import_legacy_core(path, project_id, chains)
    if is_successful(result):
        return TaskResult.success(data=result.value_or({}))
    error = result.failure()
    logger.warning(
        "import_legacy_history fallo (%s: %s)",
        type(error).__name__, error,
    )
    return TaskResult.failure(str(error))


def register(registry: Any) -> None:
    for name, fn, desc in [
        ("detect_legacy", detect_legacy, "Detect legacy walker project"),
        ("map_legacy_workflow", map_legacy_workflow, "Map legacy workflow to v4"),
        ("import_legacy_history", import_legacy_history, "Import legacy chain history"),
    ]:
        registry.register(name, fn, metadata={"description": desc, "tags": ["legacy"], "scope": "internal"})
