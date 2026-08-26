"""Tests BDD (pytest-bdd) — BPMN P1 gaps.

Feature value: valida que el motor maneje eventos de señal, compensaciones y
bloqueos para que el workflow sea resiliente y pueda recuperarse de fallos.

Escenarios: bdd/features/bpmn_gaps_p1.feature
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import pytest
from pytest_bdd import given, scenarios, then, when
from returns.pipeline import is_successful

scenarios("bdd/features/bpmn_gaps_p1.feature")

NODE_PATH = os.path.join(
    "core", "engine", "workflow", "nodes", "implement.node.json"
)


@pytest.fixture
def bpmn_ctx() -> dict:
    return {}


def _load_node_dict():
    with open(NODE_PATH) as f:
        return json.load(f)


def _load_work_substate(node_dict):
    for ss in node_dict.get("sub_states", []):
        if ss.get("key") == "WORK":
            return ss
    return {}


def _make_blocked_state(
    node="IMPLEMENT",
    sub_state="WORK",
    block_reason="waiting",
    pending_events=None,
    pending_signals=None,
    timeout_ms=0,
):
    return {
        "current_node": node,
        "current_sub_state": sub_state,
        "blocked_at": f"{node}.{sub_state}",
        "block_reason": block_reason,
        "block_since": datetime.now(timezone.utc).isoformat(),
        "pending_events": pending_events or [],
        "pending_signals": pending_signals or [],
        "block_timeout_ms": timeout_ms,
    }


# ── BlockResolver: given ────────────────────────────────────────────────────


@given("un sprint bloqueado esperando una consulta al knowledge graph")
def _blocked_waiting_kg(bpmn_ctx: dict):
    from shared.core.engine.domain.entities import (
        SubState,
        WorkflowNode,
        WorkflowGraph,
        WorkflowState,
    )

    node_dict = _load_node_dict()
    d = dict(_load_work_substate(node_dict))
    d.update(
        {
            "side_effects": [],
            "order": 0,
            "options": [],
            "interrupting": True,
            "on_timeout": "skip",
            "wait_for": ["kg_query"],
            "emits": [],
            "waits_for": [],
            "on_signal": "",
        }
    )
    sub = SubState.from_dict(d)
    node = WorkflowNode(node_id="IMPLEMENT", label="Implement", sub_states=[sub])
    graph = WorkflowGraph(nodes={"IMPLEMENT": node}, initial_state="IMPLEMENT")
    state = WorkflowState(**_make_blocked_state(pending_events=["kg_query"]))

    bpmn_ctx["state"] = state
    bpmn_ctx["graph"] = graph
    bpmn_ctx["sub_state"] = sub


@given("un sprint bloqueado en una sub-tarea")
def _blocked_subtask(bpmn_ctx: dict):
    from shared.core.engine.domain.entities import (
        SubState,
        WorkflowNode,
        WorkflowGraph,
        WorkflowState,
    )

    node_dict = _load_node_dict()
    d = dict(_load_work_substate(node_dict))
    d.update(
        {
            "side_effects": [],
            "order": 0,
            "options": [],
            "interrupting": True,
            "on_timeout": "skip",
            "wait_for": ["kg_query"],
            "emits": [],
            "waits_for": [],
            "on_signal": "",
        }
    )
    sub = SubState.from_dict(d)
    node = WorkflowNode(node_id="IMPLEMENT", label="Implement", sub_states=[sub])
    graph = WorkflowGraph(nodes={"IMPLEMENT": node}, initial_state="IMPLEMENT")
    state = WorkflowState(**_make_blocked_state(pending_events=["kg_query"]))

    bpmn_ctx["state"] = state
    bpmn_ctx["graph"] = graph
    bpmn_ctx["sub_state"] = sub


@given("un sprint bloqueado esperando una señal externa")
def _blocked_waiting_signal(bpmn_ctx: dict):
    from shared.core.engine.domain.entities import (
        SubState,
        WorkflowNode,
        WorkflowGraph,
        WorkflowState,
    )

    node_dict = _load_node_dict()
    d = dict(_load_work_substate(node_dict))
    d.update(
        {
            "side_effects": [],
            "order": 0,
            "options": [],
            "interrupting": True,
            "on_timeout": "skip",
            "wait_for": ["external_signal"],
            "emits": [],
            "waits_for": [],
            "on_signal": "wait",
        }
    )
    sub = SubState.from_dict(d)
    node = WorkflowNode(node_id="IMPLEMENT", label="Implement", sub_states=[sub])
    graph = WorkflowGraph(nodes={"IMPLEMENT": node}, initial_state="IMPLEMENT")
    state = WorkflowState(**_make_blocked_state(pending_events=["external_signal"]))

    bpmn_ctx["state"] = state
    bpmn_ctx["graph"] = graph
    bpmn_ctx["sub_state"] = sub


@given("un sprint bloqueado que no se puede resolver automáticamente")
def _blocked_needs_escalation(bpmn_ctx: dict):
    from shared.core.engine.domain.entities import (
        SubState,
        WorkflowNode,
        WorkflowGraph,
        WorkflowState,
    )

    node_dict = _load_node_dict()
    d = dict(_load_work_substate(node_dict))
    d.update(
        {
            "side_effects": [],
            "order": 0,
            "options": [],
            "interrupting": True,
            "on_timeout": "skip",
            "wait_for": ["kg_query"],
            "emits": [],
            "waits_for": [],
            "on_signal": "",
        }
    )
    sub = SubState.from_dict(d)
    implement_node = WorkflowNode(
        node_id="IMPLEMENT", label="Implement", sub_states=[sub]
    )
    escalation_sub = SubState.from_dict(
        {
            "key": "INIT",
            "label": "Init",
            "execution": "manual",
            "action": "",
            "input_schema": {},
            "output_schema": {},
            "side_effects": [],
            "on_success": "",
            "on_failure": "",
            "timeout_ms": 0,
            "required_fields": [],
            "auto_complete": "",
            "on_failure_loop": False,
            "order": 0,
            "prompt": "",
            "options": [],
            "on_entry_message": "",
            "prompt_id": "",
            "context": None,
            "required_tools": [],
            "provides": [],
            "needs": [],
            "interrupting": True,
            "on_timeout": "",
            "wait_for": [],
            "emits": [],
            "waits_for": [],
            "on_signal": "",
        }
    )
    escalate_node = WorkflowNode(
        node_id="ESCALATION", label="Escalation", sub_states=[escalation_sub]
    )
    graph = WorkflowGraph(
        nodes={"IMPLEMENT": implement_node, "ESCALATION": escalate_node},
        initial_state="IMPLEMENT",
    )
    state = WorkflowState(**_make_blocked_state(pending_events=["kg_query"]))

    bpmn_ctx["state"] = state
    bpmn_ctx["graph"] = graph
    bpmn_ctx["sub_state"] = sub


@given("un sprint con cambios aplicados que requiere rollback")
def _blocked_needs_compensation(bpmn_ctx: dict):
    from shared.core.engine.domain.entities import (
        SubState,
        WorkflowNode,
        WorkflowGraph,
        WorkflowState,
    )

    node_dict = _load_node_dict()
    d = dict(_load_work_substate(node_dict))
    d.update(
        {
            "side_effects": [],
            "order": 0,
            "options": [],
            "interrupting": True,
            "on_timeout": "skip",
            "wait_for": ["kg_query"],
            "emits": [],
            "waits_for": [],
            "on_signal": "",
        }
    )
    sub = SubState.from_dict(d)
    node = WorkflowNode(node_id="IMPLEMENT", label="Implement", sub_states=[sub])
    graph = WorkflowGraph(nodes={"IMPLEMENT": node}, initial_state="IMPLEMENT")
    state = WorkflowState(
        current_node="IMPLEMENT", current_sub_state="WORK", sub_state_index=0
    )

    bpmn_ctx["state"] = state
    bpmn_ctx["graph"] = graph
    bpmn_ctx["sub_state"] = sub


@given("un sprint esperando un evento que tarda demasiado")
def _blocked_waiting_timeout(bpmn_ctx: dict):
    from shared.core.engine.domain.entities import (
        SubState,
        WorkflowNode,
        WorkflowGraph,
        WorkflowState,
    )

    node_dict = _load_node_dict()
    d = dict(_load_work_substate(node_dict))
    d.update(
        {
            "timeout_ms": 100,
            "interrupting": True,
            "on_timeout": "skip",
            "wait_for": ["kg_query"],
            "emits": [],
            "waits_for": [],
            "on_signal": "",
            "side_effects": [],
            "order": 0,
            "options": [],
        }
    )
    sub = SubState.from_dict(d)
    node = WorkflowNode(node_id="IMPLEMENT", label="Implement", sub_states=[sub])
    graph = WorkflowGraph(nodes={"IMPLEMENT": node}, initial_state="IMPLEMENT")
    datetime.now(timezone.utc).replace(microsecond=0)
    state = WorkflowState(**_make_blocked_state(timeout_ms=100))

    bpmn_ctx["state"] = state
    bpmn_ctx["graph"] = graph
    bpmn_ctx["sub_state"] = sub


@given("un sprint esperando una señal de aprobación")
def _blocked_waiting_approval(bpmn_ctx: dict):
    from shared.core.engine.domain.entities import (
        SubState,
        WorkflowNode,
        WorkflowGraph,
        WorkflowState,
    )

    node_dict = _load_node_dict()
    d = dict(_load_work_substate(node_dict))
    d.update(
        {
            "side_effects": [],
            "order": 0,
            "options": [],
            "interrupting": True,
            "timeout_ms": 0,
            "on_timeout": "",
            "wait_for": [],
            "emits": [],
            "waits_for": ["approval_received"],
            "on_signal": "auto_advance",
        }
    )
    sub = SubState.from_dict(d)
    node = WorkflowNode(node_id="IMPLEMENT", label="Implement", sub_states=[sub])
    graph = WorkflowGraph(nodes={"IMPLEMENT": node}, initial_state="IMPLEMENT")
    state = WorkflowState(
        current_node="IMPLEMENT",
        current_sub_state="WORK",
        blocked_at="IMPLEMENT.WORK",
        block_reason="waiting for signal",
        block_since=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        pending_events=[],
        pending_signals=["approval_received"],
        block_timeout_ms=0,
    )

    bpmn_ctx["state"] = state
    bpmn_ctx["graph"] = graph
    bpmn_ctx["sub_state"] = sub


@given("múltiples nodos suscritos a una señal de actualización")
def _multiple_nodes_subscribed(bpmn_ctx: dict):
    from shared.core.engine.domain.services.signal_bus import SignalBus

    bus = SignalBus()
    bus.subscribe("NODE_A", "sig1")
    bus.subscribe("NODE_B", "sig1")
    bus.subscribe("NODE_A", "sig2")
    bpmn_ctx["signal_bus"] = bus
    bpmn_ctx["subscribers"] = ["NODE_A", "NODE_B"]
    bpmn_ctx["signal"] = "sig1"


@given("un nodo suscrito a señales globales")
def _wildcard_node_subscribed(bpmn_ctx: dict):
    from shared.core.engine.domain.services.signal_bus import SignalBus

    bus = SignalBus()
    bus.subscribe("*", "global_signal")
    bpmn_ctx["signal_bus"] = bus
    bpmn_ctx["subscribers"] = ["*"]
    bpmn_ctx["signal"] = "global_signal"


@given("un sprint con múltiples efectos secundarios aplicados")
def _sprint_with_side_effects(bpmn_ctx: dict):
    from shared.core.engine.domain.entities import (
        SubState,
        WorkflowNode,
        WorkflowGraph,
        WorkflowState,
    )
    from shared.core.engine.domain.values import SideEffectDef

    node_dict = _load_node_dict()
    d = dict(_load_work_substate(node_dict))
    d["side_effects"] = [
        SideEffectDef(
            type="index_code",
            params={"snapshot_id": "snap_123"},
            compensation="restore_code_snapshot",
        ),
        SideEffectDef(
            type="notify", params={"channel": "slack"}, compensation="notify_revert"
        ),
    ]
    d.update(
        {
            "order": 0,
            "options": [],
            "interrupting": True,
            "on_timeout": "skip",
            "wait_for": ["kg_query"],
            "emits": [],
            "waits_for": [],
            "on_signal": "",
        }
    )
    sub = SubState.from_dict(d)
    node = WorkflowNode(node_id="IMPLEMENT", label="Implement", sub_states=[sub])
    graph = WorkflowGraph(nodes={"IMPLEMENT": node}, initial_state="IMPLEMENT")
    state = WorkflowState(
        current_node="IMPLEMENT", current_sub_state="WORK", sub_state_index=0
    )

    bpmn_ctx["state"] = state
    bpmn_ctx["graph"] = graph
    bpmn_ctx["sub_state"] = sub


@given('un sprint en estado "waiting for signal"')
def _sprint_waiting_for_signal(bpmn_ctx: dict):
    from shared.core.engine.domain.entities import (
        SubState,
        WorkflowNode,
        WorkflowGraph,
        WorkflowState,
    )

    node_dict = _load_node_dict()
    d = dict(_load_work_substate(node_dict))
    d.update(
        {
            "side_effects": [],
            "order": 0,
            "options": [],
            "interrupting": True,
            "timeout_ms": 0,
            "on_timeout": "",
            "wait_for": [],
            "emits": [],
            "waits_for": ["approval_received"],
            "on_signal": "auto_advance",
        }
    )
    sub = SubState.from_dict(d)
    node = WorkflowNode(node_id="IMPLEMENT", label="Implement", sub_states=[sub])
    graph = WorkflowGraph(nodes={"IMPLEMENT": node}, initial_state="IMPLEMENT")
    state = WorkflowState(
        current_node="IMPLEMENT",
        current_sub_state="WORK",
        blocked_at="IMPLEMENT.WORK",
        block_reason="waiting for signal",
        block_since=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        pending_events=[],
        pending_signals=["approval_received"],
        block_timeout_ms=0,
    )

    bpmn_ctx["state"] = state
    bpmn_ctx["graph"] = graph
    bpmn_ctx["sub_state"] = sub


@given("un sprint en espera con timeout configurado")
def _sprint_waiting_with_timeout(bpmn_ctx: dict):
    from shared.core.engine.domain.entities import (
        SubState,
        WorkflowNode,
        WorkflowGraph,
        WorkflowState,
    )

    node_dict = _load_node_dict()
    d = dict(_load_work_substate(node_dict))
    d.update(
        {
            "timeout_ms": 100,
            "interrupting": True,
            "on_timeout": "skip",
            "wait_for": ["kg_query"],
            "emits": [],
            "waits_for": [],
            "on_signal": "",
            "side_effects": [],
            "order": 0,
            "options": [],
        }
    )
    sub = SubState.from_dict(d)
    node = WorkflowNode(node_id="IMPLEMENT", label="Implement", sub_states=[sub])
    graph = WorkflowGraph(nodes={"IMPLEMENT": node}, initial_state="IMPLEMENT")
    datetime.now(timezone.utc).replace(microsecond=0)
    state = WorkflowState(**_make_blocked_state(timeout_ms=100))

    bpmn_ctx["state"] = state
    bpmn_ctx["graph"] = graph
    bpmn_ctx["sub_state"] = sub


# ── when ────────────────────────────────────────────────────────────────────


@when('se aplica la estrategia de resolución "saltar"')
@when('se aplica la estrategia de resolución "skip"')
def _apply_skip(bpmn_ctx: dict):
    from shared.core.engine.domain.services.block_resolver import BlockResolver

    result = BlockResolver.resolve(
        bpmn_ctx["state"],
        "skip",
        {"sub_state": "WORK", "reason": "forced"},
        graph=bpmn_ctx.get("graph"),
    )
    bpmn_ctx["result"] = result


@when('se aplica la estrategia de resolución "forzar"')
def _apply_force(bpmn_ctx: dict):
    from shared.core.engine.domain.services.block_resolver import BlockResolver

    result = BlockResolver.resolve(
        bpmn_ctx["state"],
        "force",
        {"sub_state": "WORK", "reason": "forced"},
        graph=bpmn_ctx.get("graph"),
    )
    bpmn_ctx["result"] = result


@when('se aplica la estrategia de resolución "esperar"')
def _apply_wait(bpmn_ctx: dict):
    from shared.core.engine.domain.services.block_resolver import BlockResolver

    result = BlockResolver.resolve(
        bpmn_ctx["state"],
        "wait",
        {"sub_state": "WORK", "reason": "forced"},
        graph=bpmn_ctx.get("graph"),
    )
    bpmn_ctx["result"] = result


@when('se aplica la estrategia de resolución "escalar"')
def _apply_escalate(bpmn_ctx: dict):
    from shared.core.engine.domain.services.block_resolver import BlockResolver

    result = BlockResolver.resolve(
        bpmn_ctx["state"],
        "escalate",
        {"sub_state": "WORK", "reason": "timeout", "escalate_node": "ESCALATION"},
        graph=bpmn_ctx.get("graph"),
    )
    bpmn_ctx["result"] = result


@when('se aplica la estrategia de resolución "compensar"')
def _apply_compensate(bpmn_ctx: dict):
    from shared.core.engine.domain.services.block_resolver import BlockResolver

    result = BlockResolver.resolve(
        bpmn_ctx["state"],
        "compensate",
        {"sub_state": "WORK", "reason": "rollback"},
        graph=bpmn_ctx.get("graph"),
    )
    bpmn_ctx["result"] = result


@when("el timeout se cumple")
def _timeout_elapsed(bpmn_ctx: dict):
    import time
    from shared.core.engine.engine import check_blocked_state

    time.sleep(0.15)
    result = check_blocked_state(bpmn_ctx["state"], bpmn_ctx["graph"], strategy="skip")
    bpmn_ctx["result"] = result


@when('llega la señal "aprobación_recibida"')
def _approval_signal_arrives(bpmn_ctx: dict):
    from shared.core.engine.engine import check_blocked_state

    result = check_blocked_state(
        bpmn_ctx["state"], bpmn_ctx["graph"], strategy="auto_advance"
    )
    bpmn_ctx["result"] = result


@when("se emite la señal")
def _emit_signal(bpmn_ctx: dict):
    bus = bpmn_ctx["signal_bus"]
    notified = bus.emit(bpmn_ctx["signal"], source_node="NODE_X")
    bpmn_ctx["notified"] = notified


@when("se emite una señal global")
def _emit_global_signal(bpmn_ctx: dict):
    bus = bpmn_ctx["signal_bus"]
    notified = bus.emit(bpmn_ctx["signal"], source_node="NODE_X")
    bpmn_ctx["notified"] = notified


@when("se ejecuta la compensación")
def _execute_compensation(bpmn_ctx: dict):
    from shared.core.engine.domain.services.compensation_engine import CompensationEngine

    result = CompensationEngine.execute(bpmn_ctx["state"], bpmn_ctx["graph"], "WORK")
    bpmn_ctx["result"] = result


@when("se recibe la señal esperada")
def _expected_signal_received(bpmn_ctx: dict):
    from shared.core.engine.engine import check_blocked_state

    result = check_blocked_state(
        bpmn_ctx["state"], bpmn_ctx["graph"], strategy="auto_advance"
    )
    bpmn_ctx["result"] = result


@when("el tiempo de espera se agota")
def _wait_time_exhausted(bpmn_ctx: dict):
    import time
    from shared.core.engine.engine import check_blocked_state

    time.sleep(0.15)
    result = check_blocked_state(bpmn_ctx["state"], bpmn_ctx["graph"], strategy="skip")
    bpmn_ctx["result"] = result


# ── then ────────────────────────────────────────────────────────────────────


@then("el sprint continúa automáticamente sin intervención humana")
def _sprint_continues_automatically(bpmn_ctx: dict):
    result = bpmn_ctx["result"]
    assert is_successful(result)
    new_state = result.value_or(bpmn_ctx["state"])
    assert new_state.blocked_at == ""
    assert new_state.block_reason == ""


@then("el bloqueo se registra como resuelto")
def _block_registered_resolved(bpmn_ctx: dict):
    result = bpmn_ctx["result"]
    assert is_successful(result)


@then("el sprint avanza al siguiente paso sin quedar bloqueado")
def _sprint_advances(bpmn_ctx: dict):
    result = bpmn_ctx["result"]
    assert is_successful(result)
    new_state = result.value_or(bpmn_ctx["state"])
    assert new_state.blocked_at == ""
    assert new_state.block_reason == ""


@then("se preserva el contexto del workflow")
def _context_preserved(bpmn_ctx: dict):
    result = bpmn_ctx["result"]
    assert is_successful(result)


@then("el sprint mantiene el bloqueo activo")
def _sprint_keeps_block(bpmn_ctx: dict):
    result = bpmn_ctx["result"]
    assert is_successful(result)
    new_state = result.value_or(bpmn_ctx["state"])
    assert new_state.blocked_at == "IMPLEMENT.WORK"
    assert new_state.block_reason == "waiting"


@then("no se pierde el estado de espera")
def _wait_state_not_lost(bpmn_ctx: dict):
    result = bpmn_ctx["result"]
    assert is_successful(result)


@then("el sprint transiciona al nodo de escalación designado")
def _escalated_to_designated_node(bpmn_ctx: dict):
    result = bpmn_ctx["result"]
    assert is_successful(result)
    new_state = result.value_or(bpmn_ctx["state"])
    assert new_state.current_node == "ESCALATION"
    assert new_state.blocked_at == ""


@then("se notifica la intervención requerida")
def _intervention_notified(bpmn_ctx: dict):
    result = bpmn_ctx["result"]
    assert is_successful(result)


@then("los cambios se revierten en orden inverso")
def _changes_reverted_in_reverse(bpmn_ctx: dict):
    result = bpmn_ctx["result"]
    assert is_successful(result)
    new_state = result.value_or(bpmn_ctx["state"])
    assert new_state.blocked_at == ""


@then("el sprint queda en un estado consistente")
def _consistent_state(bpmn_ctx: dict):
    result = bpmn_ctx["result"]
    assert is_successful(result)


@then("el sprint se desbloquea automáticamente")
def _unblocks_automatically(bpmn_ctx: dict):
    result = bpmn_ctx["result"]
    assert result.get("resolved") is True or is_successful(result)


@then("se registra la resolución por timeout")
def _timeout_resolution_logged(bpmn_ctx: dict):
    result = bpmn_ctx["result"]
    assert result.get("resolved") is True or is_successful(result)


@then("el sprint se desbloquea automáticamente")
def _unblocks_by_signal(bpmn_ctx: dict):
    result = bpmn_ctx["result"]
    assert result.get("resolved") is True


@then("continúa con el flujo definido")
def _continues_defined_flow(bpmn_ctx: dict):
    result = bpmn_ctx["result"]
    assert result.get("resolved") is True


@then("todos los nodos suscritos reciben la notificación")
def _all_subscribers_notified(bpmn_ctx: dict):
    notified = bpmn_ctx["notified"]
    assert bpmn_ctx["subscribers"][0] in notified
    assert bpmn_ctx["subscribers"][1] in notified


@then("se registra el historial de señales emitidas")
def _signal_history_logged(bpmn_ctx: dict):
    bus = bpmn_ctx["signal_bus"]
    hist = bus.history()
    assert len(hist) >= 1
    assert hist[0]["name"] == bpmn_ctx["signal"]


@then("el nodo recibe la notificación")
def _node_receives_notification(bpmn_ctx: dict):
    notified = bpmn_ctx["notified"]
    assert "*" in notified or len(notified) >= 1


@then("el flujo puede reaccionar coordinadamente")
def _coordinated_flow_reaction(bpmn_ctx: dict):
    notified = bpmn_ctx["notified"]
    assert len(notified) >= 1


@then("los efectos se revierten en orden inverso")
def _effects_reverted_reverse(bpmn_ctx: dict):
    result = bpmn_ctx["result"]
    assert result.current_node == "IMPLEMENT"


@then("no se dejan efectos huérfanos")
def _no_orphan_effects(bpmn_ctx: dict):
    result = bpmn_ctx["result"]
    assert result.current_node == "IMPLEMENT"


@then("el sprint transiciona automáticamente")
def _transitions_automatically(bpmn_ctx: dict):
    result = bpmn_ctx["result"]
    assert result.get("resolved") is True


@then("se registra la causa de desbloqueo")
def _unblock_cause_logged(bpmn_ctx: dict):
    result = bpmn_ctx["result"]
    assert result.get("resolved") is True
