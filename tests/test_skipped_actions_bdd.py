"""test_skipped_actions_bdd.py — el gate no se lleva acciones sin avisar.

Medido 2026-11-26 sobre 1240 `node_executions`: `DESIGN.CKG_SAVE`
(documenting_knowledge) e `IMPLEMENT.STATE_CONSTRUCTION` nunca corrieron aunque
sus nodos sí se usaron. El motor ejecuta `sub_state.action` sólo en `submit` y
`advance` (2 call sites); un gate saca del nodo y la acción pendiente se pierde
sin rastro.

Contrato:
1. `skipped_actions` lista las acciones del nodo hasta la posición actual que no
   tienen ejecución registrada.
2. Los sub-estados posteriores no cuentan (todavía no llegaron).
3. Sale en la respuesta del `transition` y en el evento TRANSITION.
"""

from __future__ import annotations

from types import SimpleNamespace

from fast_mcp.features.workflow.application.orchestrator import (
    blocked_by_pending,
    skipped_actions,
)


def _node(*subs):
    return SimpleNamespace(
        sub_states=[
            SimpleNamespace(key=key, action=action) for key, action in subs
        ]
    )


def _state(index, executed=()):
    return SimpleNamespace(
        current_node="DESIGN",
        sub_state_index=index,
        node_executions=[
            SimpleNamespace(node="DESIGN", sub_state=key, action=action)
            for key, action in executed
        ],
    )


def test_lista_la_accion_que_el_gate_dejo_atras():
    node = _node(("DESIGN_GATHER", "gather_context"), ("CKG_SAVE", "documenting_knowledge"))

    skipped = skipped_actions(_state(1, [("DESIGN_GATHER", "gather_context")]), node)

    assert skipped == [{"sub_state": "CKG_SAVE", "action": "documenting_knowledge"}]


def test_una_accion_ya_ejecutada_no_se_reporta():
    node = _node(("DESIGN_GATHER", "gather_context"), ("CKG_SAVE", "documenting_knowledge"))

    skipped = skipped_actions(
        _state(
            1,
            [("DESIGN_GATHER", "gather_context"), ("CKG_SAVE", "documenting_knowledge")],
        ),
        node,
    )

    assert skipped == []


def test_lo_que_esta_mas_adelante_no_se_salteo():
    node = _node(
        ("DESIGN_GATHER", "gather_context"),
        ("DESIGN_REVIEW", ""),
        ("CKG_SAVE", "documenting_knowledge"),
    )

    skipped = skipped_actions(_state(1, [("DESIGN_GATHER", "gather_context")]), node)

    assert skipped == []


def test_sub_estados_sin_accion_no_cuentan():
    node = _node(("USER_GATE", ""), ("WORK", ""))

    assert skipped_actions(_state(1), node) == []


def _gate(name="design_approved", intent="advance"):
    return SimpleNamespace(name=name, intent=intent)


def test_un_gate_de_avance_bloquea_en_el_paso_actual_sin_ejecutar():
    node = _node(("DESIGN_REVIEW", ""), ("CKG_SAVE", "documenting_knowledge"))
    state = _state(1, [("DESIGN_GATHER", "gather_context")])
    state.current_sub_state = "CKG_SAVE"
    state.sub_state_index = 1

    blocked = blocked_by_pending(state, node, _gate())

    assert blocked == [{"sub_state": "CKG_SAVE", "action": "documenting_knowledge"}]


def test_un_gate_de_abandono_no_se_bloquea():
    node = _node(("DESIGN_REVIEW", ""), ("CKG_SAVE", "documenting_knowledge"))
    state = _state(1, [("DESIGN_GATHER", "gather_context")])
    state.current_sub_state = "CKG_SAVE"

    assert blocked_by_pending(state, node, _gate(intent="abandon")) == []


def test_no_bloquea_por_acciones_anteriores_ya_irrecuperables():
    node = _node(
        ("OBSERVE", "observe"),
        ("STATE_CONSTRUCTION", "state_construction"),
        ("USER_GATE", ""),
    )
    state = _state(2, [("OBSERVE", "observe")])
    state.current_sub_state = "USER_GATE"

    assert blocked_by_pending(state, node, _gate("implementation_done")) == []
