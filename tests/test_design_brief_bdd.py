"""test_design_brief_bdd.py — gate por forma del artefacto (AE-INV-06).

El gate no valida "que el agente haya pensado": valida el `design_brief`.
Un brief con una sola opción, sin motivo del descarte, sin invariantes o sin
verificación se rechaza con `missing[]` y `remedy` (FC-INV-07: deny con remedio).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fast_mcp.features.workflow.domain.artifacts import (
    validate_artifact,
    validate_design_brief,
)

ROOT = Path(__file__).resolve().parents[1]

_VALID = {
    "problem": "El cierre no puede certificarse desde el propio server",
    "options": [
        {"id": "A", "summary": "job_id + polling", "cost": "1 día", "risk": "medio"},
        {"id": "B", "summary": "timeout alineado", "cost": "2 h", "risk": "bajo"},
    ],
    "chosen": "B",
    "rejected_because": {"A": "el agente no puede esperar un job al cerrar"},
    "invariants_touched": ["AE-INV-09"],
    "verification": [
        {"criterion": "el server sobrevive la cancelación", "check": "test",
         "ref": "tests/test_mcp_cancellation_bdd.py"},
    ],
}


def _brief(**overrides) -> dict:
    data = json.loads(json.dumps(_VALID))
    data.update(overrides)
    return data


def test_brief_valido_pasa():
    assert validate_design_brief(_VALID) is None
    assert validate_artifact("design_brief", {"design_brief": _VALID}) is None


def test_una_sola_opcion_no_es_diseno():
    error = validate_design_brief(_brief(options=[_VALID["options"][0]], chosen="A",
                                         rejected_because={}))
    assert error is not None
    assert any("2 alternativas" in m for m in error["missing"])


def test_descartada_sin_motivo_se_rechaza():
    error = validate_design_brief(_brief(rejected_because={}))
    assert error is not None
    assert any("rejected_because[A]" in m for m in error["missing"])


def test_chosen_debe_ser_una_opcion():
    error = validate_design_brief(_brief(chosen="C"))
    assert error is not None
    assert any("chosen" in m for m in error["missing"])


def test_verificacion_necesita_check_y_ref():
    error = validate_design_brief(_brief(verification=[{"criterion": "x", "check": "ojalá"}]))
    assert error is not None
    assert any("check" in m for m in error["missing"])
    assert any("ref" in m for m in error["missing"])


def test_sin_artefacto_el_error_trae_remedio():
    error = validate_artifact("design_brief", {"q1": "..."})
    assert error is not None
    assert error["missing"] == ["design_brief"]
    assert "remedy" in error


def test_design_analyze_declara_el_artefacto():
    node_path = ROOT / "data/workflow/nodes/design.node.json"
    if not node_path.is_file():
        pytest.skip("workflow nodes no versionados (data/ está en .gitignore)")
    node = json.loads(node_path.read_text())
    analyze = next(s for s in node["sub_states"] if s["key"] == "DESIGN_ANALYZE")
    assert analyze["input_schema"]["x-artifact"] == "design_brief"
    assert "design_brief" in analyze["input_schema"]["properties"]


def test_prompt_de_analyze_explica_la_forma():
    prompt = json.loads(
        (ROOT / "fast_mcp/features/workflow/application/prompts/templates/nodes/"
         "design/design-analyze.prompt.json").read_text()
    )["template"]
    assert "DESIGN BRIEF" in prompt
    assert "rejected_because" in prompt
    assert "verification" in prompt
