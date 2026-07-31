#!/usr/bin/env python3
"""Wrapper de compatibilidad — delega a tools/codereview/.
  
Antes era un monolito de 2100+ líneas. Ahora está dividido en:
  tools/codereview/
    config.py       — Constantes, patrones, detección de herramientas
    qlty.py         — Wrappers para qlty metrics/smells/check
    ast_grep.py     — ast-grep multi-lenguaje
    coupling.py     — Coupling metrics multi-lenguaje
    deep.py         — Deep analysis (ast-grep patterns)
    quality.py      — Quality metrics computation
    analysis.py     — Orquestación del análisis
    main.py         — CLI entry point
    __init__.py     — Public API
"""
from __future__ import annotations

import sys
from pathlib import Path

# Add parent to path for codereview import
_THIS_DIR = str(Path(__file__).parent)
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

# Re-export public API para compatibilidad con task/qlty_check.py y otros
from codereview import (
    run_analysis,
    find_project_root,
    run_metrics,
    run_metrics_functions,
    run_smells,
    run_check,
    run_fix,
    run_ast_grep,
    find_imports,
    coupling_metrics,
    deep_analyze_checks,
    compute_quality_metrics,
    QLTY_AVAILABLE,
    AST_GREP_AVAILABLE,
)
from codereview.main import main

__all__ = [
    "run_analysis", "find_project_root", "main",
    "run_metrics", "run_metrics_functions", "run_smells", "run_check", "run_fix",
    "run_ast_grep", "find_imports",
    "coupling_metrics",
    "deep_analyze_checks",
    "compute_quality_metrics",
    "QLTY_AVAILABLE", "AST_GREP_AVAILABLE",
]

if __name__ == "__main__":
    sys.exit(main())
