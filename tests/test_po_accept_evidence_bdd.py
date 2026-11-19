"""BDD: el cierre de PO_ACCEPT usa el evidence de VERIFY.QA.

Regresión medida 2026-11-19 al aceptar SPEC-001: el `transform_spec` de
PO_ACCEPT leía `NODE.PO_ACCEPT.output.evidence`, pero el contrato de
PO_ACCEPT solo declara `decision` y `feedback`. El primer `accept` registró
la decisión y el gate cruzó, pero el cierre falló con:

    "Cannot mark spec as completed: missing required artifacts:
     evidence (VERIFY/QA, at least one item)"

El evidence real lo produce VERIFY.QA; PO_ACCEPT lo consume. El contrato
quedó: QA exige `evidence` y PO_ACCEPT lo lee de `NODE.QA.output.evidence`.
`feedback` sigue siendo el comentario del PO, no el artefacto de cierre.
"""
from __future__ import annotations

import pytest

from fast_mcp.features.delta_tracking.domain.entities import Spec
from fast_mcp.features.delta_tracking.domain.spec_service import SpecService
from fast_mcp.features.workflow.context import WorkflowContext
from fast_mcp.features.workflow.domain.entities import WorkflowState
from fast_mcp.features.workflow.domain.values import SubStateData
from fast_mcp.features.workflow.infrastructure.side_effect_engine import _resolve_params

ROOT = "/Users/davidmartinez/develop/Active-projects/walker"
QA_EVIDENCE = "QA: pytest 1334 passed / 0 failures; predicados 2/2"


def _graph():
    ctx = WorkflowContext.resolve(ROOT)
    if ctx is None:
        pytest.skip("workflow canónico no resoluble")
    return ctx.orchestrator._load_graph()


def _transform():
    ss = _graph().get_node("VERIFY").find_sub_state("PO_ACCEPT")
    for se in ss.side_effects or []:
        if getattr(se, "type", "") == "transform_spec":
            return ss, se
    pytest.fail("PO_ACCEPT no declara transform_spec")


def _state_with_qa_evidence(evidence: str = QA_EVIDENCE) -> WorkflowState:
    node = _graph().get_node("VERIFY")
    return WorkflowState(
        current_node="VERIFY",
        current_sub_state="PO_ACCEPT",
        sub_state_index=node.sub_state_index("PO_ACCEPT"),
        sub_state_data={
            "QA": SubStateData(fields={"decision": "approve", "evidence": evidence}),
            "PO_ACCEPT": SubStateData(fields={"decision": "accept", "feedback": "ok"}),
            "REPORT_GATE": SubStateData(
                fields={
                    "commit_hash": "abc123",
                    "completed_at": "2026-11-19T00:00:00+00:00",
                }
            ),
        },
    )


def test_po_accept_toma_el_evidence_de_qa():
    _, se = _transform()

    assert se.params["fields"]["evidence"] == "NODE.QA.output.evidence"


def test_qa_exige_evidence_como_artefacto_de_cierre():
    ss = _graph().get_node("VERIFY").find_sub_state("QA")

    assert "evidence" in ((ss.output_schema or {}).get("required") or [])


def test_el_transform_resuelve_evidence_aunque_po_solo_mande_feedback():
    ss, se = _transform()

    resolved, unresolved = _resolve_params(
        se.params["fields"],
        _state_with_qa_evidence(),
        ss,
        {"decision": "accept", "feedback": "Funcionalidad correcta"},
    )

    assert resolved["evidence"] == QA_EVIDENCE
    assert resolved["review_verdict"] == "approve"
    assert resolved["commit_hash"] == "abc123"
    assert unresolved == []


def test_la_respuesta_documentada_del_prompt_cierra_la_spec():
    ss, se = _transform()
    resolved, _ = _resolve_params(
        se.params["fields"],
        _state_with_qa_evidence(),
        ss,
        {"decision": "accept", "feedback": "Funcionalidad correcta"},
    )
    spec = Spec(
        spring_id="SPR-TEST",
        objective="probar el cierre",
        spec_index=1,
        status="in_review",
        files=["x.py"],
        acceptance_criteria=["debe cerrar"],
        completed_at="2026-11-19T00:00:00+00:00",
        commit_hash="abc123",
    )

    merged = SpecService.merge(spec, {"status": "completed", **resolved})

    assert merged.status == "completed"
    assert merged.review_verdict == "approve"
    assert merged.evidence == QA_EVIDENCE


def test_sin_evidence_el_cierre_es_ruidoso():
    spec = Spec(
        spring_id="SPR-TEST",
        objective="probar el cierre",
        spec_index=1,
        status="in_review",
        files=["x.py"],
        acceptance_criteria=["debe cerrar"],
        completed_at="2026-11-19T00:00:00+00:00",
        commit_hash="abc123",
    )

    with pytest.raises(ValueError, match="evidence"):
        SpecService.merge(spec, {"status": "completed", "review_verdict": "approve"})
