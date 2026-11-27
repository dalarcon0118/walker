"""test_fast_mcp_w5_tasks_cpg_integration_bdd.py — W5 (ADR-WALKER-076).

Integración del adapter CPG (ICpgAnalysisPort) en task handlers:
- implement_task: usa ctx.cpg_adapter si data incluye inspect_codebase_action.
- verify_predicates: usa ctx.cpg_adapter si data incluye inspect_codebase_action.
- capture: usa ctx.cpg_adapter si data incluye inspect_codebase_action.

Cobertura:
- Handler individual con adapter presente (mock).
- Handler individual sin adapter (noop, no rompe).
- Completitud de tasks registradas en TaskRegistry.
"""

from __future__ import annotations

from dataclasses import dataclass

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


# ── Tests implement_task ────────────────────────────────────────────────────


class TestImplementTaskCpgIntegration:
    """W5: implement_task con cpg_adapter."""

    def test_implement_con_cpg_adapter_ejecuta_action(self):
        from fast_mcp.features.tasks.handlers.implement import implement_task

        class FakeAdapter:
            def query(self, query=""):
                return {"query": query, "total": 1, "matches": [{"file": "x.py"}]}

        ctx = _task_ctx(cpg_adapter=FakeAdapter())
        data = {
            "objective": "Agregar feature X",
            "files": ["x.py"],
            "acceptance": ["la feature funciona"],
            "inspect_codebase_action": "query",
            "query": "function:main",
        }
        result = implement_task(ctx, data)
        assert result.ok is True
        assert "inspect_codebase" in result.data
        assert result.data["inspect_codebase"]["query"] == "function:main"

    def test_implement_sin_cpg_adapter_no_rompe(self):
        from fast_mcp.features.tasks.handlers.implement import implement_task

        ctx = _task_ctx(cpg_adapter=None)
        data = {"objective": "X", "files": ["x.py"], "acceptance": ["X"]}
        result = implement_task(ctx, data)
        assert result.ok is True
        assert "inspect_codebase" not in result.data

    def test_implement_con_action_desconocida_retorna_error(self):
        from fast_mcp.features.tasks.handlers.implement import implement_task

        class FakeAdapter:
            pass

        ctx = _task_ctx(cpg_adapter=FakeAdapter())
        data = {
            "objective": "X",
            "files": ["x.py"],
            "acceptance": ["X"],
            "inspect_codebase_action": "no_existe",
        }
        result = implement_task(ctx, data)
        assert result.ok is True
        assert result.data["inspect_codebase"]["error"] == "Unknown inspect_codebase action: no_existe"

    def test_implement_con_adapter_excepcion_no_rompe(self):
        from fast_mcp.features.tasks.handlers.implement import implement_task

        class FakeAdapter:
            def query(self, query=""):
                raise RuntimeError("boom")

        ctx = _task_ctx(cpg_adapter=FakeAdapter())
        data = {
            "objective": "X",
            "files": ["x.py"],
            "acceptance": ["X"],
            "inspect_codebase_action": "query",
            "query": "function:main",
        }
        result = implement_task(ctx, data)
        assert result.ok is True
        assert "inspect_codebase" in result.data
        assert "RuntimeError" in result.data["inspect_codebase"]["error"]


# ── Tests verify_predicates ─────────────────────────────────────────────────


class TestVerifyPredicatesCpgIntegration:
    """W5: verify_predicates con cpg_adapter."""

    def test_verify_con_cpg_adapter_ejecuta_action(self):
        from fast_mcp.features.tasks.handlers.verify import verify_predicates

        class FakeAdapter:
            def dataflow(self, file="", symbol=""):
                return {"action": "dataflow", "total": 0, "matches": []}

        ctx = _task_ctx(cpg_adapter=FakeAdapter())
        data = {
            "predicates": [
                {"type": "file_exists", "path": "x.py"},
            ],
            "inspect_codebase_action": "dataflow",
            "file": "x.py",
        }
        result = verify_predicates(ctx, data)
        assert result.ok is True
        assert "inspect_codebase" in result.data
        assert result.data["inspect_codebase"]["action"] == "dataflow"

    def test_verify_sin_cpg_adapter_no_rompe(self):
        from fast_mcp.features.tasks.handlers.verify import verify_predicates

        ctx = _task_ctx(cpg_adapter=None)
        data = {
            "predicates": [
                {"type": "file_exists", "path": "x.py"},
            ],
        }
        result = verify_predicates(ctx, data)
        assert result.ok is True
        assert "inspect_codebase" not in result.data

    def test_verify_sin_predicates_falla_igual(self):
        from fast_mcp.features.tasks.handlers.verify import verify_predicates

        ctx = _task_ctx(cpg_adapter=None)
        result = verify_predicates(ctx, {"predicates": []})
        assert result.ok is False
        assert "predicates vacíos" in result.error


# ── Tests capture ───────────────────────────────────────────────────────────


