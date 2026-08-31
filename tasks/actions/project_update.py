"""Task: update_project — update project.json with schema validation."""
from __future__ import annotations
from typing import Any
from returns.pipeline import is_successful
from returns.result import safe
from shared.core.engine.domain.execution import ExecutionContext, TaskResult


@safe
def _update_project_core(chain_dir: str, update_data: dict[str, dict]) -> dict:
    """Core puro: aplica el update vía ProjectService (puede lanzar)."""
    from features.project import ProjectService

    return ProjectService.update_project(chain_dir, update_data)


def update_project(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    chain_dir = ctx.chain_dir or ""
    if not chain_dir:
        return TaskResult.failure("chain_dir not available")

    # Build nested update data from flat flags
    update_data: dict[str, dict] = {}
    for key, val in data.items():
        if "." in key:
            section, field = key.split(".", 1)
            update_data.setdefault(section, {})[field] = val
        else:
            update_data.setdefault("identity", {})[key] = val

    # Result pattern (convention-001): el core @safe nunca lanza; ante
    # fallo devuelve Failure y el wrapper decide con is_successful.
    result = _update_project_core(chain_dir, update_data)
    if not is_successful(result):
        return TaskResult.failure(str(result.failure()))
    return TaskResult.success(data=result.value_or({}))


def register(registry: Any) -> None:
    registry.register("update_project", update_project,
                      metadata={"description": "Update project.json with schema validation",
                                "tags": ["project", "config"],
                                "purpose": ["project_update", "config_update"],
                                "input_schema": {
                                    "type": "object",
                                    "description": "Claves planas aplican a la sección 'identity' de project.json (ej. name, repo_path). Claves 'section.field' se mapean a sección.campo (ej. repo.url, ckg.enabled).",
                                    "examples": [{"name": "mi-proyecto"}, {"repo.url": "..."}],
                                    "additionalProperties": {"type": "string"},
                                }})
