"""BDD: `advance` no borra los campos que el submit ya registró.

Medido 2026-11-18: tras `submit` en VERIFY.MONITOR (plan/deviations/
non_occurrence), el `advance` siguiente volvió a ejecutar `monitoring` con
`fields={}`: la acción no vio los datos y la segunda ejecución grabó
`compliant=true` sin desviaciones, pisando la evidencia que el usuario iba a
juzgar en QA.
"""
from __future__ import annotations

from fast_mcp.features.workflow.application.bundle import EngineBundle
from fast_mcp.features.workflow.application.orchestrator import (
    OrchestrationService,
    merge_action_fields,
)
from fast_mcp.features.workflow.domain.entities import (
    SubState,
    SubStateData,
    WorkflowGraph,
    WorkflowNode,
    WorkflowState,
)
from fast_mcp.features.workflow.domain.enums import ExecutionType


def test_merge_action_fields_conserva_lo_registrado():
    state = WorkflowState(
        current_node="VERIFY",
        current_sub_state="MONITOR",
        sub_state_data={"MONITOR": SubStateData(fields={"plan": ["a"], "deviations": ["b"]})},
    )

    merged = merge_action_fields(state, "MONITOR", {})

    assert merged == {"plan": ["a"], "deviations": ["b"]}


def test_merge_action_fields_los_nuevos_ganan():
    state = WorkflowState(
        current_node="VERIFY",
        current_sub_state="MONITOR",
        sub_state_data={"MONITOR": SubStateData(fields={"plan": ["viejo"], "deviations": ["b"]})},
    )

    merged = merge_action_fields(state, "MONITOR", {"plan": ["nuevo"]})

    assert merged["plan"] == ["nuevo"]
    assert merged["deviations"] == ["b"]


def test_merge_action_fields_sin_estado_registrado():
    state = WorkflowState(current_node="N", current_sub_state="S1")

    assert merge_action_fields(state, "S1", {"x": 1}) == {"x": 1}
    assert merge_action_fields(state, "S1", None) == {}


def test_advance_ejecuta_la_accion_con_los_campos_registrados():
    node = WorkflowNode(
        node_id="VERIFY",
        sub_states=[
            SubState(key="MONITOR", execution=ExecutionType.LLM_TASK, action="monitoring"),
            SubState(key="QA", execution=ExecutionType.USER_INPUT),
        ],
    )
    graph = WorkflowGraph(nodes={"VERIFY": node}, initial_state="VERIFY")
    state = WorkflowState(
        current_node="VERIFY",
        current_sub_state="MONITOR",
        sub_state_index=0,
        sub_state_data={
            "MONITOR": SubStateData(
                fields={"plan": ["a"], "deviations": ["d"], "non_occurrence": []}
            )
        },
    )

    bundle = EngineBundle(
        workflow_store=None,
        workflow_io=None,
        tracer=None,
        side_effects=None,
        chain_dir="/tmp/x",
    )
    orch = OrchestrationService(bundle=bundle)
    captured: dict = {}

    orch._load_graph = lambda: graph
    orch._load_state = lambda: state
    orch._save_state = lambda s: True
    orch._trace_submit = lambda *a, **k: None

    def spy(ctx, sub_state, candidate, fields):
        captured["fields"] = dict(fields)
        return (
            {"action": sub_state.action, "data": {"compliant": len(fields.get("deviations", [])) == 0}},
            True,
            "",
        )

    orch._execute_action = spy

    result = orch.advance(ctx=None, fields={})

    assert result["ok"] is True, result
    assert captured["fields"]["plan"] == ["a"]
    assert captured["fields"]["deviations"] == ["d"]
