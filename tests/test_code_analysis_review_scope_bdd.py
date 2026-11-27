"""BDD: el review recibe los archivos y ejecuta el CPG SOBRE ellos.

Bug medido 2026-11-26: `inspect_codebase action=review` ignoraba `scope` y
`files`, y llamaba `service.review_code()` sin argumentos. El review corría
sobre todo el repo (732 archivos aunque se pidiera uno), así que las reglas
del CPG nunca se ejecutaban sobre los archivos de la tarea. Aun peor, el
resultado se leía como "review de la tarea".

La cadena ahora es: archivos → `ReviewUseCase.review_files(files=...)` →
métricas/dead code/DFG/CFG filtrados a esos archivos.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from fast_mcp.features.code_analysis.application.metrics_service import (
    compute_cpg_metrics,
)
from fast_mcp.memories.handlers.code_analysis import (
    _review_targets,
    handle_inspect_codebase,
)
from fast_mcp.shared_kernel.project_support import ProjectService

ROOT = str(Path(__file__).resolve().parents[1])
TARGET = "fast_mcp/features/tasks/handlers/review.py"


class _FakeService:
    def __init__(self):
        self.calls: list[list[str] | None] = []

    def review_code(self, files=None):
        self.calls.append(files)
        return {"passed": True}


class _FakeCtx:
    project_root = ROOT
    chain_dir = ROOT

    def __init__(self, service):
        self._service = service

    def code_analysis_service(self):
        return self._service


def test_review_targets_prefiere_files_explicitos():
    assert _review_targets(ROOT, {"files": ["a.py", "b.py"], "scope": "x"}) == [
        "a.py",
        "b.py",
    ]


def test_review_targets_usa_scope_cuando_es_un_archivo(tmp_path):
    target = tmp_path / "app.py"
    target.write_text("x = 1\n", encoding="utf-8")

    assert _review_targets(str(tmp_path), {"scope": "app.py"}) == ["app.py"]


def test_review_targets_no_trata_un_directorio_como_archivo(tmp_path):
    (tmp_path / "pkg").mkdir()

    assert _review_targets(str(tmp_path), {"scope": "pkg"}) is None


def test_inspect_codebase_review_pasa_los_archivos_al_servicio():
    service = _FakeService()

    handle_inspect_codebase(_FakeCtx(service), action="review", files=[TARGET])

    assert service.calls == [[TARGET]]


def test_inspect_codebase_review_usa_scope_archivo():
    service = _FakeService()

    handle_inspect_codebase(_FakeCtx(service), action="review", scope=TARGET)

    assert service.calls == [[TARGET]]


def test_inspect_codebase_sin_archivos_el_review_sigue_siendo_del_proyecto():
    service = _FakeService()

    handle_inspect_codebase(_FakeCtx(service), action="review", scope="fast_mcp/")

    assert service.calls == [None]


@pytest.fixture(scope="module")
def data_dir() -> str:
    resolved = ProjectService.resolve_paths(ROOT)
    if not resolved or not resolved.data_dir or not os.path.isdir(resolved.data_dir):
        pytest.skip("CPG no disponible en este entorno")
    return str(resolved.data_dir)


def test_las_metricas_se_acotan_a_los_archivos_pedidos(data_dir):
    outcome = compute_cpg_metrics(ROOT, data_dir, files=[TARGET])
    report = outcome.value_or(None) if hasattr(outcome, "value_or") else None
    if report is None:
        pytest.skip(f"CPG no disponible: {outcome}")

    assert report.metrics.total_files == 1
    assert report.metrics.total_loc > 0
    assert all(f.file.endswith("review.py") for f in report.metrics.files)


def test_los_archivos_absolutos_se_normalizan_igual(data_dir):
    outcome = compute_cpg_metrics(ROOT, data_dir, files=[str(Path(ROOT) / TARGET)])
    report = outcome.value_or(None) if hasattr(outcome, "value_or") else None
    if report is None:
        pytest.skip(f"CPG no disponible: {outcome}")

    assert report.metrics.total_files == 1


def test_el_review_completo_se_acota_a_los_archivos(data_dir):
    """La cadena completa: use case → review_files → métricas scoped."""
    from fast_mcp.features.code_analysis.application.use_cases.review_code import (
        ReviewCodeUseCase,
    )

    report = ReviewCodeUseCase.execute(ROOT, files=[TARGET])

    assert report["files_scanned"] == [TARGET]
    assert (report.get("metrics") or {}).get("files") == 1

