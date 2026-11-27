"""BDD: el gate user_input entrega el informe desde el sistema, no del agente."""

from __future__ import annotations

from fast_mcp.features.workflow.application.orchestrator import (
    build_review_artifact,
    render_review_markdown,
)
from fast_mcp.features.workflow.domain.entities import (
    SubState,
    WorkflowNode,
    WorkflowState,
)
from fast_mcp.features.workflow.domain.enums import ExecutionType
from fast_mcp.features.workflow.domain.values import SubStateData


def _node_and_state():
    node = WorkflowNode(
        node_id="ANALYZE",
        label="ANALYZE",
        sub_states=[
            SubState(key="OBSERVE", label="OBSERVE", execution=ExecutionType.LLM_TASK),
            SubState(key="ANALYZE", label="ANALYZE", execution=ExecutionType.LLM_TASK),
            SubState(key="REVIEW", label="REVIEW", execution=ExecutionType.USER_INPUT),
        ],
    )
    state = WorkflowState(
        current_node="ANALYZE",
        current_sub_state="REVIEW",
        sub_state_index=2,
        sub_state_data={
            "OBSERVE": SubStateData(fields={"facts": ["f1", "f2"], "project_id": "walker"}),
            "ANALYZE": SubStateData(fields={"preconditions": ["p1", "p2"]}),
        },
        context={"analysis": "resumen del analisis"},
    )
    return node, state


def test_gate_user_input_expone_el_artefacto():
    node, state = _node_and_state()
    review_ss = node.find_sub_state("REVIEW")

    artifact = build_review_artifact(state, node, review_ss)

    assert artifact is not None
    assert artifact["position"] == "ANALYZE.REVIEW"
    assert artifact["steps"]["OBSERVE"]["facts"] == ["f1", "f2"]
    assert artifact["steps"]["ANALYZE"]["preconditions"] == ["p1", "p2"]
    assert "project_id" not in artifact["steps"]["OBSERVE"]
    assert artifact["context"]["analysis"] == "resumen del analisis"


def test_review_markdown_incluye_la_evidencia():
    node, state = _node_and_state()
    artifact = build_review_artifact(state, node, node.find_sub_state("REVIEW"))

    md = render_review_markdown(artifact)

    assert "ANALYZE.REVIEW" in md
    assert "facts" in md and "f1" in md
    assert "preconditions" in md and "p1" in md


def test_paso_no_user_input_no_genera_review():
    node, state = _node_and_state()

    assert build_review_artifact(state, node, node.find_sub_state("OBSERVE")) is None
    assert render_review_markdown(None) == ""


def test_el_gate_incluye_las_metricas_del_review():
    from dataclasses import replace

    from fast_mcp.features.workflow.domain.entities import NodeExecution

    node, state = _node_and_state()
    execution = NodeExecution(
        node="ANALYZE",
        sub_state="CODE_REVIEW_SCAN",
        action="review_code",
        task_ok=True,
        task_result={
            "data": {
                "passed": True,
                "summary": "2 archivos: sintaxis ok; CPG 0 hallazgo(s)",
                "cpg": {
                    "available": True,
                    "metrics": {"files": 700, "loc": 160000, "cyclo_max": 157},
                    "gates": [{"name": "complexity", "actual": 157, "threshold": 200, "passed": True}],
                    "files_scanned": ["fast_mcp/x.py"],
                    "findings": {},
                    "issues": [],
                },
            }
        },
    )
    state = replace(state, node_executions=[execution])

    artifact = build_review_artifact(state, node, node.find_sub_state("REVIEW"))
    md = render_review_markdown(artifact)

    assert artifact["executions"][0]["data"]["review"]["metrics"]["cyclo_max"] == 157
    assert "review.metrics" in md and "cyclo_max" in md
    assert "review.gates" in md and "complexity" in md
