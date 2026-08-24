"""Project init tasks — thin adapters to project/ + spring use cases.

Result pattern (dry-python/returns): los cores con @safe nunca lanzan; los
wrappers deciden con is_successful y degradan con fallback explícito + log
(convention-001: nunca except silencioso).
"""

from __future__ import annotations
import logging
import os
from dataclasses import dataclass
from typing import Any

from returns.pipeline import is_successful
from returns.result import safe
from shared.common.shared_domain import ExecutionContext, TaskResult
from shared.common.shared_domain.validated_vo import TaskInput
from features.project import ProjectService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProjectInitInput(TaskInput):
    """Input validado de build_project_config (project_id requerido)."""

    project_id: str = ""
    name: str = ""
    repo_path: str = ""
    language: str = "python"
    framework: str = "unknown"

    def _validate(self) -> str:
        return "" if self.project_id else "Requires --project_id"


def _resolve_project_root(chain_dir: str = "", project_id: str = "") -> str:
    """Resuelve el project_root desde chain_dir o registry global (glue)."""
    if chain_dir:
        paths = ProjectService.resolve_paths(chain_dir)
        return str(paths.project_root)
    if project_id:
        from features.main.task_execution.infrastructure.kg_context import (
            _resolve_ckg_project_root,
        )

        return _resolve_ckg_project_root(project_id)
    return ""



def detect_project_context(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    project_path = data.get("path", ctx.chain_dir or os.getcwd())
    result = safe(ProjectService.detect_context)(project_path)
    if is_successful(result):
        return TaskResult.success(data=result.value_or({}))
    error = result.failure()
    logger.warning(
        "detect_project_context fallo (%s: %s); degrada a detected=False",
        type(error).__name__, error,
    )
    return TaskResult.success(data={"detected": False, "error": str(error)})



def build_project_config(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    input_result = ProjectInitInput.from_dict(data)
    if not input_result.ok:
        return TaskResult.failure(input_result.error or "project_id requerido")
    inp: ProjectInitInput = input_result.data
    created = safe(ProjectService.create_project)(
        project_id=inp.project_id,
        repo_path=inp.repo_path or ctx.chain_dir or os.getcwd(),
        name=inp.name or inp.project_id,
        language=inp.language,
        framework=inp.framework,
    )
    if is_successful(created):
        return TaskResult.success(data={"project_id": inp.project_id, "created": True})
    error = created.failure()
    logger.warning("build_project_config fallo (%s: %s)", type(error).__name__, error)
    return TaskResult.failure(str(error))



def initialize_knowledge_base(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    project_root = _resolve_project_root(
        chain_dir=ctx.chain_dir or "", project_id=data.get("_project_id", "")
    )
    if not project_root:
        return TaskResult.failure(
            "Project not found",
            data={
                "error": "No active sprint and no project context available",
                "next_step": "Run 'walker prepare <path>' first, or 'walker <project>/status' if already registered",
            },
        )

    from features.cognitive_knowledge.application.services.ckg_service import (
        CkgApplicationService,
    )

    init_result = safe(CkgApplicationService.init_ckg)(project_root)
    if not is_successful(init_result):
        error = init_result.failure()
        logger.warning("initialize_knowledge_base fallo (%s: %s)", type(error).__name__, error)
        return TaskResult.success(
            data={"initialized": False, "error": str(error),
                  "next_step": "Run 'walker prepare <path>' to set up the project first"}
        )
    ckg_path = init_result.value_or({}).get("ckg_path", "")
    return TaskResult.success(
        data={"initialized": True, "ok": True, "message": f"CKG initialized at {ckg_path}",
              "next_step": "Run 'walker <project>/extract_knowledge' to populate CKG from code analysis"}
    )



def create_first_sprint(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    from features.main.task_execution.tasks.sprint_planning import plan_sprint

    return plan_sprint(ctx, data)



def build_code_index(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Full CPG rebuild from scratch.

    Use this for initial setup or when the index is corrupted/stale.
    For routine maintenance, use update_incremental_code.
    """
    # ADR-WALKER-053: contexto explícito > ambiental, ambas claves de proyecto
    pid = (data.get("project_id") or data.get("_project_id") or "").strip()
    project_root = ""
    if pid:
        from features.project.application.services.project_service import ProjectService as _PS

        project_root = str(_PS.from_project_id(pid).manifest.repo_path or "")
    if not project_root and ctx.chain_dir:
        paths = ProjectService.resolve_paths(ctx.chain_dir)
        project_root = paths.repo_path or paths.project_root
    if not project_root:
        return TaskResult.success(
            data={
                "built": False,
                "error": "No chain_dir or project_id",
                "next_step": 'Pasá {"project_id": "..."} o corré dentro del proyecto',
            }
        )

    from features.main.task_execution.tasks.inspect_codebase import inspect_codebase  # ADR-021: no duplicar CPGBuilder

    payload: dict[str, Any] = {
        "scope": project_root,
        "build": True,
        "project_id": pid,
    }
    result = inspect_codebase(ctx, payload)
    if not result.is_success():
        error = result.error or "unknown error"
        logger.warning("build_code_index failed (%s)", error)
        return TaskResult.success(
            data={"built": False, "error": str(error), "next_step": "Check CPG configuration"}
        )

    built = result.data or {}
    return TaskResult.success(
        data={
            "built": True,
            "project_root": project_root,
            "result": built,
        }
    )


def register(registry: Any) -> None:
    registry.register(
        "detect_project_context",
        detect_project_context,
        metadata={
            "description": "Detecta el contexto del proyecto desde la ruta.",
            "usage": "walker <project>/detect_project_context",
            "tags": ["project", "init"],
            "scope": "internal",
        },
    )
    registry.register(
        "build_project_config",
        build_project_config,
        metadata={
            "description": "Crea la configuracion del proyecto.",
            "usage": "walker <project>/build_project_config",
            "tags": ["project", "init"],
            "scope": "internal",
        },
    )
    registry.register(
        "initialize_knowledge_base",
        initialize_knowledge_base,
        metadata={
            "description": "Inicializa la base de conocimiento CKG.",
            "usage": "walker <project>/initialize_knowledge_base",
            "tags": ["knowledge", "init"],
            "scope": "public",
        },
    )
    registry.register(
        "create_first_sprint",
        create_first_sprint,
        metadata={
            "description": "Crea el primer sprint del proyecto.",
            "usage": "walker <project>/create_first_sprint",
            "tags": ["sprint", "init"],
            "scope": "internal",
        },
    )
    registry.register(
        "build_code_index",
        build_code_index,
        metadata={
            "description": (
                "Full CPG rebuild from scratch. "
                "Use this for initial setup or when the index is corrupted/stale. "
                "For routine maintenance, use update_incremental_code."
            ),
            "usage": 'walker <project>/build_code_index',
            "example": 'walker <project>/build_code_index',
            "query_instructions": (
                "When to use: "
                "(1) Initial project setup; "
                "(2) CPG is corrupted or stale; "
                "(3) Need a fresh full rebuild. "
                "For incremental updates after code changes, use update_incremental_code."
            ),
            "tags": ["code", "cpg", "build", "rebuild"],
            "scope": "public",
        },
    )
