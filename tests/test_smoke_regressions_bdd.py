"""tests/test_smoke_regressions_bdd.py — Regresión de bugs descubiertos por el
smoke Capa 1 (test_smoke_mcp_workflow_bdd.py).

Bugs cubiertos:
  1. plan_sprint escribía el delta en cog/data/data/delta.json.gz (doble
     'data') en vez de cog/data/delta.json.gz — dos CognitiveGraphs distintos;
     las specs eran invisibles para spring_service(chain_dir).
  2. plan_sprint creaba el sprint en status="planning" → find_active_sprint
     (resuelve el chain_dir del submit MCP) devolvía None → 404 en submits
     sucesivos. El sprint del proyecto está activo desde su planificación.
  3. UpdateSprintStatusUseCase importaba SprintStatusUpdater de un namespace
     package inexistente (ImportError) → el side effect update_sprint_status
     fallaba con None silencioso (side_effects.py:499) → el workflow no
     avanzaba tras PLAN_APPROVAL.
  4. SprintStatusUpdater.can_transition rechazaba la transición idempotente
     (active→active) — combinado con el fix #2, PLAN_APPROVAL fallaba.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from features.delta_tracking import SpringService, spring_service
from tests.conftest import register_project_in_global_registry


@pytest.fixture
def _smoke_project_registry(tmp_path):
    """Registra walker→tmp en el registro global y restaura al final."""
    tmp = str(tmp_path)
    reg_ctx = register_project_in_global_registry(tmp).__enter__()
    try:
        yield tmp
    finally:
        reg_ctx.__exit__(None, None, None)
        from shared.core.engine.bootstrap import reset_module_registry

        reset_module_registry()
        from features.main.cli_interface import mcp_adapter

        mcp_adapter._SERVER = None


def _setup_project(tmp_path: str) -> tuple[str, str]:
    """Proyecto cognitivo + spring activo + workflow state PLANNING.

    NOTA: el registro global se registra/restaura en el TEST (via
    register_project_in_global_registry), no aquí — para no contaminar
    el registro real entre corridas.
    """
    cog = Path(tmp_path) / ".cognitive" / "projects" / "walker"
    cog.mkdir(parents=True)
    (cog / "data").mkdir()
    (cog / "data" / "project.json").write_text(json.dumps({
        "project_id": "walker", "name": "walker", "repo_path": tmp_path,
        "language": "python", "framework": "unknown", "status": "active",
    }))
    from features.delta_tracking.infrastructure.cognitive_delta_repo import (
        CognitiveDeltaRepository,
    )

    CognitiveDeltaRepository(str(cog))
    svc = SpringService(str(cog))
    svc.create_spring(
        "sprint-smoke", goal="Smoke sprint", status="active",
        project_id="walker", session_id="s1",
    )
    sprint_dir = cog / "data" / "spring" / "sprint-smoke"
    sprint_dir.mkdir(parents=True, exist_ok=True)
    from shared.core.engine.infrastructure.repositories import WorkflowStateRepository
    from shared.core.engine.domain.entities import WorkflowState

    wf = WorkflowState(current_node="SPRINT", current_sub_state="PLANNING")
    wf.context["chain_dir"] = str(sprint_dir)
    wf.context["project_id"] = "walker"
    wf.context["session_id"] = "s1"
    WorkflowStateRepository().save(str(sprint_dir), wf)
    return str(sprint_dir), str(cog)


class TestSmokeRegressions:
    """Bugs que el smoke descubrió y que deben quedar fijos."""

    def test_plan_sprint_escribe_en_el_delta_canonico(self, tmp_path, _smoke_project_registry):
        """plan_sprint escribe en cog/data/delta.json.gz (NO data/data/)."""
        from features.delta_tracking.application.use_cases_sprint import (
            PlanSprintUseCase,
        )
        from shared.common.shared_domain import ExecutionContext

        td = str(tmp_path)
        sprint_dir, cog_dir = _setup_project(td)
        ctx = ExecutionContext(
            chain_dir=sprint_dir, task_id="T1", session_id="s1",
            trace_id="t1", started_at="2026-01-01", span_id="sp1",
        )
        data = {
            "_project_id": "walker",
            "goal": "Implementar modulo completo de auth",
            "tasks": [{"objective": "Login", "type": "feature", "domain": "frontend"}],
        }
        result = PlanSprintUseCase.execute(ctx, data)
        assert result.get("status") == "planned", result

        # Las specs deben leerse desde el delta canónico (no data/data/)
        svc = spring_service(chain_dir=sprint_dir)
        assert svc is not None
        specs = svc.get_specs("sprint-smoke")
        assert specs, f"specs invisibles: {len(specs)}"

        # No debe existir cog/data/data/delta.json.gz (doble segmento)
        cog_path = Path(cog_dir)
        assert not (cog_path / "data" / "data" / "delta.json.gz").exists(), (
            "plan_sprint escribió en data/data/ (delta duplicado)"
        )

    def test_plan_sprint_mantiene_sprint_activo(self, tmp_path, _smoke_project_registry):
        """plan_sprint NO degrada el sprint: sigue activo para find_active_sprint."""
        from features.delta_tracking.application.use_cases_sprint import (
            PlanSprintUseCase,
        )
        from shared.common.shared_domain import ExecutionContext

        td = str(tmp_path)
        sprint_dir, cog_dir = _setup_project(td)
        ctx = ExecutionContext(
            chain_dir=sprint_dir, task_id="T1", session_id="s1",
            trace_id="t1", started_at="2026-01-01", span_id="sp1",
        )
        PlanSprintUseCase.execute(ctx, {
            "_project_id": "walker",
            "goal": "Implementar modulo completo de auth",
            "tasks": [{"objective": "Login", "type": "feature", "domain": "frontend"}],
        })

        svc = SpringService(cog_dir)
        spring = svc.get_spring("sprint-smoke")
        assert spring is not None
        assert spring.get("status") == "active", (
            f"plan_sprint degradó el sprint a {spring.get('status')} — "
            "find_active_sprint del submit MCP fallaría con 404"
        )

    def test_update_sprint_status_side_effect_no_import_error(self, tmp_path, _smoke_project_registry):
        """El side effect update_sprint_status ejecuta sin ImportError."""
        from shared.core.engine.domain.models import ExecutionContext
        from features.main.task_execution.side_effects.status import update_sprint_status

        td = str(tmp_path)
        sprint_dir, _ = _setup_project(td)
        ctx = ExecutionContext(
            chain_dir=sprint_dir, task_id="T1", session_id="s1",
            trace_id="t1", started_at="2026-01-01", span_id="sp1",
        )
        result = update_sprint_status(ctx, {"status": "active"})
        # ANTES: ImportError SprintStatusUpdater → None silencioso
        assert result is not None, "update_sprint_status devolvió None"
        assert result.get("ok") is True, f"update_sprint_status falló: {result}"

    def test_sprint_status_updater_idempotente(self):
        """can_transition(active, active) = OK (no-op exitoso)."""
        from features.delta_tracking.domain.services.spec_service import (
            SprintStatusUpdater,
        )

        allowed, reason = SprintStatusUpdater.can_transition("active", "active")
        assert allowed, f"transición idempotente rechazada: {reason}"
        allowed, reason = SprintStatusUpdater.can_transition("planning", "active")
        assert allowed, f"planning→active rechazada: {reason}"
        allowed, reason = SprintStatusUpdater.can_transition("active", "planning")
        assert not allowed, "retroceso active→planning debería fallar"
