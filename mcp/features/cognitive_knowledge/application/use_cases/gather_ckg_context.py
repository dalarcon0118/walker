"""use_cases/gather_ckg_context.py — GatherCkgContextUseCase.

Reúne contexto CKG para una tarea.
"""
from __future__ import annotations

from returns.pipeline import is_successful
from returns.result import Result, Success, safe

from features.project import ProjectService


class GatherCkgContextUseCase:
    """Gather CKG context for a task."""

    @staticmethod
    def execute(
        chain_dir: str,
        query: str = "type:convention OR type:decision",
        task_id: str = "",
    ) -> Result[list, Exception]:
        @safe
        def _gather() -> list:
            from features.cognitive_knowledge import ckg_service

            paths = ProjectService.resolve_paths(chain_dir)
            ckg = ckg_service(str(paths.project_root))
            q = f"task_id:{task_id}" if task_id else query
            return ckg.search(q)[:20]

        result = _gather()
        if is_successful(result):
            return result
        else:
            return Success([])
