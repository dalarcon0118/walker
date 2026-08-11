"""Tests BDD (pytest-bdd) — Mapeo de decision gates en el workflow.

Feature value: las gates de decisión se resuelven automáticamente según la
intención del usuario, permitiendo que el workflow avance sin intervención
manual cuando las condiciones se cumplen.

Escenarios: tests/features/decision_gate_mapping.feature
"""
from __future__ import annotations

import json
import os

import pytest
from pytest_bdd import given, scenarios, then, when

scenarios("features/decision_gate_mapping.feature")


@pytest.fixture
def dgm_ctx() -> dict:
    return {}


def _to_json_safe(obj):
    if isinstance(obj, dict):
        return {k: _to_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_json_safe(v) for v in obj]
    if hasattr(obj, "__dataclass_fields__"):
        return {k: _to_json_safe(getattr(obj, k)) for k in obj.__dataclass_fields__}
    return obj


def _write_workflow(chain_dir: str, nodes, edges: list[dict]) -> None:
    if hasattr(nodes, "items"):
        node_iter = nodes.items()
    else:
        node_iter = []
        for n in nodes:
            node_iter.append((n.node_id, n))

    nodes_dict = {}
    for nid, ndata in node_iter:
        nodes_dict[nid] = _to_json_safe(ndata)

    edges_list = [_to_json_safe(e) for e in edges]

    wf = {
        "version": "4.0",
        "initial_state": next(iter(nodes_dict)),
        "nodes": nodes_dict,
        "edges": edges_list,
    }
    with open(os.path.join(chain_dir, "workflow.json"), "w") as f:
        json.dump(wf, f)


def _load_graph(chain_dir: str):
    from features.engine.infrastructure.repositories import WorkflowRepository
    return WorkflowRepository().load(chain_dir)


@given("un workflow con DESIGN y DESIGN_REVIEW")
def _design_review_workflow(tmp_path, dgm_ctx: dict):
    chain_dir = str(tmp_path)
    dgm_ctx["chain_dir"] = chain_dir
    _write_workflow(
        chain_dir,
        {"DESIGN": {"sub_states": [{"key": "DESIGN_REVIEW", "execution": "user_input"}]}},
        [],
    )
    from features.engine.infrastructure.repositories import WorkflowRepository
    dgm_ctx["graph"] = WorkflowRepository().load(chain_dir)
    dgm_ctx["state"] = type("WS", (), {"current_node": "DESIGN", "current_sub_state": "DESIGN_REVIEW"})()


@given("un workflow con SPRINT y TASK_REVIEW")
def _task_review_workflow(tmp_path, dgm_ctx: dict):
    chain_dir = str(tmp_path)
    dgm_ctx["chain_dir"] = chain_dir
    _write_workflow(
        chain_dir,
        {"SPRINT": {"sub_states": [{"key": "TASK_REVIEW", "execution": "user_input"}]}},
        [],
    )
    from features.engine.infrastructure.repositories import WorkflowRepository
    dgm_ctx["graph"] = WorkflowRepository().load(chain_dir)
    dgm_ctx["state"] = type("WS", (), {"current_node": "SPRINT", "current_sub_state": "TASK_REVIEW"})()


@given("un workflow con VERIFY y QA")
def _verify_qa_workflow(tmp_path, dgm_ctx: dict):
    chain_dir = str(tmp_path)
    dgm_ctx["chain_dir"] = chain_dir
    _write_workflow(
        chain_dir,
        {
            "VERIFY": {
                "sub_states": [
                    {"key": "QA", "execution": "user_input"},
                    {"key": "PO_ACCEPT", "execution": "user_input"},
                ]
            }
        },
        [],
    )
    from features.engine.infrastructure.repositories import WorkflowRepository
    dgm_ctx["graph"] = WorkflowRepository().load(chain_dir)
    dgm_ctx["state"] = type("WS", (), {"current_node": "VERIFY", "current_sub_state": "QA"})()


@given("un workflow con VERIFY y PO_ACCEPT")
def _verify_po_workflow(tmp_path, dgm_ctx: dict):
    chain_dir = str(tmp_path)
    dgm_ctx["chain_dir"] = chain_dir
    _write_workflow(
        chain_dir,
        {
            "VERIFY": {
                "sub_states": [
                    {"key": "QA", "execution": "user_input"},
                    {"key": "PO_ACCEPT", "execution": "user_input"},
                ]
            }
        },
        [],
    )
    from features.engine.infrastructure.repositories import WorkflowRepository
    dgm_ctx["graph"] = WorkflowRepository().load(chain_dir)
    dgm_ctx["state"] = type("WS", (), {"current_node": "VERIFY", "current_sub_state": "PO_ACCEPT"})()


