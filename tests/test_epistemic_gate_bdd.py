"""test_epistemic_gate_bdd.py — el gate de premisas: ¿bloquea de verdad?

Contexto medido (2026-11-17): los 34 sub-estados del workflow tienen
`required_fields: []`. El contenedor existe (Spec.preconditions, evidence,
acceptance_criteria, predicates; y `verify_engine` sabe evaluar
`file_exists`/`symbol_exists`/`command_exit`/`test_passes`), pero nada lo
declara obligatorio, así que una tarea puede atravesar ANALYZE → IMPLEMENT sin
una sola premisa.

Y el gate, además, tenía un agujero: `is_field_complete("preconditions")`
devolvía True para `[]`, porque comparaba contra "" y un contenedor vacío no es
"". Declarar el campo habría producido un gate que no bloquea nada.

Estos tests fijan la semántica del gate, no la política: qué campos se exigen y
en qué sub-estado es una decisión aparte, y se prueba con su propio probe.
"""

from __future__ import annotations

from fast_mcp.features.workflow.domain.entities import (
    SubState,
    WorkflowGraph,
    WorkflowNode,
    WorkflowState,
)
from fast_mcp.features.workflow.domain.services.validation import ValidationService
from fast_mcp.features.workflow.domain.values import SubStateData


def _graph(required: list[str]) -> WorkflowGraph:
    node = WorkflowNode(
        node_id="analyze",
        label="analyze",
        sub_states=[
            SubState(key="OBSERVE", label="observe"),
            SubState(key="ANALYZE", label="analyze", required_fields=required),
        ],
    )
    return WorkflowGraph(nodes={"analyze": node}, edges=[], initial_state="analyze")


def _state(fields: dict, index: int = 1) -> WorkflowState:
    state = WorkflowState(current_node="analyze", current_sub_state="OBSERVE")
    state = state.record_submit("OBSERVE", {"note": "ok"})
    state = state.record_submit("ANALYZE", fields)
    return state.with_node("analyze", "ANALYZE", index)


def test_lista_vacia_no_satisface_un_campo_requerido():
    """El agujero: `[] != ""` dejaba pasar un gate de premisas sin premisas."""
    assert not SubStateData(fields={"preconditions": []}).is_field_complete("preconditions")


def test_dict_vacio_tampoco():
    assert not SubStateData(fields={"predicates": {}}).is_field_complete("predicates")


def test_campo_ausente_no_esta_completo():
    assert not SubStateData(fields={}).is_field_complete("preconditions")


def test_valor_con_contenido_si_esta_completo():
    assert SubStateData(fields={"preconditions": ["el test falla"]}).is_field_complete(
        "preconditions"
    )
    assert SubStateData(fields={"predicates": {"a": 1}}).is_field_complete("predicates")
    assert SubStateData(fields={"evidence": "traza 123"}).is_field_complete("evidence")


def test_transicion_sin_premisas_es_rechazada():
    errors = ValidationService.check_transition_preconditions(
        _state({"preconditions": []}), _graph(["preconditions"]), ""
    )

    assert errors and "preconditions" in errors[0]


def test_transicion_con_premisas_pasa():
    errors = ValidationService.check_transition_preconditions(
        _state({"preconditions": ["el repo no tiene .env"]}), _graph(["preconditions"]), ""
    )

    assert errors == []


def test_sin_required_fields_no_se_exige_nada():
    """Compatibilidad: hoy todos los sub-estados están así."""
    errors = ValidationService.check_transition_preconditions(_state({}), _graph([]), "")

    assert errors == []
