"""Harness del router: un modo por error.

Mismo espíritu que `agent-plugins/walker-flight-control/gate.test.mjs`: cada
modo aísla UN fallo del router y verifica el contrato del sobre
(`ok=false`, `action`, `error` no vacío, `error_type`/`missing` cuando aplica).

Criterio de "listo" del lote de contexto: ningún paso devuelve un bloqueo sin
`error` y sin decir qué hacer. Este archivo es la matriz que lo sostiene.

Modos:
  node_complete        el nodo terminó: hay que elegir un gate
  user_input_required  falta la decisión del usuario
  task_failed          la acción del paso falló
  side_effect_failed   un efecto no opcional falló (cierre bloqueado)
  gate_blocked         el paso exige consultar antes de submit
  missing_artifacts    no se puede cerrar sin commit_hash/completed_at
"""
from __future__ import annotations

import pytest

from fast_mcp.features.delta_tracking.domain.spec_service import SpecStatusUpdater
from fast_mcp.features.workflow.application.bundle import EngineBundle
from fast_mcp.features.workflow.application.orchestrator import (
    MIN_SKILL_SCORE_REQUIRED,
    OrchestrationService,
)
from fast_mcp.features.workflow.domain.entities import (
    SubState,
    WorkflowGraph,
    WorkflowNode,
    WorkflowState,
)
from fast_mcp.features.workflow.domain.enums import ExecutionType


# ── helpers ─────────────────────────────────────────────────────────────

def _sub(key="S", **over):
    base = {"key": key, "execution": ExecutionType.AUTO}
    base.update(over)
    return SubState(**base)


def _orchestrator(sub_states, state, side_effects=None):
    node = WorkflowNode(node_id="N", sub_states=sub_states)
    graph = WorkflowGraph(nodes={"N": node}, initial_state="N")
    orch = OrchestrationService(
        bundle=EngineBundle(
            workflow_store=None,
            workflow_io=None,
            tracer=None,
            side_effects=side_effects,
            chain_dir="/tmp/x",
        )
    )
    orch._load_graph = lambda: graph
    orch._load_state = lambda: state
    orch._save_state = lambda s: True
    orch._trace_submit = lambda *a, **k: None
    orch._trace_transition = lambda *a, **k: None
    return orch


def _assert_router_error(response, action, error_type=None):
    assert response.get("ok") is False, response
    assert response.get("action") == action, response
    assert isinstance(response.get("error"), str) and response["error"].strip(), response
    if error_type:
        assert response.get("error_type") == error_type, response


# ── modos ───────────────────────────────────────────────────────────────

def mode_node_complete():
    state = WorkflowState(current_node="N", current_sub_state="S", sub_state_index=0)
    orch = _orchestrator([_sub("S")], state)
    return orch.advance(ctx=None, fields={})


def mode_user_input_required():
    sub = _sub(
        "GATE",
        execution=ExecutionType.USER_INPUT,
        output_schema={"type": "object", "required": ["decision"]},
    )
    state = WorkflowState(current_node="N", current_sub_state="GATE", sub_state_index=0)
    orch = _orchestrator([sub, _sub("NEXT")], state)
    return orch.advance(ctx=None, fields={})


def mode_task_failed():
    state = WorkflowState(current_node="N", current_sub_state="S", sub_state_index=0)
    orch = _orchestrator([_sub("S", action="rompe")], state)
    orch._execute_action = lambda *a, **k: ({"error": "boom"}, False, "rompe falló")
    return orch.advance(ctx=None, fields={})


def mode_side_effect_failed():
    class FakeEffects:
        def apply_side_effects(self, *a, **k):
            return {"ok": False, "error": "transform_spec: artifacts missing"}

    sub = _sub(
        "S",
        execution=ExecutionType.USER_INPUT,
        output_schema={"type": "object", "required": ["decision"]},
    )
    state = WorkflowState(current_node="N", current_sub_state="S", sub_state_index=0)
    orch = _orchestrator([sub], state, side_effects=FakeEffects())
    return orch.submit(ctx=None, fields={"decision": "accept"})


def mode_gate_blocked(monkeypatch):
    sub = _sub(
        "QA",
        execution=ExecutionType.USER_INPUT,
        output_schema={"type": "object", "required": ["decision"]},
        skill_intent={"policy": "required", "needs": "review"},
    )
    state = WorkflowState(current_node="N", current_sub_state="QA", sub_state_index=0)
    orch = _orchestrator([sub], state)
    monkeypatch.setattr(
        orch,
        "_skill_candidates_scored",
        lambda ctx, intent: [("code-review-checklist", MIN_SKILL_SCORE_REQUIRED)],
    )
    return orch.submit(ctx=None, fields={"decision": "approve"})


def mode_missing_artifacts():
    """El cierre no puede completar una spec sin artefactos (invariante de dominio)."""
    spec = {"status": "in_review"}
    with pytest.raises(ValueError) as exc:
        SpecStatusUpdater.update(spec, "completed")
    return {"ok": False, "action": "missing_artifacts", "error": str(exc.value)}


MODES = {
    "node_complete": (mode_node_complete, "node_complete", None),
    "user_input_required": (mode_user_input_required, "user_input_required", None),
    "task_failed": (mode_task_failed, "task_failed", None),
    "side_effect_failed": (mode_side_effect_failed, "side_effect_failed", None),
    "gate_blocked": (mode_gate_blocked, "gate_blocked", "required_skill_gate"),
    "missing_artifacts": (mode_missing_artifacts, "missing_artifacts", None),
}


@pytest.mark.parametrize("mode", sorted(MODES))
def test_cada_modo_devuelve_el_contrato_de_error(mode, monkeypatch):
    run, action, error_type = MODES[mode]

    response = run(monkeypatch) if mode == "gate_blocked" else run()

    _assert_router_error(response, action, error_type)


def test_user_input_required_dice_qué_falta():
    response = mode_user_input_required()

    assert response["missing"] == ["decision"]
    assert "decisión del usuario" in response["error"]


def test_gate_blocked_nombra_los_candidatos():
    with pytest.MonkeyPatch.context() as mp:
        response = mode_gate_blocked(mp)

    assert "code-review-checklist" in response["error"]


def test_missing_artifacts_nombra_los_campos():
    response = mode_missing_artifacts()

    assert "commit_hash" in response["error"]
    assert "completed_at" in response["error"]


def test_node_complete_ofrece_los_gates():
    response = mode_node_complete()

    assert "available_gates" in response
    assert "transition" in response["error"]