@given("el workflow tiene edge design_approved de DESIGN a ANALYZE")
def _add_design_approved(dgm_ctx: dict):
    chain_dir = dgm_ctx["chain_dir"]
    graph = dgm_ctx["graph"]
    edge = {"from": "DESIGN", "to": "ANALYZE", "gate": "design_approved"}
    graph.edges.append(edge)
    graph.edges_by_source.setdefault("DESIGN", []).append(edge)
    _write_workflow(chain_dir, graph.nodes, graph.edges)
    dgm_ctx["graph"] = _load_graph(chain_dir)


@given("el workflow tiene edge design_needs_refine de DESIGN a REFINE")
def _add_design_refine(dgm_ctx: dict):
    chain_dir = dgm_ctx["chain_dir"]
    graph = dgm_ctx["graph"]
    edge = {"from": "DESIGN", "to": "REFINE", "gate": "design_needs_refine"}
    graph.edges.append(edge)
    graph.edges_by_source.setdefault("DESIGN", []).append(edge)
    _write_workflow(chain_dir, graph.nodes, graph.edges)
    dgm_ctx["graph"] = _load_graph(chain_dir)


@given("el workflow tiene edge next_task de SPRINT a SPRINT")
def _add_next_task(dgm_ctx: dict):
    chain_dir = dgm_ctx["chain_dir"]
    graph = dgm_ctx["graph"]
    edge = {"from": "SPRINT", "to": "SPRINT", "gate": "next_task"}
    graph.edges.append(edge)
    graph.edges_by_source.setdefault("SPRINT", []).append(edge)
    _write_workflow(chain_dir, graph.nodes, graph.edges)
    dgm_ctx["graph"] = _load_graph(chain_dir)


@given("el workflow tiene edge all_done de SPRINT a SPRINT")
def _add_all_done(dgm_ctx: dict):
    chain_dir = dgm_ctx["chain_dir"]
    graph = dgm_ctx["graph"]
    edge = {"from": "SPRINT", "to": "SPRINT", "gate": "all_done"}
    graph.edges.append(edge)
    graph.edges_by_source.setdefault("SPRINT", []).append(edge)
    _write_workflow(chain_dir, graph.nodes, graph.edges)
    dgm_ctx["graph"] = _load_graph(chain_dir)


@given("el workflow tiene edge task_complete de VERIFY a SPRINT")
def _add_task_complete(dgm_ctx: dict):
    chain_dir = dgm_ctx["chain_dir"]
    graph = dgm_ctx["graph"]
    edge = {"from": "VERIFY", "to": "SPRINT", "gate": "task_complete"}
    graph.edges.append(edge)
    graph.edges_by_source.setdefault("VERIFY", []).append(edge)
    _write_workflow(chain_dir, graph.nodes, graph.edges)
    dgm_ctx["graph"] = _load_graph(chain_dir)


@given("el workflow tiene edge needs_refine de VERIFY a REFINE")
def _add_needs_refine(dgm_ctx: dict):
    chain_dir = dgm_ctx["chain_dir"]
    graph = dgm_ctx["graph"]
    edge = {"from": "VERIFY", "to": "REFINE", "gate": "needs_refine"}
    graph.edges.append(edge)
    graph.edges_by_source.setdefault("VERIFY", []).append(edge)
    _write_workflow(chain_dir, graph.nodes, graph.edges)
    dgm_ctx["graph"] = _load_graph(chain_dir)


@given("el workflow tiene edge design_approved con pre ckg_has_decision(approve)")
def _add_design_approved_with_contract(dgm_ctx: dict):
    chain_dir = dgm_ctx["chain_dir"]
    graph = dgm_ctx["graph"]
    edge = {
        "from": "DESIGN",
        "to": "ANALYZE",
        "gate": "design_approved",
        "contract": {"pre": "ckg_has_decision('approve')"},
    }
    graph.edges.append(edge)
    graph.edges_by_source.setdefault("DESIGN", []).append(edge)
    _write_workflow(chain_dir, graph.nodes, graph.edges)
    dgm_ctx["graph"] = _load_graph(chain_dir)


@when("el usuario expresa approve")
def _user_approves(dgm_ctx: dict):
    from features.engine.application.use_cases import _resolve_decision_gate
    dgm_ctx["gate"] = _resolve_decision_gate(
        dgm_ctx["state"], dgm_ctx["graph"], "approve", {"decision": "approve"}
    )


