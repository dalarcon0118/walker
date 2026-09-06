"""test_fast_mcp_w8_w9_health_and_po_decision_bdd.py — W8/W9 (ADR-WALKER-076).

W8: acción `health` en `inspect_codebase` para verificar el estado del CPG
(node count, status). Consume `CheckCpgHealthUseCase.execute(data_dir)`.

W9: decisión PO ejecutada: borrar `infrastructure/sg_adapter.py` (legacy sin
importadores; la vía viva es review_code→deep.py y conventions→ast-grep).

Cobertura:
- CodeAnalysisService.check_health con data_dir presente.
- CodeAnalysisService.check_health fail-fast si data_dir no resuelto.
- handle_inspect_codebase action=health.
- WalkerCpgAnalysisAdapter.check_health.
- Verificación de que sg_adapter.py fue borrado (W9 PO ejecutada).
"""

from __future__ import annotations

import pytest


# ── Helpers ────────────────────────────────────────────────────────────────


def _prepared_project(tmp_path):
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


# ── Tests W8: action=health ─────────────────────────────────────────────────


class TestCheckHealthService:
    """W8: check_health en CodeAnalysisService."""

    def test_check_health_sin_cpg_devuelve_not_scanned(self, tmp_path):
        from fast_mcp.features.code_analysis.application.services.code_analysis_service import (
            CodeAnalysisService,
        )

        repo, data_dir, project_id = _prepared_project(tmp_path)
        svc = CodeAnalysisService(chain_dir=repo, project_id=project_id)
        result = svc.check_health()
        assert "status" in result
        assert "node_count" in result
        assert result["node_count"] == 0
        assert result["status"] == "not scanned"

    def test_check_health_sin_data_dir_fail_fast(self, tmp_path):
        from fast_mcp.features.code_analysis.application.services.code_analysis_service import (
            CodeAnalysisService,
        )

        svc = CodeAnalysisService(chain_dir=str(tmp_path))
        result = svc.check_health()
        assert "error" in result
        assert "data_dir no resuelto" in result["error"]


class TestInspectCodebaseHealthAction:
    """W8: inspect_codebase action=health en el handler MCP."""

    def test_handler_health_sin_cpg(self, tmp_path):
        from fast_mcp.memories.handlers.code_analysis import handle_inspect_codebase
        from fast_mcp.features.code_analysis.application.services.code_analysis_service import (
            CodeAnalysisService,
        )

        repo, data_dir, project_id = _prepared_project(tmp_path)
        svc = CodeAnalysisService(chain_dir=repo, project_id=project_id)

        class FakeCtx:
            def code_analysis_service(self):
                return svc

            chain_dir = repo

        result = handle_inspect_codebase(FakeCtx(), action="health")
        assert result["action"] == "health"
        assert "result" in result
        assert result["result"]["node_count"] == 0
        assert result["result"]["status"] == "not scanned"


class TestAdapterCheckHealth:
    """W8: WalkerCpgAnalysisAdapter.check_health."""

    def test_adapter_health_sin_cpg(self, tmp_path):
        from fast_mcp.features.code_analysis.infrastructure.adapters.walker_cpg_adapter import (
            WalkerCpgAnalysisAdapter,
        )
        from fast_mcp.features.code_analysis.infrastructure.cognitive_cpg_repo import (
            CognitiveCpgRepository,
        )
        from fast_mcp.features.code_analysis.application.services.code_analysis_service import (
            CodeAnalysisService,
        )

        pid = "p-health"
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        repo = CognitiveCpgRepository(str(data_dir), pid)
        svc = CodeAnalysisService(chain_dir=str(tmp_path), project_id=pid)
        adapter = WalkerCpgAnalysisAdapter(
            repo=repo,
            analyzer=None,
            event_bus=None,
            service=svc,
        )
        # chain_dir=/tmp no es un proyecto válido: fail-fast con error accionable.
        result = adapter.check_health()
        assert "error" in result
        assert "data_dir no resuelto" in result["error"]


# ── Tests W9: decisión PO ejecutada (sg_adapter borrado) ────────────────────


class TestW9PoDecisionExecuted:
    """W9: verificar que la decisión PO se ejecutó (sg_adapter.py borrado)."""

    def test_sg_adapter_borrado(self):
        """W9: sg_adapter.py no debe existir en infrastructure/."""
        import os

        path = "fast_mcp/features/code_analysis/infrastructure/sg_adapter.py"
        assert not os.path.exists(path), f"sg_adapter.py sigue existiendo; W9 PO no ejecutada: {path}"

    def test_no_imports_de_sg_adapter_en_repo(self):
        """W9: no debe haber imports de sg_adapter en el código (solo referencias en comentarios históricos permitidas)."""
        import os

        hits = []
        for root, dirs, files in os.walk("fast_mcp"):
            dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git")]
            for f in files:
                if not f.endswith(".py"):
                    continue
                path = os.path.join(root, f)
                with open(path, "r", encoding="utf-8") as fh:
                    lines = fh.readlines()
                for line in lines:
                    stripped = line.strip()
                    if stripped.startswith("#"):
                        continue
                    if "sg_adapter" in line:
                        hits.append(path)
                        break
        assert not hits, f"Imports activos de sg_adapter encontrados en: {hits}"

    def test_no_reference_docs_solo_adr076(self):
        """W9: las referencias a sg_adapter en docs deben ser solo el ADR-076 (histórico)."""
        import os

        hits = []
        for root, dirs, files in os.walk("docs"):
            for f in files:
                if not f.endswith(".md"):
                    continue
                path = os.path.join(root, f)
                with open(path, "r", encoding="utf-8") as fh:
                    content = fh.read()
                if "sg_adapter" in content and "ADR-WALKER-076" not in path:
                    hits.append(path)
        assert not hits, f"Referencias a sg_adapter fuera de ADR-076: {hits}"


# ── Completitud de actions ──────────────────────────────────────────────────


def test_inspect_codebase_completitud_actions_w8():
    """W8: EXPECTED_ACTIONS == set(dispatch_map keys) — 11 actions."""
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
        "query": service.query,
        "conventions": service.check_conventions,
        "health": service.check_health,
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
        "query",
        "conventions",
        "health",
    }
    actual = set(dispatch_map.keys())
    missing = expected - actual
    extra = actual - expected
    assert not missing, f"Faltan actions en dispatch_map: {missing}"
    assert not extra, f"Actions extras no declaradas en expected: {extra}"
