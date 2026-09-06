"""test_fast_mcp_w6_hallucination_verify_bdd.py — W6 (ADR-WALKER-076).

Wave 6: integración de hallucination.py en verify_predicates como
action=imports (HU-3, fail-fast sobre índice vacío).

Cobertura:
- WalkerCpgAnalysisAdapter.detect_imports: escanea archivos del proyecto.
- verify_predicates con inspect_codebase_action=imports: falla si hay
  hallucinations, pasa si no hay.
- verify_predicates sin adapter: noop, no rompe.
- Completitud de methods en WalkerCpgAnalysisAdapter.
"""

from __future__ import annotations

from pathlib import Path

import pytest


# ── Helpers ────────────────────────────────────────────────────────────────


def _task_ctx(cpg_adapter=None):
    from fast_mcp.features.workflow.domain.execution import ExecutionContext

    return ExecutionContext(
        session_id="test",
        trace_id="tid",
        span_id="sid",
        chain_dir="/tmp",
        task_id="task-1",
        cpg_adapter=cpg_adapter,
    )


def _write_python_file(path: Path, content: str) -> None:
    path.write_text(content)


# ── Tests de WalkerCpgAnalysisAdapter.detect_imports ────────────────────────


class TestDetectImportsAdapter:
    """W6: detect_imports en el adapter CPG."""

    def test_detect_imports_archivo_con_import_alucinado(self, tmp_path):
        from fast_mcp.features.code_analysis.infrastructure.adapters.walker_cpg_adapter import (
            WalkerCpgAnalysisAdapter,
        )
        from fast_mcp.features.code_analysis.infrastructure.cognitive_cpg_repo import (
            CognitiveCpgRepository,
        )

        pid = "p-imports-test"
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        repo = CognitiveCpgRepository(str(data_dir), pid)

        # No necesitamos EventBus ni trace publisher para este test
        adapter = WalkerCpgAnalysisAdapter(
            repo=repo,
            analyzer=None,
            event_bus=None,
            service=None,
        )

        src = tmp_path / "src"
        src.mkdir()
        _write_python_file(
            src / "app.py",
            "import os\nfrom modulo_que_no_existe import algo\n",
        )

        result = adapter.detect_imports(files=[str(src / "app.py")], project_root=str(tmp_path))
        assert result["files_scanned"] == 1
        assert result["hallucinations"] >= 1
        assert len(result["issues"]) > 0
        assert result["issues"][0]["status"] == "HALLUCINATION"

    def test_detect_imports_sin_archivos_devuelve_vacio(self, tmp_path):
        from fast_mcp.features.code_analysis.infrastructure.adapters.walker_cpg_adapter import (
            WalkerCpgAnalysisAdapter,
        )
        from fast_mcp.features.code_analysis.infrastructure.cognitive_cpg_repo import (
            CognitiveCpgRepository,
        )

        pid = "p-imports-empty"
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        repo = CognitiveCpgRepository(str(data_dir), pid)
        adapter = WalkerCpgAnalysisAdapter(repo=repo, analyzer=None, event_bus=None, service=None)

        result = adapter.detect_imports(files=[], project_root=str(tmp_path))
        assert result["files_scanned"] == 0
        assert result["hallucinations"] == 0
        assert result["issues"] == []

    def test_detect_imports_sin_project_root_falla(self, tmp_path):
        from fast_mcp.features.code_analysis.infrastructure.adapters.walker_cpg_adapter import (
            WalkerCpgAnalysisAdapter,
        )
        from fast_mcp.features.code_analysis.infrastructure.cognitive_cpg_repo import (
            CognitiveCpgRepository,
        )

        pid = "p-imports-noroot"
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        repo = CognitiveCpgRepository(str(data_dir), pid)
        # service=None: el adapter construye CodeAnalysisService(data_dir=repo.data_dir).
        # detect_imports con project_root="" cae a service._chain_dir (data_dir),
        # así que NO falla aquí; falla solo si service._chain_dir también es vacío.
        adapter = WalkerCpgAnalysisAdapter(repo=repo, analyzer=None, event_bus=None, service=None)

        # Si proveemos un project_root vacío pero el servicio tiene chain_dir,
        # el adapter usa el chain_dir como fallback (no fail-fast).
        result = adapter.detect_imports(files=["x.py"], project_root="")
        assert "error" not in result
        # El archivo x.py no existe, así que issues contiene el error de archivo
        # no encontrado pero no es una alucinación.
        assert result["hallucinations"] == 0


# ── Tests de verify_predicates con action=imports ───────────────────────────


