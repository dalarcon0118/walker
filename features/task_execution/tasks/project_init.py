"""Project init tasks — thin adapters to project/ + spring use cases.

Result pattern (dry-python/returns): los cores con @safe nunca lanzan; los
wrappers deciden con is_successful y degradan con fallback explícito + log
(convention-001: nunca except silencioso).
"""

from __future__ import annotations
import logging
from dataclasses import dataclass
from typing import Any
from returns.pipeline import is_successful
from returns.result import safe
from libs.shared_domain import ExecutionContext, TaskResult
from libs.shared_domain.validated_vo import TaskInput
from features.project import ProjectService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProjectInitInput(TaskInput):
    """Input validado de build_project_config.

    project_id (required): id del proyecto.
    name: nombre (default project_id).
    repo_path: ruta del repo (default cwd).
    language: lenguaje (default python).
    framework: framework (default unknown).
    """

    project_id: str = ""
    name: str = ""
    repo_path: str = ""
    language: str = "python"
    framework: str = "unknown"

    def _validate(self) -> str:
        if not self.project_id:
            return "Requires --project_id"
        return ""


@safe
def _detect_context_core(project_path: str) -> dict:
    """Core puro: detecta el contexto del proyecto (puede lanzar)."""
    return ProjectService.detect_context(project_path)


