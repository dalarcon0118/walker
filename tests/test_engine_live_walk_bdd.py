"""tests/test_engine_live_walk_bdd.py — Live walk del workflow real por el engine canónico.

Recorre el workflow GLOBAL del motor (SSOT en el root del walker, project.md §
'Configuración del workflow: GLOBAL del motor') con la cadena canónica de
producción (WorkflowContext.resolve → OrchestrationService), verificando EN
VIVO las fases Gollwitzer cableadas:

  1. START (LIST→SELECT) → transition project_selected → SPRINT.ACTIVE
  2. SPRINT.ACTIVE: gate goal_invalidation disponible (PHASE 12) y
     transition goal_invalidation → SPRINT.PLANNING (re-deliberar el goal)
  3. SPRINT → start_task → DESIGN → design_approved → ANALYZE.GATHER
  4. ANALYZE.OBSERVE (PHASE 0): status() surfacea el prompt de fase y
     submit ejecuta la acción `observe` vía el TaskRegistry real
  5. ANALYZE.INFO_GATE (PHASE 11): status() surfacea el prompt y
     submit ejecuta `info_acquisition` (decision=acquired)
  6. ANALYZE (DELIBERATION): prompt 6->3 + gate analysis_approved disponible

El engine es anémico (ADR-067): el HOST avanza subestados tras submits
exitosos vía AdvanceService — este test juega el rol del host.

Aislamiento (modelo global): el workflow NO se copia al proyecto — el
fixture apunta el override `WALKER_WORKFLOW_MANIFEST` al SSOT del motor y
usa un chain_dir temporal SOLO para el estado/memoria del proyecto.
"""
import shutil
from pathlib import Path

import pytest

from fast_mcp.features.tasks.domain.task_registry import TaskRegistry, TaskResult
from fast_mcp.features.tasks.handlers import register_all_tasks
from fast_mcp.features.workflow.context import WorkflowContext
from fast_mcp.features.workflow.domain.execution import ExecutionContext
from fast_mcp.features.workflow.domain.services.advance import AdvanceService
from fast_mcp.shared_kernel.path_manager import PathManager
from fast_mcp.shared_kernel.paths import get_walker_root

REPO_ROOT = Path(__file__).resolve().parents[1]
ENGINE_WORKFLOW = get_walker_root() / "data" / "workflow" / "workflow.json"


@pytest.fixture()
def project(tmp_path, monkeypatch):
    """Proyecto temporal (solo estado) + workflow GLOBAL vía override."""
    monkeypatch.setenv(PathManager.ENV_OVERRIDE, str(ENGINE_WORKFLOW))

    registry = TaskRegistry()
    register_all_tasks(registry)

    # START.LIST declara action=list_projects (handler del host en producción;
    # fuera del alcance de este walk). El fail-loud de G-W6 exige registrarlo.
    registry.register(
        "list_projects",
        lambda ctx, data: TaskResult.success(data={"projects": []}),
        metadata={"description": "stub host action for live walk"},
    )

    ctx = WorkflowContext.resolve(str(tmp_path), task_registry=registry)
    assert ctx is not None, "WorkflowContext.resolve falló con el SSOT global"
    return ctx


def _exec(ctx, task_id="T-001"):
    return ExecutionContext(
        session_id="live-walk",
        trace_id="trace-live",
        span_id="span-live",
        chain_dir=ctx.chain_dir,
        task_id=task_id,
    )


def _host_advance(ctx):
    """Rol del HOST: avanzar al siguiente subestado tras un submit exitoso."""
    graph = ctx.bundle.workflow_store.load_graph()
    state = ctx.bundle.workflow_store.load_state(ctx.chain_dir)
    new_state = AdvanceService.advance(state, graph)
    assert new_state is not None, "no hay subestado siguiente (host advance)"
    ctx.bundle.workflow_store.save_state(ctx.chain_dir, new_state)
    return new_state


