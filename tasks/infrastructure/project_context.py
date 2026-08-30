"""task_execution/infrastructure/project_context.py — Resolución de proyecto.

Glue del task layer (ADR-051 G4): cómo el adapter resuelve project_id y
project_root desde el contexto del payload/chain. NO es lógica de negocio —
es traducción de contexto. Result pattern (convention-001).
"""
from __future__ import annotations

from returns.result import safe


@safe
def resolve_project_root(project_id: str) -> str:
    """Resuelve el directorio cognitivo del proyecto (puede lanzar)."""
    from app.path_resolver import get_project_dir

    return str(get_project_dir(project_id))


@safe
def resolve_project_id_from_chain(chain_dir: str) -> str:
    """Resuelve el project_id desde el chain_dir (puede lanzar)."""
    from features.project import ProjectService

    paths = ProjectService.resolve_paths(chain_dir)
    return paths.project_id
