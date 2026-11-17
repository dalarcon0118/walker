"""use_cases/project_metrics.py — ProjectMetricsUseCase.

Computa métricas del proyecto desde el CPG.
"""
from __future__ import annotations


class ProjectMetricsUseCase:
    """Compute project metrics from CPG."""

    @staticmethod
    def execute(chain_dir: str, file_filter: str = "") -> dict:
        from returns.pipeline import is_successful

        from fast_mcp.features.code_analysis.application.metrics_service import (
            compute_cpg_metrics,
        )
        from fast_mcp.features.code_analysis.application.review_service import ReviewUseCase
        from fast_mcp.shared_kernel.project_support import ProjectService

        paths = ProjectService.resolve_paths(chain_dir)
        svc = ReviewUseCase(chain_dir, data_dir=paths.data_dir or None)
        files = [file_filter] if file_filter else None
        result = svc.review_files(files=files)
        out = result.to_dict()

        # `review_files` reporta functions/classes/imports/cyclo en CERO fijo
        # (codereview/analysis.py: `cyclo = 0  # sin AST profundo`). Los números
        # reales ya están en el CPG, así que se sobreescriben desde ahí y se
        # conservan los gates/findings del review.
        cpg = compute_cpg_metrics(str(paths.project_root or chain_dir), str(paths.data_dir or ""), scope=file_filter)
        if is_successful(cpg):
            report = cpg.value_or(None)
            m = getattr(report, "metrics", None)
            if m is not None:
                metrics = out.setdefault("metrics", {})
                metrics.update({
                    "functions": getattr(m, "total_functions", metrics.get("functions", 0)),
                    "classes": getattr(m, "total_classes", metrics.get("classes", 0)),
                    "imports": getattr(m, "total_imports", metrics.get("imports", 0)),
                    "cyclo_avg": getattr(m, "cyclo_avg", metrics.get("cyclo_avg", 0.0)),
                    "cyclo_max": getattr(m, "cyclo_max", metrics.get("cyclo_max", 0)),
                })
                if getattr(m, "total_loc", 0):
                    metrics["loc"] = m.total_loc
        return out
