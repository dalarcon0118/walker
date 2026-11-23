"""tests/test_quality_report_files_bdd.py — el guard de falso PASS lee el reporte.

`MetricsReport.to_dict()` es PLANO (`files` en la raíz), no anidado bajo
`metrics`. El guard de `_persist_quality_report` leía solo `metrics.files`,
veía 0 y marcaba `degraded: true` un reporte con 454 archivos y 1.310
funciones reales (medido 2026-11-22 en client-chat).
"""

from __future__ import annotations

from fast_mcp.features.code_analysis.application.build_service import (
    _report_files_count,
)


def test_reporte_plano_devuelve_los_archivos():
    assert _report_files_count({"files": 454, "functions": 1310}) == 454


def test_reporte_con_metrics_anidado():
    assert _report_files_count({"metrics": {"files": 3}}) == 3


def test_reporte_vacio_es_cero():
    assert _report_files_count({}) == 0
    assert _report_files_count({"files": 0}) == 0


def test_metrics_anidado_manda_si_existe():
    assert _report_files_count({"metrics": {"files": 2}, "files": 999}) == 2
