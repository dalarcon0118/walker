"""
application/use_cases.py — Code Analysis domain use cases.

SRP: Each use case orchestrates CPG services behind a clean interface.
     Tasks delegate here instead of calling CpgQueryService directly.
SSOT: The code_analysis feature is the SINGLE owner of CPG data.
      No other feature should read CPG files directly.
"""

from __future__ import annotations

import gzip
import json
import os


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
    """Read a CPG file (.json or .json.gz). Returns None on error/missing."""
    if not os.path.exists(path):
        return None
    try:
        if path.endswith(".gz"):
            with gzip.open(path) as f:
                return json.load(f)
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


class GatherCpgContextUseCase:
    """Gather CPG context for a task (files or general structure)."""

    @staticmethod
    def execute(chain_dir: str, files: list[str] | None = None) -> dict:
        try:
            from features.code_analysis import CpgQueryService

            svc = CpgQueryService(chain_dir)
            if files:
                context = {}
                for f in files:
                    result = svc.grep(f"file:{f}")
                    if result:
                        context[f] = result
                return {"files": context, "files_analyzed": len(files)}
            result = svc.grep("function:") or svc.grep("class:")
            return {
                "structure": result[:20] if isinstance(result, list) else result,
                "files_analyzed": 0,
            }
        except Exception as e:
            return {"error": str(e), "files_analyzed": 0}


class DetectUnusedFunctionsUseCase:
    """Detect unused functions/methods via CPG."""

    @staticmethod
    def execute(chain_dir: str, file_filter: str = "") -> dict:
        from features.code_analysis import CpgQueryService

        svc = CpgQueryService(chain_dir)
        filters = [("bug", "unused_functions")]
        if file_filter:
            filters.append(("file", file_filter))
        result = svc.grep(" ".join(f"{k}:{v}" for k, v in filters))
        if not result:
            return {
                "query": "bug:unused_functions",
                "total": 0,
                "matches": [],
                "message": "No unused functions found",
            }
        return result


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

        svc = ReviewUseCase(chain_dir)
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
    """Carga bug_proposals.json del CPG del proyecto (si existe)."""
    try:
        from libs.path_resolver import get_project_dir
        import os

        # resolver project_id desde chain_dir
        from features.project import ProjectService

        paths = ProjectService.resolve_paths(chain_dir)
        pid = paths.project_id
        proj = get_project_dir(pid)
        data_dir = paths.data_dir or os.path.join(str(proj), "data")
        bp_path = os.path.join(data_dir, "bug_proposals.json")
        if os.path.isfile(bp_path):
            with open(bp_path) as f:
                return json.load(f)
    except Exception:
        pass
    return {"total_findings": 0, "by_type": {}, "findings": []}


class ProjectMetricsUseCase:
    """Compute project metrics from CPG."""

    @staticmethod
    def execute(chain_dir: str, file_filter: str = "") -> dict:
        from features.code_analysis.application.review_service import ReviewUseCase

        svc = ReviewUseCase(chain_dir)
        files = [file_filter] if file_filter else None
        result = svc.review_files(files=files)
        return result.to_dict()
