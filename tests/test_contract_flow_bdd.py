"""test_contract_flow_bdd.py — el contrato llega al prompt y al paso, no sólo al JSON.

`test_contract_fields_bdd` verifica el schema y `test_output_contract_dynamic_bdd`
el return del handler. Quedaban los dos huecos de punta a punta:

1. PO_ACCEPT: el briefing del gate tiene que mostrar el veredicto REAL de
   predicados (`false` / `sin verificar`), no un texto fijo.
2. INFO_ACQUISITION: el paso `auto` tiene que dejar `acquired=false` en el
   resultado del paso cuando no hubo queries ni gaps, no certificar por vacío.
"""

from __future__ import annotations

from fast_mcp.features.workflow.application.bundle import EngineBundle
from fast_mcp.features.workflow.application.orchestrator import OrchestrationService
from fast_mcp.features.workflow.application.prompt_context import build_prompt_extra
from fast_mcp.features.workflow.application.prompts.manager import PromptManager
from fast_mcp.features.workflow.domain.entities import (
    NodeExecution,
    SubState,
    WorkflowGraph,
    WorkflowNode,
    WorkflowState,
)
from fast_mcp.features.workflow.domain.enums import ExecutionType

from tests.test_fast_mcp_workflow_application_bdd import (
    MockSideEffects,
    MockTracer,
    MockWorkflowIO,
    MockWorkflowStore,
    _ctx,
)


def _render_po_accept(tmp_path, executions):
    state = WorkflowState(
        current_node="VERIFY",
        current_sub_state="PO_ACCEPT",
        sub_state_index=5,
        node_executions=executions,
    )
    extra = build_prompt_extra(state, str(tmp_path), "ses-test")
    return PromptManager(str(tmp_path)).resolve_for_sub_state(
        node_id="VERIFY",
        sub_state_key="PO_ACCEPT",
        execution="user_input",
        fields={"prompt_id": "verify/po-accept"},
        extra_data=extra,
    )["template"]


def _predicate_execution(all_satisfied: bool) -> NodeExecution:
    return NodeExecution(
        node="VERIFY",
        sub_state="PREDICATE_CHECK",
        action="verify_predicates",
        task_result={"action": "verify_predicates", "data": {"all_satisfied": all_satisfied}},
    )


def test_po_accept_muestra_el_veredicto_fallido(tmp_path):
    template = _render_po_accept(tmp_path, [_predicate_execution(False)])

    # Se mira la línea renderizada, no las palabras sueltas: el texto del
    # briefing explica qué hacer "si dice sin verificar".
    assert "- Predicados:    false" in template
    assert "- Predicados:    sin verificar" not in template
    assert "<exit_code>" not in template


def test_po_accept_declara_sin_verificar_si_no_hay_dato(tmp_path):
    template = _render_po_accept(tmp_path, [])

    assert "- Predicados:    sin verificar" in template


def _graph_info_acquisition() -> WorkflowGraph:
    node = WorkflowNode(
        node_id="IMPLEMENT",
        sub_states=[
            SubState(key="INFO_ACQUISITION", execution=ExecutionType.AUTO, action="info_acquisition"),
            SubState(key="USER_GATE", execution=ExecutionType.USER_INPUT),
        ],
    )
    return WorkflowGraph(nodes={"IMPLEMENT": node}, initial_state="IMPLEMENT")


def _orchestrator():
    from fast_mcp.composition import _build_task_registry

    graph = _graph_info_acquisition()
    state = WorkflowState(
        current_node="IMPLEMENT", current_sub_state="INFO_ACQUISITION", sub_state_index=0
    )
    bundle = EngineBundle(
        workflow_store=MockWorkflowStore(_graph=graph, _state=state),
        workflow_io=MockWorkflowIO(),
        tracer=MockTracer(),
        side_effects=MockSideEffects(),
        chain_dir="/tmp/test-project",
        task_registry=_build_task_registry(),
    )
    return OrchestrationService(bundle=bundle)


def test_info_acquisition_auto_no_certifica_en_el_resultado_del_paso():
    out = _orchestrator().advance(_ctx())

    assert out["ok"] is True, out
    data = (out.get("action_result") or {}).get("data") or {}
    assert data.get("acquired") is False
    assert "no certifica" in str(data.get("reason") or "")
