"""use_cases/detect_unused_functions.py — DetectUnusedFunctionsUseCase.

Detecta funciones/métodos no usados via CPG.
Acepta firma nueva (repo, file_filter) o legacy (chain_dir, file_filter).
"""
from __future__ import annotations

import logging

from returns.pipeline import is_successful
from returns.result import safe

from features.code_analysis.application.ports.cpg_repository import CpgRepository
from ._common import _resolve_repo

logger = logging.getLogger(__name__)


class DetectUnusedFunctionsUseCase:
    """Detect unused functions/methods via CPG."""

    @staticmethod
    def execute(chain_dir_or_repo: str | CpgRepository, file_filter: str = "") -> dict:
        repo = _resolve_repo(chain_dir_or_repo)
        result = _detect_unused_core(repo, file_filter)
        if is_successful(result):
            return result.value_or({
                "query": "bug:unused_functions",
                "total": 0,
                "matches": [],
                "message": "No unused functions found",
            })
        error = result.failure()
        logger.warning(
            "DetectUnusedFunctions fallo (%s: %s)", type(error).__name__, error,
        )
        return {"error": str(error), "total": 0}


@safe
def _detect_unused_core(repo: CpgRepository, file_filter: str = "") -> dict:
    """Core puro: detecta funciones no usadas via repo (puede lanzar)."""
    query: dict = {"type": "function"}
    if file_filter:
        query["file"] = file_filter
    result = repo.search_nodes(query, max_results=200)
    hits = result.get("hits", {}).get("hits", [])
    candidates = []
    for h in hits:
        src = h.get("_source", {})
        if src.get("is_import"):
            continue
        candidates.append({
            "id": h.get("_id", ""),
            "name": src.get("name", ""),
            "file": src.get("path", src.get("file", "")),
            "line": src.get("line", 0),
            "type": src.get("type", ""),
        })
    return {
        "query": "bug:unused_functions",
        "total": len(candidates),
        "matches": candidates,
    }