@when("el usuario expresa refine")
def _user_refines(dgm_ctx: dict):
    from features.engine.application.use_cases import _resolve_decision_gate
    dgm_ctx["gate"] = _resolve_decision_gate(
        dgm_ctx["state"], dgm_ctx["graph"], "refine", {"decision": "refine"}
    )


@when("el usuario expresa all_done")
def _user_all_done(dgm_ctx: dict):
    from features.engine.application.use_cases import _resolve_decision_gate
    dgm_ctx["gate"] = _resolve_decision_gate(
        dgm_ctx["state"], dgm_ctx["graph"], "all_done", {"decision": "all_done"}
    )


@when("el workflow clasifica el sub-estado como guarded")
def _classify_guarded(dgm_ctx: dict):
    from features.engine.domain.services import ValidationService
    dgm_ctx["graph_type"] = ValidationService.classify_sub_state(
        dgm_ctx["state"], dgm_ctx["graph"]
    )


@then("resolve_xor_by_intent encuentra la gate")
def _resolve_xor(dgm_ctx: dict):
    from features.engine.domain.services import ValidationService
    gate_name = dgm_ctx.get("gate")
    dgm_ctx["resolved"] = ValidationService.resolve_xor_by_intent(
        dgm_ctx["state"], dgm_ctx["graph"], gate_name
    )
    # Audit 2026-08-11: este step no assertaba nada (y escribía a
    # /tmp/bdd_debug.txt). El resultado de resolve_xor_by_intent quedaba sin
    # verificar: una mutación que lo rompiera pasaba la suite en verde.
    assert dgm_ctx["resolved"] is not None, (
        f"resolve_xor_by_intent no resolvió la gate '{gate_name}'"
    )
    assert getattr(dgm_ctx["resolved"], "name", None) == gate_name, (
        f"esperaba gate '{gate_name}', obtuvo {getattr(dgm_ctx['resolved'], 'name', None)}"
    )


@then("la gate resuelta es design_approved")
def _assert_design_approved(dgm_ctx: dict):
    assert dgm_ctx.get("gate") == "design_approved", f"esperaba design_approved, got {dgm_ctx.get('gate')}"


@then("la gate resuelta es next_task")
def _assert_next_task(dgm_ctx: dict):
    assert dgm_ctx.get("gate") == "next_task", f"esperaba next_task, got {dgm_ctx.get('gate')}"


@then("la gate resuelta es task_complete")
def _assert_task_complete(dgm_ctx: dict):
    assert dgm_ctx.get("gate") == "task_complete", f"esperaba task_complete, got {dgm_ctx.get('gate')}"


@then("la gate resuelta es design_needs_refine")
def _assert_refine(dgm_ctx: dict):
    assert dgm_ctx.get("gate") == "design_needs_refine", f"got {dgm_ctx.get('gate')}"


@then("el workflow puede transicionar de DESIGN a ANALYZE")
def _assert_design_to_analyze(dgm_ctx: dict):
    gate = dgm_ctx.get("gate")
    assert gate == "design_approved", f"la gate '{gate}' no conecta DESIGN -> ANALYZE"


@then("el workflow puede continuar al siguiente task")
def _assert_next_task_continuation(dgm_ctx: dict):
    gate = dgm_ctx.get("gate")
    assert gate == "next_task", f"la gate '{gate}' no continua al siguiente task"


@then("el workflow puede finalizar la verificación")
def _assert_verify_complete(dgm_ctx: dict):
    gate = dgm_ctx.get("gate")
    assert gate == "task_complete", f"la gate '{gate}' no finaliza la verificación"


@then("el producto puede marcarse como completado")
def _assert_product_completed(dgm_ctx: dict):
    gate = dgm_ctx.get("gate")
    assert gate == "task_complete", f"la gate '{gate}' no completa el producto"


@then("el sub-estado se clasifica como guarded")
def _assert_guarded(dgm_ctx: dict):
    assert dgm_ctx.get("graph_type") == "guarded", f"esperaba guarded, got {dgm_ctx.get('graph_type')}"


@then("resolve_xor_by_intent encuentra la gate sin bloquearse")
def _assert_xor_resolved(dgm_ctx: dict):
    resolved = dgm_ctx.get("resolved")
    assert resolved is not None, "resolve_xor_by_intent retorno None"
    assert getattr(resolved, "name", None) == "design_approved", f"gate esperada design_approved, got {resolved}"
