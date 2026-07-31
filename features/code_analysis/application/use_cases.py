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
from typing import Any

from libs.shared_domain import ExecutionContext, TaskResult
from features.project import ProjectService


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
            return {"structure": result[:20] if isinstance(result, list) else result, "files_analyzed": 0}
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
            return {"query": "bug:unused_functions", "total": 0, "matches": [],
                    "message": "No unused functions found"}
        return result


class ReviewCodeUseCase:
    """Execute code review via CPG."""

    @staticmethod
    def execute(chain_dir: str, files: list[str] | None = None, thresholds: dict | None = None) -> dict:
        from features.code_analysis.application.review_service import ReviewUseCase
        svc = ReviewUseCase(chain_dir)
        result = svc.review_files(files=files, thresholds=thresholds)
        return result.to_dict()


class ProjectMetricsUseCase:
    """Compute project metrics from CPG."""

    @staticmethod
    def execute(chain_dir: str, file_filter: str = "") -> dict:
        from features.code_analysis.application.review_service import ReviewUseCase
        svc = ReviewUseCase(chain_dir)
        files = [file_filter] if file_filter else None
        result = svc.review_files(files=files)
        return result.to_dict()
