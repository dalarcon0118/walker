"""read_code task — lee el contenido de un archivo del proyecto.

Task pública para consultar código fuente por ruta. Resuelve el project_root
desde chain_dir o project_id, normaliza la ruta y devuelve el contenido
con metadata de tamaño y existencia.
"""
from __future__ import annotations

import os
from typing import Any

from shared.core.engine.domain.execution import ExecutionContext, TaskResult


def read_code(ctx: ExecutionContext, data: dict[str, Any]) -> TaskResult:
    """Lee un archivo de código del proyecto.

    Args:
        ctx: contexto de ejecución con chain_dir.
        data: dict con `file` (ruta relativa o absoluta) y opcional `project_id`.

    Returns:
        TaskResult con `content`, `file`, `exists`, `size` y `project_root`.
    """
    chain_dir = ctx.chain_dir or ""
    file_path = data.get("file", "")
    project_id = data.get("project_id", "")
    if not file_path:
        return TaskResult.failure(
            "file is required",
            data={"next_step": "Usage: read_code '{\"file\":\"src/auth.py\"}'"},
        )

    project_root = ""
    repo_root = ""
    if chain_dir:
        try:
            from features.project import ProjectService
            paths = ProjectService.resolve_paths(chain_dir)
            project_root = str(paths.project_root)
            repo_root = paths.repo_path or ""
        except Exception:
            pass
    if not repo_root and project_id:
        try:
            from features.project.application.services.project_service import ProjectService
            project = ProjectService.from_project_id(project_id)
            repo_root = project.manifest.repo_path or ""
        except Exception:
            pass
    if not repo_root and not project_root:
        try:
            from app.path_resolver import get_project_dir
            project_root = str(get_project_dir(project_id))
        except Exception:
            pass

    candidate = file_path
    if os.path.isabs(file_path):
        candidate = file_path
    elif repo_root:
        candidate = os.path.join(repo_root, file_path)
    elif project_root:
        candidate = os.path.join(project_root, file_path)

    candidate = os.path.normpath(candidate)
    if not os.path.isfile(candidate):
        return TaskResult.failure(
            f"file not found: {file_path}",
            data={"file": file_path, "project_root": project_root, "exists": False},
        )

    try:
        with open(candidate, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception as exc:
        return TaskResult.failure(
            f"read_code failed: {exc}",
            data={"file": file_path, "project_root": project_root, "exists": True},
        )

    return TaskResult.success(
        data={
            "file": file_path,
            "project_root": project_root,
            "exists": True,
            "size": len(content),
            "content": content,
        }
    )


def register(registry: Any) -> None:
    registry.register(
        "read_code",
        read_code,
        metadata={
            "description": "Lee el contenido de un archivo del proyecto por ruta.",
            "usage": 'walker <project>/read_code \'{"file":"src/auth.py"}\'',
            "example": 'walker <project>/read_code \'{"file":"src/auth.py","project_id":"mi-app"}\'',
            "tags": ["code", "read", "file"],
            "scope": "public",
            "input_schema": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "description": "Ruta del archivo a leer (relativa al project_root o absoluta).",
                    },
                    "project_id": {
                        "type": "string",
                        "description": "ID del proyecto (opcional si el contexto lo resuelve).",
                    },
                },
                "required": ["file"],
            },
            "output_schema": {
                "type": "object",
                "properties": {
                    "file": {"type": "string"},
                    "project_root": {"type": "string"},
                    "exists": {"type": "boolean"},
                    "size": {"type": "integer"},
                    "content": {"type": "string"},
                },
                "required": ["file", "exists", "content"],
            },
        },
    )
