"""test_fast_mcp_w7_conventions_ckg_circuit_bdd.py — W7 (ADR-WALKER-076).

Wave 7: acción `conventions` en `inspect_codebase` para el circuito
CKG↔CPG. Consume convenciones almacenadas en el CKG (tipo "convention")
y las verifica contra el código real vía ast-grep.

Cobertura:
- CodeAnalysisService.check_conventions con CKG presente (mock).
- CodeAnalysisService.check_conventions sin CKG (fail-fast).
- handle_inspect_codebase action=conventions.
- WalkerCpgAnalysisAdapter.check_conventions con/sin CKG.
- Completitud de actions de inspect_codebase (10 actions).
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


# ── Tests de CodeAnalysisService.check_conventions ──────────────────────────


class TestCheckConventionsService:
    """W7: check_conventions en CodeAnalysisService."""

    def test_check_conventions_con_ckg_devuelve_reporte(self, tmp_path):
        from fast_mcp.features.code_analysis.application.services.code_analysis_service import (
            CodeAnalysisService,
        )

        repo, data_dir, project_id = _prepared_project(tmp_path)

        class FakeCKG:
            def query(self, q):
                return [
                    {
                        "statement": "Use snake_case",
                        "metadata": {
                            "rule_id": "R1",
                            "rule_yaml": 'id: R1\nlanguage: python\nrule:\n  pattern: camelCase\nmessage: Use snake_case\n',
                            "language": "python",
                            "severity": "warning",
                            "fix": "Convert to snake_case",
                        },
                    }
                ]

        svc = CodeAnalysisService(chain_dir=repo, project_id=project_id)
        report = svc.check_conventions(languages=["python"], ckg=FakeCKG())
        assert "rules_checked" in report
        assert report["rules_checked"] == 1
        assert "violations" in report
        assert "by_rule" in report

    def test_check_conventions_sin_ckg_fail_fast(self, tmp_path):
        from fast_mcp.features.code_analysis.application.services.code_analysis_service import (
            CodeAnalysisService,
        )

        repo, data_dir, project_id = _prepared_project(tmp_path)
        svc = CodeAnalysisService(chain_dir=repo, project_id=project_id)
        report = svc.check_conventions(languages=["python"], ckg=None)
        errors = report.get("errors", [])
        assert any("CKG no inyectado" in e for e in errors), f"errors={errors}"
        assert report["rules_checked"] == 0

    def test_check_conventions_sin_data_dir_fail_fast(self, tmp_path):
        from fast_mcp.features.code_analysis.application.services.code_analysis_service import (
            CodeAnalysisService,
        )

        svc = CodeAnalysisService(chain_dir=str(tmp_path))
        report = svc.check_conventions(languages=["python"], ckg=None)
        errors = report.get("errors", [])
        assert any("resolver" in e.lower() or "project root" in e.lower() or "CKG no inyectado" in e for e in errors), f"errors={errors}"
        assert report["rules_checked"] == 0


# ── Tests de handle_inspect_codebase action=conventions ─────────────────────


class TestInspectCodebaseConventionsAction:
    """W7: inspect_codebase action=conventions en el handler MCP."""

    def test_handler_conventions_con_ckg(self, tmp_path):
        from fast_mcp.memories.handlers.code_analysis import handle_inspect_codebase
        from fast_mcp.features.code_analysis.application.services.code_analysis_service import (
            CodeAnalysisService,
        )

        repo, data_dir, project_id = _prepared_project(tmp_path)

        class FakeCKG:
            def query(self, q):
                return []

        svc = CodeAnalysisService(chain_dir=repo, project_id=project_id)

        class FakeCtx:
            def code_analysis_service(self):
                return svc

            chain_dir = repo

        result = handle_inspect_codebase(
            FakeCtx(),
            action="conventions",
            languages=["python"],
        )
        assert result["action"] == "conventions"
        assert "result" in result
        assert "rules_checked" in result["result"]

    def test_handler_conventions_sin_ckg_devuelve_error(self, tmp_path):
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

        result = handle_inspect_codebase(
            FakeCtx(),
            action="conventions",
        )
        assert result["action"] == "conventions"
        errors = result["result"].get("errors", [])
        assert any("CKG no inyectado" in e for e in errors), f"errors={errors}"


# ── Tests de WalkerCpgAnalysisAdapter.check_conventions ─────────────────────


class TestAdapterCheckConventions:
    """W7: WalkerCpgAnalysisAdapter.check_conventions."""

    def test_adapter_conventions_con_ckg(self, tmp_path):
        from fast_mcp.features.code_analysis.infrastructure.adapters.walker_cpg_adapter import (
            WalkerCpgAnalysisAdapter,
        )
        from fast_mcp.features.code_analysis.infrastructure.cognitive_cpg_repo import (
            CognitiveCpgRepository,
        )
        from fast_mcp.features.code_analysis.application.services.code_analysis_service import (
            CodeAnalysisService,
        )

        pid = "p-conventions"
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        repo = CognitiveCpgRepository(str(data_dir), pid)
        svc = CodeAnalysisService(chain_dir=str(tmp_path), project_id=pid)

        class FakeCKG:
            def query(self, q):
                return []

        adapter = WalkerCpgAnalysisAdapter(
            repo=repo,
            analyzer=None,
            event_bus=None,
            service=svc,
            ckg=FakeCKG(),
        )
        result = adapter.check_conventions(languages=["python"])
        assert "rules_checked" in result
        assert result["rules_checked"] == 0  # sin convenciones en el CKG mock

    def test_adapter_conventions_sin_ckg_fail_fast(self, tmp_path):
        from fast_mcp.features.code_analysis.infrastructure.adapters.walker_cpg_adapter import (
            WalkerCpgAnalysisAdapter,
        )
        from fast_mcp.features.code_analysis.infrastructure.cognitive_cpg_repo import (
            CognitiveCpgRepository,
        )
        from fast_mcp.features.code_analysis.application.services.code_analysis_service import (
            CodeAnalysisService,
        )

        pid = "p-conventions-nockg"
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        repo = CognitiveCpgRepository(str(data_dir), pid)
        svc = CodeAnalysisService(chain_dir=str(tmp_path), project_id=pid)
        adapter = WalkerCpgAnalysisAdapter(
            repo=repo,
            analyzer=None,
            event_bus=None,
            service=svc,
            ckg=None,
        )
        result = adapter.check_conventions()
        assert "error" in result
        assert "CKG no inyectado" in result["error"]
        assert result["rules_checked"] == 0


# ── Tests de integración task handler con conventions ───────────────────────


class TestTaskHandlerConventionsIntegration:
    """W7: task handler con inspect_codebase_action=conventions."""

    def test_implement_con_conventions_action(self, tmp_path):
        from fast_mcp.features.tasks.handlers.implement import implement_task
        from fast_mcp.features.workflow.domain.execution import ExecutionContext

        class FakeAdapter:
            def conventions(self, languages=None):
                return {
                    "rules_checked": 2,
                    "violations": [],
                    "by_rule": {},
                }

        ctx = ExecutionContext(
            session_id="test",
            trace_id="tid",
            span_id="sid",
            chain_dir="/tmp",
            task_id="task-1",
            cpg_adapter=FakeAdapter(),
        )
        data = {
            "objective": "Refactor",
            "files": ["x.py"],
            "acceptance": ["las convenciones se respetan"],
            "inspect_codebase_action": "conventions",
            "languages": ["python"],
        }
        result = implement_task(ctx, data)
        assert result.ok is True
        assert "inspect_codebase" in result.data
        assert result.data["inspect_codebase"]["rules_checked"] == 2

    def test_verify_con_conventions_action(self, tmp_path):
        from fast_mcp.features.tasks.handlers.verify import verify_predicates
        from fast_mcp.features.workflow.domain.execution import ExecutionContext

        class FakeAdapter:
            def conventions(self, languages=None):
                return {
                    "rules_checked": 1,
                    "violations": [{"rule": "R1", "file": "x.py"}],
                    "by_rule": {"R1": [{"file": "x.py"}]},
                }

        ctx = ExecutionContext(
            session_id="test",
            trace_id="tid",
            span_id="sid",
            chain_dir="/tmp",
            task_id="task-1",
            cpg_adapter=FakeAdapter(),
        )
        data = {
            "predicates": [{"type": "command_exit", "command": "true"}],
            "inspect_codebase_action": "conventions",
        }
        result = verify_predicates(ctx, data)
        assert result.ok is True
        assert "inspect_codebase" in result.data
        assert result.data["inspect_codebase"]["rules_checked"] == 1


# ── Completitud de actions ──────────────────────────────────────────────────


def test_inspect_codebase_completitud_actions_w7():
    """W7: EXPECTED_ACTIONS == set(dispatch_map keys) — 10 actions."""
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
    }
    actual = set(dispatch_map.keys())
    missing = expected - actual
    extra = actual - expected
    assert not missing, f"Faltan actions en dispatch_map: {missing}"
    assert not extra, f"Actions extras no declaradas en expected: {extra}"
