"""features/code_analysis/infrastructure/cpg_constants.py — Constantes del CPG.

Módulo HOJA (sin imports de code_analysis): rompe el ciclo
repository ↔ cognitive_cpg_repo (ADR-068 Fase 4 / M-02). `CPG_DERIVED_KEYS`
son proyecciones que NO viven en el SSOT gráfico (ADR-WALKER-055 F3):
archivos *.json derivados o recomputables on-the-fly.
"""
from __future__ import annotations

CPG_DERIVED_KEYS = frozenset({
    "business_layer", "quality_report", "bug_proposals",
    "dfg", "analysis_results",
})