class TestCaptureCpgIntegration:
    """W5: capture con cpg_adapter."""

    def test_capture_con_cpg_adapter_ejecuta_action(self):
        from fast_mcp.features.tasks.handlers.capture import capture

        class FakeAdapter:
            def business_layer(self, focus=""):
                return {"action": "business", "pure_functions": []}

        ctx = _task_ctx(cpg_adapter=FakeAdapter())
        data = {
            "evidence": "fin",
            "artifacts": [],
            "inspect_codebase_action": "business_layer",
            "focus": "src",
        }
        result = capture(ctx, data)
        assert result.ok is True
        arts = result.data["artifacts"]
        assert len(arts) == 1
        assert arts[0]["type"] == "inspect_codebase"
        assert arts[0]["result"]["action"] == "business"

    def test_capture_sin_cpg_adapter_no_rompe(self):
        from fast_mcp.features.tasks.handlers.capture import capture

        ctx = _task_ctx(cpg_adapter=None)
        data = {"evidence": "fin", "artifacts": []}
        result = capture(ctx, data)
        assert result.ok is True
        assert result.data["artifacts"] == []


# ── Tests de completitud de tasks ───────────────────────────────────────────


class TestTaskRegistryCompleteness:
    """W5: EXPECTED_TASKS == set(tasks registradas por register_all_tasks)."""

    def test_completitud_tasks_w5(self):
        from fast_mcp.features.tasks.handlers import register_all_tasks
        from fast_mcp.features.tasks.domain.task_registry import TaskRegistry

        registry = TaskRegistry()
        register_all_tasks(registry, sprint_service=object())

        expected = {
            "implement_task",
            # Continuidad sesión→sprint (delta-sprint): decision auto|new|continue
            "session_bind",
            "session_open",
            "session_close",
            "session_list",
            # ADR-WALKER-078: transición de sprint (paused reversible)
            "sprint_status",
            # ADR-WALKER-080: plan de vuelo de la sesión
            "session_plan",
            # Detalle: iniciar una spec existente (roadmap)
            "spec_start",
            # Cierre de tarea con artefactos (salida accionable de close_sprint)
            "spec_status",
            "spec_predicates",
            "spec_update",
            "run_tests",
            "verify_predicates",
            "capture",
            "plan_sprint",
            "spec_task",
            "validate_plan",
            "close_sprint",
            "observe",
            "state_construction",
            "info_acquisition",
            "monitoring",
            # START.LIST (el nodo declaraba action=list_projects sin handler)
            "list_projects",
            # CPG bootstrap idempotente: project_init.SCAN_CODE / analyze.GATHER
            "sync_code_index",
            # Punto A / punto B del workflow (analyze.BASELINE, verify.BASELINE_DIFF)
            "capture_baseline",
            "diff_baseline",
            "capture_knowledge",
            # DESIGN / IMPLEMENT: acciones que el grafo declaraba sin handler
            "gather_context",
            "documenting_knowledge",
            "review_code",
            # REFINE / PROJECT_INIT / ADD_TASKS
            "refine_diagnose",
            "route_to_target",
            "escalate_refine",
            "detect_project_context",
            "build_project_config",
            "apply_project_config",
            "initialize_knowledge_base",
            "create_first_sprint",
            "add_tasks",
        }
        actual = set(registry.list_tasks().keys())
        missing = expected - actual
        extra = actual - expected
        assert not missing, f"Faltan tasks registradas: {missing}"
        assert not extra, f"Tasks extras no esperadas: {extra}"


# ── Tests de integración execute_public_task con cpg_adapter ────────────────


class TestExecutePublicTaskCpgAdapter:
    """W5: execute_public_task transporta cpg_adapter a ExecutionContext."""

    def test_execute_public_task_con_cpg_adapter(self):
        from fast_mcp.features.workflow.application.task_dispatcher import execute_public_task
        from fast_mcp.features.tasks.domain.task_registry import TaskRegistry

        class FakeAdapter:
            def gather_context(self, files=None):
                return {"files_analyzed": len(files or [])}

        registry = TaskRegistry()
        registry.register("implement_task", implement_task_stub)

        result = execute_public_task(
            name="implement_task",
            project_id="p-1",
            chain_dir="/tmp",
            task_data={"objective": "X", "files": ["a.py", "b.py"], "acceptance": ["X"], "inspect_codebase_action": "gather_context"},
            registry=registry,
            cpg_adapter=FakeAdapter(),
        )
        assert result["ok"] is True
        assert result["data"]["inspect_codebase"]["files_analyzed"] == 2

    def test_execute_public_task_sin_cpg_adapter_no_rompe(self):
        from fast_mcp.features.workflow.application.task_dispatcher import execute_public_task
        from fast_mcp.features.tasks.domain.task_registry import TaskRegistry

        registry = TaskRegistry()
        registry.register("implement_task", implement_task_stub)

        result = execute_public_task(
            name="implement_task",
            project_id="p-1",
            chain_dir="/tmp",
            task_data={"objective": "X", "files": ["a.py"], "acceptance": ["X"]},
            registry=registry,
        )
        assert result["ok"] is True
        assert "inspect_codebase" not in result["data"]


def implement_task_stub(ctx, data):
    from fast_mcp.features.tasks.handlers.implement import implement_task

    return implement_task(ctx, data)
