"""test_knowledge_capture_schema.py — regresion del schema canonico (SPEC-004).

Fija GAP-6: los 4 schemas divergentes de captura de conocimiento
(ANALYZE.KNOWLEDGE_CAPTURE, IMPLEMENT.KNOWLEDGE_UPDATE,
REFINE.KNOWLEDGE_CAPTURE, SPRINT.RETRO) ahora referencian un schema
canonico schema/knowledge-capture.schema.json via $ref.
"""
from __future__ import annotations

import json
import os

SCHEMA_PATH = os.path.join("schema", "knowledge-capture.schema.json")
WORKFLOW_DIR = os.path.join("features", "engine", "workflow", "nodes")
CANONICO = "schema/knowledge-capture.schema.json"


def _load(path):
    with open(path) as f:
        return json.load(f)


def test_schema_canonico_existe_y_es_valido():
    """El schema canonico existe y es JSON valido."""
    schema = _load(SCHEMA_PATH)
    assert schema.get("$defs"), "debe tener $defs"
    assert "knowledge_item" in schema["$defs"], "debe definir knowledge_item"


def test_knowledge_item_defs_completo():
    """knowledge_item define type/domain/statement/rationale."""
    schema = _load(SCHEMA_PATH)
    item = schema["$defs"]["knowledge_item"]
    props = item.get("properties", {})
    assert set(props.keys()) >= {"type", "domain", "statement", "rationale"}
    assert "type" in item.get("required", [])


def test_analyze_knowledge_capture_ref_canonico():
    """ANALYZE.KNOWLEDGE_CAPTURE referencia el canonico."""
    node = _load(os.path.join(WORKFLOW_DIR, "analyze.node.json"))
    ss = next(s for s in node["sub_states"] if s["key"] == "KNOWLEDGE_CAPTURE")
    assert ss["output_schema"]["$ref"] == CANONICO


def test_refine_knowledge_capture_ref_canonico():
    """REFINE.KNOWLEDGE_CAPTURE referencia el canonico."""
    node = _load(os.path.join(WORKFLOW_DIR, "refine.node.json"))
    ss = next(s for s in node["sub_states"] if s["key"] == "KNOWLEDGE_CAPTURE")
    assert ss["output_schema"]["$ref"] == CANONICO


def test_implement_knowledge_update_ref_canonico():
    """IMPLEMENT.KNOWLEDGE_UPDATE referencia el canonico."""
    node = _load(os.path.join(WORKFLOW_DIR, "implement.node.json"))
    ss = next(s for s in node["sub_states"] if s["key"] == "KNOWLEDGE_UPDATE")
    assert ss["output_schema"]["$ref"] == CANONICO


def test_sprint_retro_ref_canonico():
    """SPRINT.RETRO referencia el canonico."""
    node = _load(os.path.join(WORKFLOW_DIR, "sprint.node.json"))
    ss = next(s for s in node["sub_states"] if s["key"] == "RETRO")
    assert ss["output_schema"]["$ref"] == CANONICO


def test_workflow_carga_con_schemas_ref():
    """El workflow carga sin errores con los schemas con $ref."""
    import sys

    sys.path.insert(0, ".")
    from features.engine.infrastructure.repositories import WorkflowRepository

    wf = WorkflowRepository().load(
        os.path.join(".cognitive", "projects", "walker", "data", "spring", "session-20260731")
    )
    assert len(wf.nodes) == 8


def run_all():
    tests = [
        test_schema_canonico_existe_y_es_valido,
        test_knowledge_item_defs_completo,
        test_analyze_knowledge_capture_ref_canonico,
        test_refine_knowledge_capture_ref_canonico,
        test_implement_knowledge_update_ref_canonico,
        test_sprint_retro_ref_canonico,
        test_workflow_carga_con_schemas_ref,
    ]
    for t in tests:
        t()
        print(f"  OK {t.__name__}")


if __name__ == "__main__":
    run_all()
