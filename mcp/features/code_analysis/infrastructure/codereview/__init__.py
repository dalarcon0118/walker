"""codereview — Multi-language code quality analysis via ast-grep (sin qlty).

Usage:
    python3 -m codereview.main [--json] [--fast|--deep] <files...>
"""
from __future__ import annotations

from .config import AST_GREP_AVAILABLE
from .analysis import run_analysis, find_project_root
from .ast_grep import run_ast_grep, find_imports
from .coupling import coupling_metrics
from .deep import deep_analyze_checks, deep_analyze_file
from .quality import compute_quality_metrics

__all__ = [
    "run_analysis", "find_project_root",
    "run_ast_grep", "find_imports",
    "coupling_metrics",
    "deep_analyze_checks", "deep_analyze_file",
    "compute_quality_metrics",
    "AST_GREP_AVAILABLE",
]