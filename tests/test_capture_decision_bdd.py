"""BDD: `capture_decision` lee la decisión del submit (`fields`), no solo de `params`.

El nodo declara `params: {}` para `capture_decision`; la decisión viaja en los
campos del submit. Leer solo `params` hacía fallar todo gate de decisión.
"""
from types import SimpleNamespace

from fast_mcp.features.tasks.side_effects.decision import capture_decision


def test_lee_decision_de_fields():
    state = SimpleNamespace(current_sub_state="PLAN_APPROVAL", context={"task_id": "SPEC-002"})

    out = capture_decision("/tmp/p", {}, state=state, fields={"decision": "approve", "evidence": "ok", "_human": True})

    assert out["ok"] is True
    assert out["decision"] == "approve"
    assert out["rationale"] == "ok"
    assert out["sub_state"] == "PLAN_APPROVAL"
    assert out["task_id"] == "SPEC-002"


def test_params_ganan_sobre_fields():
    out = capture_decision("/tmp/p", {"decision": "accept"}, fields={"decision": "approve", "_human": True})

    assert out["ok"] is True
    assert out["decision"] == "accept"


def test_sin_decision_falla():
    out = capture_decision("/tmp/p", {}, fields={})

    assert out["ok"] is False
    assert "decision" in out["error"]


def test_lee_la_decision_registrada_en_el_estado():
    """`advance` después de `submit` re-ejecuta el efecto con fields vacío.

    La decisión ya está en el estado; el efecto debe leerla de ahí para no
    romper el paso siguiente (DESIGN_REVIEW -> CKG_SAVE).
    """
    from fast_mcp.features.workflow.domain.entities import SubStateData

    # En `advance` el estado ya está movido al sub-estado siguiente
    # (CKG_SAVE); la decisión quedó registrada en DESIGN_REVIEW.
    state = SimpleNamespace(
        current_sub_state="CKG_SAVE",
        sub_state_data={
            "DESIGN_REVIEW": SubStateData(
                fields={"decision": "approve", "evidence": "ADR-lite ok", "_human": True}
            )
        },
        context={"task_id": "SPEC-002"},
    )

    out = capture_decision("/tmp/p", {}, state=state, fields={})

    assert out["ok"] is True
    assert out["decision"] == "approve"
    assert out["rationale"] == "ADR-lite ok"
    assert out["sub_state"] == "DESIGN_REVIEW"