def test_live_walk_start_to_analyze_with_gollwitzer_phases(project):
    orch = project.orchestrator
    xctx = _exec(project)

    # ── 1. START: auto-init a LIST, submit select, transition a SPRINT ──
    result = orch.submit(xctx, fields={})
    assert result["ok"], result
    assert result["node"] == "START"

    _host_advance(project)  # LIST -> SELECT
    result = orch.submit(xctx, fields={"intent": "select", "project_id": "walker"})
    assert result["ok"], result

    result = orch.transition(xctx, "project_selected")
    assert result["ok"], result
    assert result["to_node"] == "SPRINT"
    assert result["to_sub_state"] == "ACTIVE"

    # ── 2. SPRINT.ACTIVE: PHASE 12 MONITORING — gate de invalidación ──
    status = orch.status(xctx)
    assert status["ok"]
    assert status["node"] == "SPRINT"
    assert status["sub_state"] == "ACTIVE"
    gate_names = [g["name"] for g in status["available_gates"]]
    assert "goal_invalidation" in gate_names, (
        "gate goal_invalidation (PHASE 12) no disponible en SPRINT.ACTIVE"
    )

    result = orch.transition(xctx, "goal_invalidation")
    assert result["ok"], result
    assert result["to_node"] == "SPRINT"
    assert result["to_sub_state"] == "PLANNING", (
        "goal_invalidation debe re-deliberar (ACTIVE -> PLANNING)"
    )

    # ── 3. Re-plan → start_task → DESIGN → design_approved → ANALYZE ──
    result = orch.transition(xctx, "start_task")
    assert result["ok"], result
    assert result["to_node"] == "DESIGN"
    assert result["to_sub_state"] == "DESIGN_GATHER"

    result = orch.transition(xctx, "design_approved")
    assert result["ok"], result
    assert result["to_node"] == "ANALYZE"
    assert result["to_sub_state"] == "GATHER"

    # ── 4. ANALYZE.OBSERVE (PHASE 0): prompt + acción `observe` ──
    _host_advance(project)  # GATHER -> OBSERVE
    status = orch.status(xctx)
    assert status["ok"]
    assert status["sub_state"] == "OBSERVE"
    assert status["execution_type"] == "llm_task"
    prompt = status.get("prompt") or {}
    assert prompt.get("error") is None, prompt
    text = prompt.get("template") or prompt.get("prompt") or ""
    assert "PHASE 0 OBSERVE" in text, "prompt de OBSERVE no surfacea en status()"
    assert "facts" in text and "contradictions" in text

    result = orch.submit(xctx, fields={
        "facts": ["auth.py usa Strategy pattern"],
        "inferred_information": ["el flujo de refresh puede verse afectado"],
        "assumptions": [],
        "contradictions": [],
        "paths": ["src/auth.py"],
        "evidence": "CPG: auth.py con clases A, B, C",
    })
    assert result["ok"], result  # la acción `observe` se ejecutó vía TaskRegistry

    # ── 5. ANALYZE.INFO_GATE (PHASE 11): prompt + acción `info_acquisition` ──
    _host_advance(project)  # OBSERVE -> INFO_GATE
    status = orch.status(xctx)
    assert status["sub_state"] == "INFO_GATE"
    prompt = status.get("prompt") or {}
    assert prompt.get("error") is None, prompt
    text = prompt.get("template") or prompt.get("prompt") or ""
    assert "PHASE 11 INFORMATION ACQUISITION" in text
    assert "DO NOT GUESS" in text

    result = orch.submit(xctx, fields={
        "decision": "acquired",
        "missing_information": ["estado real del módulo auth"],
        "queries": ["code_context:query auth"],
        "resolved_gaps": ["gap-auth-1"],
        "uncertainties": [],
    })
    assert result["ok"], result  # `info_acquisition` ejecutada

    # ── 6. ANALYZE (DELIBERATION): prompt 6->3 + gate de Rubicón ──
    _host_advance(project)  # INFO_GATE -> ANALYZE
    status = orch.status(xctx)
    assert status["sub_state"] == "ANALYZE"
    prompt = status.get("prompt") or {}
    text = prompt.get("template") or prompt.get("prompt") or ""
    assert "6->3 Task Analysis" in text
    gate_names = [g["name"] for g in status["available_gates"]]
    assert "analysis_approved" in gate_names


def test_live_walk_goal_invalidation_then_replan_keeps_state_coherent(project):
    """Tras goal_invalidation el estado queda en PLANNING y puede re-planear."""
    orch = project.orchestrator
    xctx = _exec(project)

    orch.submit(xctx, fields={})
    orch.transition(xctx, "project_selected")
    result = orch.transition(xctx, "goal_invalidation")
    assert result["ok"]

    status = orch.status(xctx)
    assert (status["node"], status["sub_state"]) == ("SPRINT", "PLANNING")
    # Desde PLANNING el ciclo deliberativo continúa (plan_approval gates)
    gate_names = [g["name"] for g in status["available_gates"]]
    assert "plan_approval_approved" in gate_names or "start_task" in gate_names
