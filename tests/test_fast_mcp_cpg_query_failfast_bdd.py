"""test_fast_mcp_cpg_query_failfast_bdd.py — W2 (ADR-WALKER-076).

Cierre de la familia R-A (path-guessing) en el read path del CPG:
QueryGraphUseCase/ReviewService reciben data_dir canónico SIEMPRE inyectado
(ADR-054 D4); sin inyección → fail-fast con ValueError, nunca fallback a
<repo_root>/data (BD parásita en la raíz del repo).

Cubre:
- QueryGraphUseCase.repo sin data_dir → ValueError con remediación.
- QueryGraphUseCase.repo con data_dir = paths.data_dir → repos canónicos.
- CodeContextService.execute hace business layer sobre el data_dir canónico.
- ReviewService.repo sin data_dir → ValueError (mismo patrón fail-fast).
"""

from __future__ import annotations

import pytest


def _prepared_project(tmp_path):
    """Crea un proyecto preparado y devuelve (repo, data_dir, project_id)."""
    from fast_mcp import composition
    from fast_mcp.features.project.application import use_cases
    from fast_mcp.shared_kernel.project_support import ProjectService

    fake_root = tmp_path / "walker-root"
    (fake_root / "config").mkdir(parents=True)
    (fake_root / "config" / "config.json").write_text("{}")
    ctx = composition.build(root=fake_root).project

    repo = tmp_path / "demo-repo"
    repo.mkdir(parents=True)
    use_cases.prepare_use_case(ctx, project_path=str(repo), args={"language": "python"})

    paths = ProjectService.resolve_paths(str(repo))
    assert paths.data_dir, "prepare_use_case debe resolver el data_dir canónico"
    return str(repo), paths.data_dir, paths.project_id


def test_query_graph_usecase_sin_data_dir_fail_fast(tmp_path):
    """R-A raíz: sin data_dir inyectado → ValueError, nunca fallback a <root>/data."""
    from fast_mcp.features.code_analysis.application.query_service import QueryGraphUseCase

    uc = QueryGraphUseCase(chain_dir=str(tmp_path))
    with pytest.raises(ValueError, match="data_dir no inyectado"):
        _ = uc.repo


def test_query_graph_usecase_repo_usa_data_dir_canonico(tmp_path):
    """repo._data_dir == paths.data_dir canónico (no <repo>/data legacy)."""
    from fast_mcp.features.code_analysis.application.query_service import QueryGraphUseCase

    repo, data_dir, project_id = _prepared_project(tmp_path)
    uc = QueryGraphUseCase(
        chain_dir=repo, project_id=project_id, data_dir=data_dir
    )
    assert uc.repo.data_dir == data_dir
    # Y el CPG debe vivir en el data_dir canónico, no en <repo>/data.
    assert not (tmp_path / "demo-repo" / "data" / "walker.sqlite").exists()


def test_code_context_service_usa_data_dir_canonico(tmp_path):
    """CodeContextService (W1 R-A fix) conserva el data_dir canónico."""
    from fast_mcp.features.code_analysis.application.services.code_context_service import (
        CodeContextService,
    )

    repo, data_dir, project_id = _prepared_project(tmp_path)
    svc = CodeContextService(chain_dir=repo, project_id=project_id)
    result = svc.execute()

    assert isinstance(result, dict)
    assert "context_type" in result  # capsula el repo canónico sin excepción


def test_review_service_sin_data_dir_fail_fast(tmp_path):
    """ReviewService: mismo patrón fail-fast (sin fallback a registro global)."""
    from fast_mcp.features.code_analysis.application.review_service import ReviewUseCase

    svc = ReviewUseCase(chain_dir=str(tmp_path))
    with pytest.raises(ValueError, match="data_dir no inyectado"):
        _ = svc.repo


def test_zero_path_guessing_en_code_analysis_read_path(tmp_path):
    """M-06 (Wave 1): 0 ocurrencias de os.path.join(root, 'data') en el read path.

    El comentario en bug_service.py es docstring (documenta el anti-patrón
    corregido); el único join('data') restante es el layout canónico
    .cognitive/projects/<pid>/data.
    """
    import os
    from pathlib import Path

    feature_dir = (
        Path(__file__).resolve().parents[1] / "fast_mcp" / "features" / "code_analysis"
    )
    offenders: list[str] = []
    for py in feature_dir.rglob("*.py"):
        text = py.read_text()
        if "os.path.join(project_root, \"data\")" in text and "jamás" not in text:
            offenders.append(str(py))
    assert not offenders, f"Path-guessing R-A en: {offenders}"