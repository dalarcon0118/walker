"""test_analyze_flow_verbosity.py — regresion de SPEC-005 (fusion KNOWLEDGE_REVIEW).

Fija GAP-5: el camino feliz tenia 7 user_input del PO por tarea.
ANALYZE.KNOWLEDGE_REVIEW (user_input redundante tras REVIEW) fue eliminado;
ANALYZE.REVIEW ahora aprueba analisis + conocimiento en un solo paso.
"""
from __future__ import annotations

import json
import os

NODE_PATH = os.path.join("features", "engine", "workflow", "nodes", "analyze.node.json")


def _load(path):
    with open(path) as f:
        return json.load(f)


def test_knowledge_review_eliminado():
    """KNOWLEDGE_REVIEW ya no existe en el nodo ANALYZE."""
    node = _load(NODE_PATH)
    keys = [s["key"] for s in node["sub_states"]]
    assert "KNOWLEDGE_REVIEW" not in keys, f"KNOWLEDGE_REVIEW debe eliminarse: {keys}"


def test_review_absorbe_knowledge():
    """REVIEW ahora captura knowledge_items en su output_schema."""
    node = _load(NODE_PATH)
    review = next(s for s in node["sub_states"] if s["key"] == "REVIEW")
    props = review.get("output_schema", {}).get("properties", {})
    assert "knowledge_items" in props, "REVIEW debe capturar knowledge_items"
    assert "decision" in props
    assert "evidence" in props


def test_knowledge_capture_es_fin_de_nodo():
    """KNOWLEDGE_CAPTURE ya no apunta a KNOWLEDGE_REVIEW (fin de nodo)."""
    node = _load(NODE_PATH)
    kc = next(s for s in node["sub_states"] if s["key"] == "KNOWLEDGE_CAPTURE")
    assert kc.get("on_success") in ("", None), "KNOWLEDGE_CAPTURE debe terminar el nodo"


def test_workflow_valido_sin_warnings():
    """verify_graph valida el grafo sin warnings (fusion completa)."""
    import sys

    sys.path.insert(0, ".")
    from features.engine.infrastructure.repositories import WorkflowRepository
    from features.engine.domain.services import ValidationService

    wf = WorkflowRepository().load(
        os.path.join(".cognitive", "projects", "walker", "data", "spring", "session-20260731")
    )
    v = ValidationService.verify_graph(wf)
    assert v["valid"] is True, f"grafo invalido: {v['errors']}"
    assert len(v["warnings"]) == 0, f"warnings: {v['warnings']}"


def test_review_maps_analysis_approved():
    """REVIEW.approve mapea a analysis_approved (salida del nodo)."""
    from features.engine.application.use_cases import _DECISION_GATE_OVERRIDES

    assert _DECISION_GATE_OVERRIDES.get("REVIEW", {}).get("approve") == "analysis_approved"


def run_all():
    tests = [
        test_knowledge_review_eliminado,
        test_review_absorbe_knowledge,
        test_knowledge_capture_es_fin_de_nodo,
        test_workflow_valido_sin_warnings,
        test_review_maps_analysis_approved,
    ]
    for t in tests:
        t()
        print(f"  OK {t.__name__}")


if __name__ == "__main__":
    run_all()
