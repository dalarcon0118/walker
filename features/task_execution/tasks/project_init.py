"""Project init tasks — thin adapters to project/ + spring use cases."""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from libs.shared_domain import ExecutionContext, TaskResult
from libs.shared_domain.validated_vo import TaskInput
from features.project.application.use_cases import DetectProjectContextUseCase


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


def detect_project_context(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    try:
        project_path = data.get("path", ctx.chain_dir or __import__("os").getcwd())
        result = DetectProjectContextUseCase.execute(project_path)
        return TaskResult.success(data=result)
    except Exception as e:
        return TaskResult.success(data={"detected": False, "error": str(e)})


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
    try:
        ProjectService.create_project(
            project_id=project_id,
            repo_path=repo_path,
            name=name,
            language=language,
            framework=framework,
        )
        return TaskResult.success(data={"project_id": project_id, "created": True})
    except ValueError as e:
        return TaskResult.failure(str(e))


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


def build_initial_ckg(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    try:
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

        from features.cognitive_knowledge.application.ckg import CodeKnowledgeGraph
        import gzip
        import json
        import os

        CodeKnowledgeGraph(project_root)
        # Crear archivo CKG vacio con estructura limpia (sin nodos marcadores)
        ckg_path = os.path.join(project_root, "data", "ckg.json.gz")
        os.makedirs(os.path.dirname(ckg_path), exist_ok=True)
        empty_ckg = {"index": "ckg", "docs": [], "edges": [], "token_index": {}}
        with open(ckg_path, "wb") as f:
            f.write(gzip.compress(json.dumps(empty_ckg).encode()))
        return TaskResult.success(
            data={
                "initialized": True,
                "ok": True,
                "message": f"CKG initialized at {ckg_path}",
                "next_step": "Run 'walker <project>/kg_extract' to populate CKG from code analysis",
            }
        )
    except Exception as e:
        return TaskResult.success(
            data={
                "initialized": False,
                "error": str(e),
                "next_step": "Run 'walker prepare <path>' to set up the project first",
            }
        )


def create_first_sprint(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    from features.task_execution.tasks.planning import plan_sprint

    return plan_sprint(ctx, data)


def scan_code_and_build_cpg(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    chain_dir = ctx.chain_dir or ""
    try:
        from features.code_analysis import CPGBuilder
        from features.project import ProjectService
        from libs.path_resolver import get_project_dir

        if chain_dir:
            paths = ProjectService.resolve_paths(chain_dir)
            project_root = paths.project_root
        elif data.get("project_id"):
            project_root = str(get_project_dir(data["project_id"]))
        else:
            return TaskResult.success(
                data={"built": False, "error": "No chain_dir or project_id"}
            )

        builder = CPGBuilder(project_root)
        result = builder.build()
        return TaskResult.success(
            data={
                "built": True,
                "nodes": result.get("nodes", 0),
                "edges": result.get("edges", 0),
            }
        )
    except Exception as e:
        return TaskResult.success(data={"built": False, "error": str(e)})


def add_tasks(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Add specifications to a sprint. Delegates to EditSprintUseCase.

    REST: walker <project>/addSpec '{"tasks":[{"objective":"...","domain":"..."}]}'
    Legacy: walker <project>/add_spec '{"tasks":[...]}'
    """
    from features.spring.application.use_cases import EditSprintUseCase

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

    try:
        result = EditSprintUseCase.execute(ctx, {"tasks": specs})
        return TaskResult.success(
            data={
                "specs_added": len(specs),
                "total_specs": result.get("spec_count", 0),
                "next_step": "Run 'walker <project>/status' to see the updated sprint",
            }
        )
    except Exception as e:
        return TaskResult.failure(
            str(e),
            data={
                "error": f"Failed to add specifications: {e}",
                "next_step": "Run 'walker <project>/status' to check sprint state",
            },
        )


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
