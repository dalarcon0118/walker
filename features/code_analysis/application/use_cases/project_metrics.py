"""use_cases/project_metrics.py — ProjectMetricsUseCase.

Computa métricas del proyecto desde el CPG.
"""
from __future__ import annotations


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
