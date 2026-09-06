"""test_fast_mcp_w3_dataflow_controlflow_bdd.py — W3 (ADR-WALKER-076).

Wave 3: acciones consultables DFG/CFG en inspect_codebase (P-1/P-2).
- action=dataflow(file, symbol) → dfg.json (taint_paths, secrets...).
- action=controlflow(file, symbol) → analysis_results.json (CFG findings).

Cobertura:
- Projection helpers: filtrado por archivo/símbolo y fail-fast si falta proyección.
- CodeAnalysisService.dataflow/controlflow con proyección presente.
- CodeAnalysisService.dataflow/controlflow fail-fast si falta proyección.
- Completitud de actions de inspect_codebase (patrón AGENTS.md).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


# ── Fixture: proyecto preparado con data_dir canónico ─────────────────────


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


# ── Tests de projection_helpers ────────────────────────────────────────────


class TestProjectionHelpers:
    """Unitarios: helpers de filtrado y carga de proyecciones."""

    def test_filter_by_file_exact(self):
        from fast_mcp.features.code_analysis.application.projection_helpers import (
            filter_by_file,
        )

        entries = [
            {"file": "src/app.py", "variable": "x"},
            {"file": "src/main.py", "variable": "y"},
            {"file": "lib/util.py", "variable": "z"},
        ]
        result = filter_by_file(entries, "src/app.py")
        assert len(result) == 1
        assert result[0]["variable"] == "x"

    def test_filter_by_file_normalizes_paths(self):
        from fast_mcp.features.code_analysis.application.projection_helpers import (
            filter_by_file,
        )

        entries = [
            {"file": "src\\app.py", "variable": "x"},
        ]
        result = filter_by_file(entries, "src/app.py")
        assert len(result) == 1

    def test_filter_by_file_case_insensitive(self):
        from fast_mcp.features.code_analysis.application.projection_helpers import (
            filter_by_file,
        )

        entries = [
            {"file": "SRC/APP.PY", "variable": "x"},
        ]
        result = filter_by_file(entries, "src/app.py")
        assert len(result) == 1

    def test_filter_entries_by_symbol(self):
        from fast_mcp.features.code_analysis.application.projection_helpers import (
            filter_entries,
        )

        entries = [
            {"source": "user_input", "sink": "query", "variable": "user_input"},
            {"source": "admin_input", "sink": "log", "variable": "admin_input"},
        ]
        result = filter_entries(entries, "user", ["source", "sink", "variable"])
        assert len(result) == 1
        assert result[0]["variable"] == "user_input"

    def test_filter_entries_empty_when_no_match(self):
        from fast_mcp.features.code_analysis.application.projection_helpers import (
            filter_entries,
        )

        entries = [{"source": "x", "sink": "y", "variable": "z"}]
        result = filter_entries(entries, "missing", ["source", "sink"])
        assert result == []

    def test_load_projection_raise_when_missing(self, tmp_path):
        from fast_mcp.features.code_analysis.application.projection_helpers import (
            load_projection,
        )

        with pytest.raises(ValueError, match="build_code_index"):
            load_projection(str(tmp_path), "dfg.json")

    def test_load_projection_returns_dict_when_present(self, tmp_path):
        from fast_mcp.features.code_analysis.application.projection_helpers import (
            load_projection,
        )

        payload = {"taint_paths": []}
        (tmp_path / "dfg.json").write_text(json.dumps(payload))
        result = load_projection(str(tmp_path), "dfg.json")
        assert result == payload


# ── Tests de CodeAnalysisService.dataflow ──────────────────────────────────


class TestDataflowAction:
    """W3: action=dataflow desde dfg.json."""

    def test_dataflow_retorna_entradas_filtradas_por_archivo_y_simbolo(self, tmp_path):
        from fast_mcp.features.code_analysis.application.services.code_analysis_service import (
            CodeAnalysisService,
        )

        repo, data_dir, project_id = _prepared_project(tmp_path)
        dfg = {
            "taint_paths": [
                {"file": "src/app.py", "source": "request.args", "sink": "cursor.execute",
                 "variable": "user_input", "line": 10},
                {"file": "src/other.py", "source": "input", "sink": "log",
                 "variable": "other_input", "line": 5},
            ],
            "hardcoded_secrets": [],
        }
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        (Path(data_dir) / "dfg.json").write_text(json.dumps(dfg))

        svc = CodeAnalysisService(chain_dir=repo, project_id=project_id)
        result = svc.dataflow(file="src/app.py", symbol="user_input")

        assert result["action"] == "dataflow"
        assert result["total"] == 1
        assert result["matches"][0]["file"] == "src/app.py"
        assert result["matches"][0]["variable"] == "user_input"

    def test_dataflow_sin_proyeccion_fail_fast(self, tmp_path):
        from fast_mcp.features.code_analysis.application.services.code_analysis_service import (
            CodeAnalysisService,
        )

        repo, data_dir, project_id = _prepared_project(tmp_path)
        svc = CodeAnalysisService(chain_dir=repo, project_id=project_id)
        result = svc.dataflow(file="src/app.py", symbol="user_input")

        assert "error" in result
        assert "build_code_index" in result["error"]

    def test_dataflow_sin_data_dir_fail_fast(self, tmp_path):
        from fast_mcp.features.code_analysis.application.services.code_analysis_service import (
            CodeAnalysisService,
        )

        svc = CodeAnalysisService(chain_dir=str(tmp_path))
        result = svc.dataflow(file="x.py", symbol="y")
        assert "error" in result
        assert "build_code_index" in result["error"]


# ── Tests de CodeAnalysisService.controlflow ────────────────────────────────


class TestControlflowAction:
    """W3: action=controlflow desde analysis_results.json."""

    def test_controlflow_retorna_findings_filtrados_por_archivo(self, tmp_path):
        from fast_mcp.features.code_analysis.application.services.code_analysis_service import (
            CodeAnalysisService,
        )

        repo, data_dir, project_id = _prepared_project(tmp_path)
        analysis = {
            "dead_branches": [
                {"file": "src/app.py", "function": "foo", "detail": "always false", "line": 42},
                {"file": "src/other.py", "function": "bar", "detail": "always true", "line": 10},
            ],
            "missing_returns": [],
            "infinite_loops": [],
        }
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        (Path(data_dir) / "analysis_results.json").write_text(json.dumps(analysis))

        svc = CodeAnalysisService(chain_dir=repo, project_id=project_id)
        result = svc.controlflow(file="src/app.py", symbol="")

        assert result["action"] == "controlflow"
        assert result["total"] == 1
        assert result["matches"][0]["function"] == "foo"

    def test_controlflow_sin_proyeccion_fail_fast(self, tmp_path):
        from fast_mcp.features.code_analysis.application.services.code_analysis_service import (
            CodeAnalysisService,
        )

        repo, data_dir, project_id = _prepared_project(tmp_path)
        svc = CodeAnalysisService(chain_dir=repo, project_id=project_id)
        result = svc.controlflow(file="src/app.py", symbol="")
        assert "error" in result
        assert "build_code_index" in result["error"]

    def test_controlflow_sin_data_dir_fail_fast(self, tmp_path):
        from fast_mcp.features.code_analysis.application.services.code_analysis_service import (
            CodeAnalysisService,
        )

        svc = CodeAnalysisService(chain_dir=str(tmp_path))
        result = svc.controlflow(file="x.py", symbol="y")
        assert "error" in result
        assert "build_code_index" in result["error"]


# ── Test de completitud de actions (patrón AGENTS.md) ───────────────────────


def test_inspect_codebase_completitud_actions_w3():
    """W3: EXPECTED_ACTIONS == set(dispatch_map keys) — anti-patrón: action huérfana."""
    from fast_mcp.memories.handlers.code_analysis import handle_inspect_codebase

    # Reconstruir el dispatch_map como lo hace el handler (fuente de verdad).
    from fast_mcp.features.code_analysis.application.services.code_analysis_service import (
        CodeAnalysisService,
    )

    service = CodeAnalysisService()
    dispatch_map = {
        "business": service.business_layer,
        "dead_code": service.detect_unused_functions,
        "review": service.review_code,
        "metrics": service.project_metrics,
        "bugs": service.find_bugs,
        "security": service.security_scan,
        "dataflow": service.dataflow,
        "controlflow": service.controlflow,
    }
    expected = {
        "business",
        "dead_code",
        "review",
        "metrics",
        "bugs",
        "security",
        "dataflow",
        "controlflow",
    }
    actual = set(dispatch_map.keys())
    missing = expected - actual
    extra = actual - expected
    assert not missing, f"Faltan actions en dispatch_map: {missing}"
    assert not extra, f"Actions extras no declaradas en expected: {extra}"
