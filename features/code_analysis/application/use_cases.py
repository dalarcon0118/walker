"""
application/use_cases.py — Code Analysis domain use cases.

SRP: Each use case orchestrates CPG services behind a clean interface.
     Tasks delegate here instead of calling CpgQueryService directly.
SSOT: The code_analysis feature is the SINGLE owner of CPG data.
      No other feature should read CPG files directly.

Result pattern (dry-python/returns): los cores puros con @safe nunca lanzan;
los wrappers deciden con is_successful y hacen fallback explícito con log
(convention-001: nunca except silencioso).
"""

from __future__ import annotations

import gzip
import json
import logging
import os

from returns.pipeline import is_successful
from returns.result import safe

from features.code_analysis.application.ports.cpg_repository import CpgRepository

logger = logging.getLogger(__name__)


# ── CPG health check (SSOT: solo code_analysis lee archivos CPG) ──────────


class CheckCpgHealthUseCase:
    """Check CPG health: node count and status.

    SRP: Single source of truth for CPG health data.
         No other feature module should read CPG files directly.
    """

    @staticmethod
    def execute(projects_data_dir: str) -> dict:
        """Check CPG status in the given data directory.

        Args:
            projects_data_dir: Path to .cognitive/projects/<pid>/data/

        Returns:
            dict with keys: status (str), node_count (int)
        """
        # Try .json.gz first (canonical), then .json (legacy)
        cpg_path = os.path.join(projects_data_dir, "cpg.json.gz")
        data = _read_cpg_file(cpg_path)
        if data is not None:
            total = len(data.get("nodes", data.get("docs", [])))
            return {"status": f"{total} nodes", "node_count": total}

        cpg_old = os.path.join(projects_data_dir, "cpg.json")
        old_data = _read_cpg_file(cpg_old)
        if old_data is not None:
            total = len(old_data.get("nodes", old_data.get("docs", [])))
            return {"status": f"{total} nodes", "node_count": total}

        return {"status": "not scanned", "node_count": 0}


def _read_cpg_file(path: str) -> dict | None:
    """Read a CPG file (.json or .json.gz).

    Result pattern: el core @safe nunca lanza; ante fallo devuelve None
    y loggea el error (convention-001).
    """
    if not os.path.exists(path):
        return None
    result = _read_cpg_file_core(path)
    if is_successful(result):
        return result.value_or(None)
    error = result.failure()
    logger.warning(
        "No se pudo leer CPG %s (%s: %s)", path, type(error).__name__, error,
    )
    return None


@safe
def _read_cpg_file_core(path: str) -> dict:
    """Core puro de lectura de CPG (puede lanzar)."""
    if path.endswith(".gz"):
        with gzip.open(path) as f:
            return json.load(f)
    with open(path) as f:
        return json.load(f)


class GatherCpgContextUseCase:
    """Gather CPG context for a task (files or general structure).

    Acepta firma nueva (repo, files) o legacy (chain_dir, files).
    """

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


class DetectUnusedFunctionsUseCase:
    """Detect unused functions/methods via CPG.

    Acepta firma nueva (repo, file_filter) o legacy (chain_dir, file_filter).
    """

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


def _resolve_repo(chain_dir_or_repo: str | CpgRepository) -> CpgRepository:
    """Resuelve un CpgRepository desde un chain_dir (legacy) o lo pasa tal cual."""
    if isinstance(chain_dir_or_repo, CpgRepository):
        return chain_dir_or_repo
    from features.code_analysis.infrastructure.cognitive_cpg_repo import CognitiveCpgRepository
    from features.project import ProjectService
    try:
        paths = ProjectService.resolve_paths(chain_dir_or_repo)
        data_dir = str(paths.project_root)
    except Exception:
        data_dir = chain_dir_or_repo
    project_id = os.path.basename(os.path.normpath(data_dir))
    return CognitiveCpgRepository(data_dir, project_id)


class ReviewCodeUseCase:
    """Execute code review via CPG.

    Devuelve DOS informes:
      1. quality: métricas + quality gates + smells + lint (primer informe)
      2. bug_proposals: análisis de bugs del CPG recién indexado (segundo
         informe post-index, para que el agente vea el estado del código)
    """

    @staticmethod
    def execute(
        chain_dir: str, files: list[str] | None = None, thresholds: dict | None = None
    ) -> dict:
        from features.code_analysis.application.review_service import ReviewUseCase
        from features.project import ProjectService

        # data_dir explícito (SSOT local) — evita que ReviewUseCase resuelva
        # al registro global (bug: devolvía el walker root).
        paths = ProjectService.resolve_paths(chain_dir)
        svc = ReviewUseCase(chain_dir, data_dir=paths.data_dir or None)
        result = svc.review_files(files=files, thresholds=thresholds)
        report = result.to_dict()

        # 2do informe: bug proposals del CPG (si el CPG ya está indexado)
        bug_proposals = _load_bug_proposals(chain_dir)
        report["bug_proposals"] = bug_proposals
        report["second_report"] = {
            "summary": "Bug analysis post-index (desde CPG)",
            "total_findings": bug_proposals.get("total_findings", 0),
            "by_type": bug_proposals.get("by_type", {}),
            "findings": bug_proposals.get("findings", [])[:50],
        }
        return report


def _load_bug_proposals(chain_dir: str) -> dict:
    """Carga bug_proposals.json del CPG del proyecto (si existe).

    Result pattern: fallback explícito con log (nunca silencioso).
    """
    try:
        from features.project import ProjectService

        paths = ProjectService.resolve_paths(chain_dir)
        data_dir = paths.data_dir or ""
        bp_path = os.path.join(data_dir, "bug_proposals.json")
        if os.path.isfile(bp_path):
            result = _load_bug_proposals_core(bp_path)
            if is_successful(result):
                return result.value_or({})
            error = result.failure()
            logger.warning(
                "bug_proposals.json ilegible (%s: %s); reporte vacio",
                type(error).__name__, error,
            )
    except Exception as e:
        logger.warning(
            "No se pudo resolver bug_proposals.json (%s: %s); reporte vacio",
            type(e).__name__, e,
        )
    return {"total_findings": 0, "by_type": {}, "findings": []}


@safe
def _load_bug_proposals_core(bp_path: str) -> dict:
    """Core puro de carga de bug_proposals (puede lanzar)."""
    with open(bp_path) as f:
        return json.load(f)


class ProjectMetricsUseCase:
    """Compute project metrics from CPG."""

    @staticmethod
    def execute(chain_dir: str, file_filter: str = "") -> dict:
        from features.code_analysis.application.review_service import ReviewUseCase
        from features.project import ProjectService

        paths = ProjectService.resolve_paths(chain_dir)
        svc = ReviewUseCase(chain_dir, data_dir=paths.data_dir or None)
        files = [file_filter] if file_filter else None
        result = svc.review_files(files=files)
        return result.to_dict()
