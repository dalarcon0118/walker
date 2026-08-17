"""use_cases/gather_cpg_context.py — GatherCpgContextUseCase.

Reúne contexto CPG para una tarea (archivos o estructura general).
Acepta firma nueva (repo, files) o legacy (chain_dir, files).
"""
from __future__ import annotations

import logging

from returns.pipeline import is_successful
from returns.result import safe

from features.code_analysis.application.ports.cpg_repository import CpgRepository
from ._common import _resolve_repo

logger = logging.getLogger(__name__)


class GatherCpgContextUseCase:
    """Gather CPG context for a task (files or general structure)."""

    @staticmethod
    def execute(chain_dir_or_repo: str | CpgRepository, files: list[str] | None = None) -> dict:
        """Ejecuta el gather con fallback explícito (Result pattern)."""
        repo = _resolve_repo(chain_dir_or_repo)
        result = _gather_cpg_context_core(repo, files)
        if is_successful(result):
            return result.value_or({"files_analyzed": 0})
        error = result.failure()
        logger.warning(
            "GatherCpgContext fallo (%s: %s)", type(error).__name__, error,
        )
        return {"error": str(error), "files_analyzed": 0}


@safe
def _gather_cpg_context_core(
    repo: CpgRepository, files: list[str] | None = None
) -> dict:
    """Core puro del gather (puede lanzar)."""
    if files:
        context = {}
        for f in files:
            result = repo.search_nodes({"file": f})
            if result.get("hits", {}).get("total"):
                context[f] = result
        return {"files": context, "files_analyzed": len(files)}

    result = repo.search_nodes({"type": "function"}, max_results=20)
    hits = result.get("hits", {}).get("hits", [])
    if not hits:
        result = repo.search_nodes({"type": "class"}, max_results=20)
    return {
        "structure": result,
        "files_analyzed": 0,
    }