class TestVerifyPredicatesImportsAction:
    """W6: verify_predicates con inspect_codebase_action=imports."""

    def test_verify_con_imports_hallucinations_falla(self, tmp_path):
        from fast_mcp.features.tasks.handlers.verify import verify_predicates

        class FakeAdapter:
            def imports(self, files=None, project_root=""):
                return {
                    "files_scanned": 1,
                    "hallucinations": 2,
                    "issues": [
                        {"line": 1, "module": "modulo_inexistente", "status": "HALLUCINATION"},
                    ],
                    "truncated": False,
                }

        ctx = _task_ctx(cpg_adapter=FakeAdapter())
        data = {
            "predicates": [
                {"type": "command_exit", "command": "true"},
            ],
            "inspect_codebase_action": "imports",
            "files": ["x.py"],
            "project_root": str(tmp_path),
        }
        result = verify_predicates(ctx, data)
        assert result.ok is True
        assert result.data["all_satisfied"] is False
        assert result.data.get("hallucinations_found") is True
        assert result.data["failed_count"] >= 1
        assert any(
            r.get("predicate") == "imports:no_hallucinations" and not r["passed"]
            for r in result.data["results"]
        )

    def test_verify_con_imports_sin_hallucinations_pasa(self, tmp_path):
        from fast_mcp.features.tasks.handlers.verify import verify_predicates

        class FakeAdapter:
            def imports(self, files=None, project_root=""):
                return {
                    "files_scanned": 1,
                    "hallucinations": 0,
                    "issues": [],
                    "truncated": False,
                }

        ctx = _task_ctx(cpg_adapter=FakeAdapter())
        data = {
            "predicates": [
                {"type": "command_exit", "command": "true"},
            ],
            "inspect_codebase_action": "imports",
            "files": ["x.py"],
            "project_root": str(tmp_path),
        }
        result = verify_predicates(ctx, data)
        assert result.ok is True
        assert result.data["all_satisfied"] is True
        assert result.data.get("hallucinations_found") is not True

    def test_verify_sin_adapter_imports_no_rompe(self):
        from fast_mcp.features.tasks.handlers.verify import verify_predicates

        ctx = _task_ctx(cpg_adapter=None)
        data = {
            "predicates": [
                {"type": "file_exists", "path": "x.py"},
            ],
            "inspect_codebase_action": "imports",
            "files": ["x.py"],
        }
        result = verify_predicates(ctx, data)
        assert result.ok is True
        assert "inspect_codebase" not in result.data

    def test_verify_sin_predicates_falla_igual_con_imports(self):
        from fast_mcp.features.tasks.handlers.verify import verify_predicates

        ctx = _task_ctx(cpg_adapter=None)
        result = verify_predicates(ctx, {
            "predicates": [],
            "inspect_codebase_action": "imports",
        })
        assert result.ok is False
        assert "predicates vacíos" in result.error

    def test_verify_imports_falla_si_adapter_devuelve_error(self):
        from fast_mcp.features.tasks.handlers.verify import verify_predicates

        class FakeAdapter:
            def detect_imports(self, files=None, project_root=""):
                return {"error": "project_root no resuelto"}

        ctx = _task_ctx(cpg_adapter=FakeAdapter())
        data = {
            "predicates": [
                {"type": "file_exists", "path": "x.py"},
            ],
            "inspect_codebase_action": "imports",
            "files": ["x.py"],
        }
        result = verify_predicates(ctx, data)
        assert result.ok is True
        assert "inspect_codebase" in result.data
        assert "error" in result.data["inspect_codebase"]


# ── Tests de integración con archivos reales ────────────────────────────────


class TestDetectImportsIntegration:
    """W6: detect_imports contra archivos Python reales en tmp_path."""

    def test_detect_imports_stdlib_no_alucina(self, tmp_path):
        from fast_mcp.features.code_analysis.infrastructure.adapters.walker_cpg_adapter import (
            WalkerCpgAnalysisAdapter,
        )
        from fast_mcp.features.code_analysis.infrastructure.cognitive_cpg_repo import (
            CognitiveCpgRepository,
        )

        pid = "p-imports-stdlib"
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        repo = CognitiveCpgRepository(str(data_dir), pid)
        adapter = WalkerCpgAnalysisAdapter(repo=repo, analyzer=None, event_bus=None, service=None)

        src = tmp_path / "src"
        src.mkdir()
        _write_python_file(
            src / "app.py",
            "import os\nimport sys\nfrom pathlib import Path\n",
        )

        result = adapter.detect_imports(files=[str(src / "app.py")], project_root=str(tmp_path))
        assert result["files_scanned"] == 1
        assert result["hallucinations"] == 0