def detect_project_context(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    project_path = data.get("path", ctx.chain_dir or __import__("os").getcwd())
    result = _detect_context_core(project_path)
    if is_successful(result):
        return TaskResult.success(data=result.value_or({}))
    error = result.failure()
    logger.warning(
        "detect_project_context fallo (%s: %s); degrada a detected=False",
        type(error).__name__, error,
    )
    return TaskResult.success(data={"detected": False, "error": str(error)})


@safe
def _create_project_core(project_id: str, repo_path: str, name: str, language: str, framework: str) -> None:
    """Core puro: crea el proyecto (puede lanzar ValueError)."""
    ProjectService.create_project(
        project_id=project_id,
        repo_path=repo_path,
        name=name,
        language=language,
        framework=framework,
    )


def build_project_config(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    from features.project import ProjectService

    input_result = ProjectInitInput.from_dict(data)
    if not input_result.ok:
        return TaskResult.failure(input_result.error or "project_id requerido")
    inp: ProjectInitInput = input_result.data
    project_id = inp.project_id
    name = inp.name or project_id
    repo_path = inp.repo_path or ctx.chain_dir or __import__("os").getcwd()
    language = inp.language
    framework = inp.framework

    created = _create_project_core(project_id, repo_path, name, language, framework)
    if is_successful(created):
        return TaskResult.success(data={"project_id": project_id, "created": True})
    error = created.failure()
    logger.warning(
        "build_project_config fallo (%s: %s)",
        type(error).__name__, error,
    )
    return TaskResult.failure(str(error))


def _resolve_project_root(project_id: str) -> str:
    """Resuelve el project_root desde el registry global."""
    from libs.path_resolver import get_walker_root
    import json
    import os

    registry_path = get_walker_root() / "data" / "projects.json"
    if registry_path.exists():
        with open(registry_path) as f:
            reg = json.load(f).get("projects", {}).get(project_id, {})
        repo_path = reg.get("repo_path", "")
        if repo_path:
            cog_proj = os.path.join(repo_path, ".cognitive", "projects", project_id)
            if os.path.isdir(cog_proj):
                return str(cog_proj)
    return ""


@safe
def _init_ckg_core(project_root: str) -> str:
    """Core puro: inicializa el archivo CKG vacío (puede lanzar)."""
    from features.cognitive_knowledge.application.ckg import CodeKnowledgeGraph
    import gzip
    import json
    import os

    CodeKnowledgeGraph(project_root)
    ckg_path = os.path.join(project_root, "data", "ckg.json.gz")
    os.makedirs(os.path.dirname(ckg_path), exist_ok=True)
    empty_ckg = {"index": "ckg", "docs": [], "edges": [], "token_index": {}}
    with open(ckg_path, "wb") as f:
        f.write(gzip.compress(json.dumps(empty_ckg).encode()))
    return ckg_path


def build_initial_ckg(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    chain_dir = ctx.chain_dir or ""
    project_root = ""

    if chain_dir:
        from features.project import ProjectService

        paths = ProjectService.resolve_paths(chain_dir)
        project_root = str(paths.project_root)
    else:
        project_id = data.get("_project_id", "")
        if project_id:
            project_root = _resolve_project_root(project_id)

    if not project_root:
        return TaskResult.failure(
            "Project not found",
            data={
                "error": "No active sprint and no project context available",
                "next_step": "Run 'walker prepare <path>' first, or 'walker <project>/status' if already registered",
            },
        )

    init_result = _init_ckg_core(project_root)
    if not is_successful(init_result):
        error = init_result.failure()
        logger.warning(
            "build_initial_ckg fallo (%s: %s)",
            type(error).__name__, error,
        )
        return TaskResult.success(
            data={
                "initialized": False,
                "error": str(error),
                "next_step": "Run 'walker prepare <path>' to set up the project first",
            }
        )
    ckg_path = init_result.value_or("")
    return TaskResult.success(
        data={
            "initialized": True,
            "ok": True,
            "message": f"CKG initialized at {ckg_path}",
            "next_step": "Run 'walker <project>/kg_extract' to populate CKG from code analysis",
        }
    )


def create_first_sprint(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    from features.task_execution.tasks.planning import plan_sprint

    return plan_sprint(ctx, data)


def scan_code_and_build_cpg(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    chain_dir = ctx.chain_dir or ""
    from features.project import ProjectService
    from libs.path_resolver import get_project_dir

    if chain_dir:
        paths = ProjectService.resolve_paths(chain_dir)
        project_root = paths.repo_path or paths.project_root
    elif data.get("project_id"):
        project_root = str(get_project_dir(data["project_id"]))
    else:
        return TaskResult.success(
            data={"built": False, "error": "No chain_dir or project_id"}
        )

    # Delegar en cpg_analyze (unificación ADR-021): no duplicar CPGBuilder
    from features.task_execution.tasks.cpg_analyze import cpg_analyze
    result = cpg_analyze(ctx, {"scope": project_root, "build": True})
    if result.ok:
        return TaskResult.success(data=result.data or {"built": True})
    error = result.error or "cpg build fallo"
    logger.warning("scan_code_and_build_cpg fallo: %s", error)
    return TaskResult.success(data={"built": False, "error": error})


def add_tasks(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Add specifications to a sprint. Delegates to EditSprintUseCase.

    REST: walker <project>/addSpec '{"tasks":[{"objective":"...","domain":"..."}]}'
    Legacy: walker <project>/add_spec '{"tasks":[...]}'
    """
    from features.delta_tracking.application.use_cases_sprint import EditSprintUseCase

    chain_dir = ctx.chain_dir or ""
    if not chain_dir:
        return TaskResult.failure(
            "No active sprint",
            data={
                "error": "No sprint context available. Create a sprint first.",
                "next_step": 'walker <project>/plan_sprint \'{"goal":"sprint goal"}\'',
            },
        )

    # Leer specs desde _payload (REST body directo: array de specs o {tasks:[...]})
    payload = data.get("_payload", data)
    if isinstance(payload, list):
        specs = payload  # body directo: [{"objective":"...", ...}]
    elif isinstance(payload, dict):
        specs = payload.get(
            "tasks", payload.get("specifications", data.get("tasks", []))
        )
    else:
        specs = data.get("tasks", [])

    if not specs:
        return TaskResult.failure(
            "Missing specifications",
            data={
                "error": "Provide specifications as JSON array in request body",
                "next_step": 'walker <project>/addSpec \'[{"objective":"...","domain":"..."}]\'',
            },
        )

    result = _add_specs_core(ctx, specs)
    if not is_successful(result):
        error = result.failure()
        logger.warning(
            "add_tasks fallo (%s: %s)",
            type(error).__name__, error,
        )
        return TaskResult.failure(
            str(error),
            data={
                "error": f"Failed to add specifications: {error}",
                "next_step": "Run 'walker <project>/status' to check sprint state",
            },
        )
    value = result.value_or({})
    return TaskResult.success(
        data={
            "specs_added": len(specs),
            "total_specs": value.get("spec_count", 0),
            "next_step": "Run 'walker <project>/status' to see the updated sprint",
        }
    )


@safe
def _add_specs_core(ctx: ExecutionContext, specs: list) -> dict:
    """Core puro: agrega specs al sprint vía EditSprintUseCase (puede lanzar)."""
    from features.delta_tracking.application.use_cases_sprint import EditSprintUseCase
    return EditSprintUseCase.execute(ctx, {"tasks": specs})


def map_legacy(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    from features.task_execution.tasks.legacy_mapper import map_legacy_workflow

    return map_legacy_workflow(ctx, data)


def register(registry: Any) -> None:
    # ── Tareas INTERNAS del workflow PROJECT_INIT (solo el motor las ejecuta) ──
    for name, fn, desc in [
        ("detect_project_context", detect_project_context, "Detect project context"),
        ("build_project_config", build_project_config, "Build project config"),
        ("create_first_sprint", create_first_sprint, "Create first sprint"),
        ("add_tasks", add_tasks, "Add specifications to existing sprint"),
        ("map_legacy", map_legacy, "Map legacy workflow"),
    ]:
        registry.register(
            name,
            fn,
            metadata={
                "description": desc,
                "tags": ["project-init"],
                "scope": "internal",
            },
        )
    # ── Tareas PÚBLICAS (actualizar memorias: el agente SÍ las puede llamar) ──
    registry.register(
        "build_initial_ckg",
        build_initial_ckg,
        metadata={
            "description": "Build initial CKG",
            "tags": ["project-init", "ckg"],
            "purpose": ["ckg_init"],
            "scope": "public",
        },
    )
    registry.register(
        "scan_code_and_build_cpg",
        scan_code_and_build_cpg,
        metadata={
            "description": "Scan code and build CPG",
            "tags": ["project-init", "cpg", "scan"],
            "purpose": ["cpg_build", "cpg_scan", "code_index"],
            "scope": "public",
        },
    )
