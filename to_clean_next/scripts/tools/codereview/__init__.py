"""codereview — Multi-language code quality analysis via Qlty + ast-grep.

Usage:
    python3 -m codereview.codereview [--json] [--fast|--deep] <files...>
    python3 -m codereview.main [--json] [--fast|--deep] <files...>
"""
from __future__ import annotations

from .config import QLTY_AVAILABLE, AST_GREP_AVAILABLE
from .analysis import run_analysis, find_project_root
from .qlty import run_metrics, run_metrics_functions, run_smells, run_check, run_fix
from .ast_grep import run_ast_grep, find_imports
from .coupling import coupling_metrics
from .deep import deep_analyze_checks
from .quality import compute_quality_metrics
from .cache import MetricsCache

__all__ = [
    "run_analysis", "find_project_root",
    "run_metrics", "run_metrics_functions", "run_smells", "run_check", "run_fix",
    "run_ast_grep", "find_imports",
    "coupling_metrics",
    "deep_analyze_checks",
    "compute_quality_metrics",
    "MetricsCache",
    "QLTY_AVAILABLE", "AST_GREP_AVAILABLE",
]
