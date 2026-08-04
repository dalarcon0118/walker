"""test_decision_gate_mapping.py — regresion del mapeo decision->gate.

Cubre el bug GAP-7 extendido: los sub-estados con gates de aprobacion
(DESIGN_REVIEW, ANALYZE.REVIEW, VERIFY.QA/PO_ACCEPT) se clasifican como
'guarded' (aristas con contract.pre) y el paso 11 de SubmitDataUseCase
solo resolvia XOR para 'xor'. Resultado: decision:approve capturaba pero
no transicionaba.

Este test asegura:
  1. _resolve_decision_gate mapea cada decision a la gate correcta por nodo.
  2. classify_sub_state identifica los sub-estados guarded.
  3. resolve_xor_by_intent encuentra la gate mapeada.
  4. El workflow completo carga y la gate se resuelve.
"""
from __future__ import annotations

import tempfile
import os
import json

from features.engine.domain.models import WorkflowState
from features.engine.infrastructure.repositories import WorkflowRepository


def _write_workflow(chain_dir: str, nodes: dict, edges: list[dict]) -> None:
    """Escribe un workflow.json minimo en chain_dir."""
    wf = {
        "version": "4.0",
        "initial_state": next(iter(nodes)),
        "nodes": nodes,
        "edges": edges,
    }
    with open(os.path.join(chain_dir, "workflow.json"), "w") as f:
        json.dump(wf, f)


def _state(node: str, sub: str) -> WorkflowState:
    return WorkflowState(current_node=node, current_sub_state=sub)


def test_resolve_decision_gate_design_review():
    """DESIGN_REVIEW.approve -> design_approved (gate guarded, no derivable)."""
    from features.engine.application.use_cases import _resolve_decision_gate

    with tempfile.TemporaryDirectory() as tmp:
        _write_workflow(
            tmp,
            {"DESIGN": {"sub_states": [{"key": "DESIGN_REVIEW", "execution": "user_input"}]}},
            [
                {"from": "DESIGN", "to": "ANALYZE", "gate": "design_approved"},
                {"from": "DESIGN", "to": "REFINE", "gate": "design_needs_refine"},
            ],
        )
        graph = WorkflowRepository().load(tmp)
        state = _state("DESIGN", "DESIGN_REVIEW")

        gate = _resolve_decision_gate(state, graph, "approve", {"decision": "approve"})
        assert gate == "design_approved", f"esperaba design_approved, got {gate}"

        gate_refine = _resolve_decision_gate(state, graph, "refine", {"decision": "refine"})
        assert gate_refine == "design_needs_refine", f"got {gate_refine}"


def test_resolve_decision_gate_task_review():
    """TASK_REVIEW.approve -> next_task (override existente)."""
    from features.engine.application.use_cases import _resolve_decision_gate

    with tempfile.TemporaryDirectory() as tmp:
        _write_workflow(
            tmp,
            {"SPRINT": {"sub_states": [{"key": "TASK_REVIEW", "execution": "user_input"}]}},
            [
                {"from": "SPRINT", "to": "SPRINT", "gate": "next_task"},
                {"from": "SPRINT", "to": "SPRINT", "gate": "all_done"},
                {"from": "SPRINT", "to": "SPRINT", "gate": "refine_task"},
            ],
        )
        graph = WorkflowRepository().load(tmp)
        state = _state("SPRINT", "TASK_REVIEW")

        gate = _resolve_decision_gate(state, graph, "approve", {"decision": "approve"})
        assert gate == "next_task", f"esperaba next_task, got {gate}"

        gate_done = _resolve_decision_gate(state, graph, "all_done", {"decision": "all_done"})
        assert gate_done == "all_done", f"got {gate_done}"


def test_resolve_decision_gate_verify():
    """VERIFY.QA/PO_ACCEPT.approve -> task_complete (gates propias del nodo)."""
    from features.engine.application.use_cases import _resolve_decision_gate

    with tempfile.TemporaryDirectory() as tmp:
        _write_workflow(
            tmp,
            {
                "VERIFY": {
                    "sub_states": [
                        {"key": "QA", "execution": "user_input"},
                        {"key": "PO_ACCEPT", "execution": "user_input"},
                    ]
                }
            },
            [
                {"from": "VERIFY", "to": "SPRINT", "gate": "task_complete"},
                {"from": "VERIFY", "to": "REFINE", "gate": "needs_refine"},
            ],
        )
        graph = WorkflowRepository().load(tmp)

        gate_qa = _resolve_decision_gate(_state("VERIFY", "QA"), graph, "approve", {})
        assert gate_qa == "task_complete", f"QA.approve esperaba task_complete, got {gate_qa}"

        gate_po = _resolve_decision_gate(_state("VERIFY", "PO_ACCEPT"), graph, "approve", {})
        assert gate_po == "task_complete", f"PO_ACCEPT.approve got {gate_po}"


def test_decision_gate_resolves_in_guarded_sub_state():
    """Un sub-estado 'guarded' (aristas con contract.pre) resuelve la gate por decision.

    Escenario del bug: DESIGN_REVIEW se clasifica guarded porque
    design_approved tiene contract.pre (ckg_has_decision). El paso 11 debe
    aceptar 'guarded' para transicionar.
    """
    from features.engine.domain.services import ValidationService
    from features.engine.application.use_cases import _resolve_decision_gate

    with tempfile.TemporaryDirectory() as tmp:
        _write_workflow(
            tmp,
            {"DESIGN": {"sub_states": [{"key": "DESIGN_REVIEW", "execution": "user_input"}]}},
            [
                {
                    "from": "DESIGN",
                    "to": "ANALYZE",
                    "gate": "design_approved",
                    "contract": {"pre": "ckg_has_decision('approve')"},
                },
                {"from": "DESIGN", "to": "REFINE", "gate": "design_needs_refine"},
            ],
        )
        graph = WorkflowRepository().load(tmp)
        state = _state("DESIGN", "DESIGN_REVIEW")

        graph_type = ValidationService.classify_sub_state(state, graph)
        assert graph_type == "guarded", f"esperaba guarded, got {graph_type}"

        gate = _resolve_decision_gate(state, graph, "approve", {"decision": "approve"})
        assert gate == "design_approved"

        resolved = ValidationService.resolve_xor_by_intent(state, graph, gate)
        assert resolved is not None and resolved.name == "design_approved"


def run_all():
    tests = [
        test_resolve_decision_gate_design_review,
        test_resolve_decision_gate_task_review,
        test_resolve_decision_gate_verify,
        test_decision_gate_resolves_in_guarded_sub_state,
    ]
    for t in tests:
        t()
        print(f"  OK {t.__name__}")


if __name__ == "__main__":
    run_all()
