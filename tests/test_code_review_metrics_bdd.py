"""BDD: las métricas del code review salen del CPG v2, no del blob legacy.

Medido 2026-11-18: `ReviewCodeUseCase.execute` devolvía
`{files: 1309, loc: 324265, functions: 0, classes: 0, imports: 0, cyclo: 0}`
aunque el CPG indexado tenía 8.812 nodos. La causa no era el review: era
`ReviewUseCase._resolve_repo_and_data_dir`, que trataba el `data_dir`
canónico (`.cognitive/projects/<id>/data`) como raíz del proyecto y devolvía
`<repo>/data` — el store legacy, vacío.

Con el resolver correcto el mismo CPG reporta ~576 archivos, ~4.134 funciones,
~441 clases y cyclo medio ~4.0.
"""
from __future__ import annotations

import os

import pytest

from fast_mcp.features.code_analysis.application.metrics_service import compute_cpg_metrics
from fast_mcp.features.code_analysis.application.review_service import ReviewUseCase
from fast_mcp.features.code_analysis.application.use_cases.review_code import ReviewCodeUseCase
from fast_mcp.shared_kernel.project_support import ProjectService

ROOT = "/Users/davidmartinez/develop/Active-projects/walker"


@pytest.fixture(scope="module")
def paths():
    resolved = ProjectService.resolve_paths(ROOT)
    if not resolved or not resolved.data_dir:
        pytest.skip("proyecto no resoluble")
    if not os.path.isdir(resolved.data_dir):
        pytest.skip("data_dir canónico no existe")
    return resolved


def test_el_resolver_devuelve_el_data_dir_canonico_y_no_repo_data(paths):
    svc = ReviewUseCase(ROOT, data_dir=paths.data_dir)

    repo_root, data_dir = svc._resolve_repo_and_data_dir()  # noqa: SLF001

    assert repo_root == os.path.abspath(ROOT)
    assert data_dir == paths.data_dir
    assert not data_dir.endswith(f"{os.sep}repo{os.sep}data")
    assert ".cognitive" in data_dir


def test_compute_cpg_metrics_lee_funciones_y_clases_del_store_v2(paths):
    outcome = compute_cpg_metrics(ROOT, paths.data_dir, scope="")

    report = outcome.value_or(None) if hasattr(outcome, "value_or") else None
    if report is None:
        pytest.skip(f"CPG no disponible: {outcome}")

    metrics = report.metrics
    assert metrics.total_files > 0
    assert metrics.total_loc > 0
    assert metrics.total_functions > 0, "el store v2 tiene funciones; 0 = leyó el blob legacy"
    assert metrics.total_classes > 0
    assert metrics.cyclo_avg > 0, "cyclo se llena al indexar; 0 = métricas vacías"
    assert metrics.cyclo_max >= metrics.cyclo_avg


@pytest.fixture(scope="module")
def review_report(paths):
    return ReviewCodeUseCase.execute(ROOT, files=[])


def test_el_review_completo_reporta_metricas_reales(review_report):
    report = review_report

    metrics = report.get("metrics") or {}
    assert metrics.get("files", 0) > 0
    assert metrics.get("loc", 0) > 0
    assert metrics.get("functions", 0) > 0
    assert metrics.get("classes", 0) > 0
    assert metrics.get("imports", 0) > 0
    # El detalle por archivo también traía loc=0: el total se calculaba, el
    # desglose no.
    assert metrics["per_file"][0]["loc"] > 0


def test_el_gate_de_dead_code_ya_no_es_el_unico_dato(review_report):
    """El reporte trae métricas + gates; antes `passed` fallaba con todo en 0."""
    report = review_report
    gates = {g["name"]: g for g in report.get("gates") or []}

    assert "dead_code" in gates  # sigue siendo el gate rojo conocido (falsos positivos)
    assert gates["loc_per_file"]["actual"] > 0
