"""BDD: `capture_decision` lee la decisión del submit (`fields`), no solo de `params`.

El nodo declara `params: {}` para `capture_decision`; la decisión viaja en los
campos del submit. Leer solo `params` hacía fallar todo gate de decisión.
"""
from types import SimpleNamespace

from fast_mcp.features.tasks.side_effects.decision import capture_decision


def test_lee_decision_de_fields():
    state = SimpleNamespace(current_sub_state="PLAN_APPROVAL", context={"task_id": "SPEC-002"})

    out = capture_decision("/tmp/p", {}, state=state, fields={"decision": "approve", "evidence": "ok"})

    assert out["ok"] is True
    assert out["decision"] == "approve"
    assert out["rationale"] == "ok"
    assert out["sub_state"] == "PLAN_APPROVAL"
    assert out["task_id"] == "SPEC-002"


def test_params_ganan_sobre_fields():
    out = capture_decision("/tmp/p", {"decision": "accept"}, fields={"decision": "approve"})

    assert out["ok"] is True
    assert out["decision"] == "accept"


def test_sin_decision_falla():
    out = capture_decision("/tmp/p", {}, fields={})

    assert out["ok"] is False
    assert "decision" in out["error"]
