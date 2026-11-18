"""BDD: el router devuelve un CONTRATO, no solo prosa.

Medido 2026-11-18: para cruzar un gate el agente pagaba tres llamadas
(`submit` → `advance` → `transition`) y descubría el protocolo cuando algo
fallaba. Además `required_fields` mostraba `[]` mientras `advance` reclamaba
`decision`, y el payload había que deducirlo del prompt.

Contrato nuevo en `workflow_state`, `submit`, `advance` y `transition`:
  * `expected_payload`: tipo/obligatoriedad/enum de cada campo.
  * `next_action`: la llamada exacta que desbloquea (submit | advance | transition).
  * `build`: si el proceso corre código anterior al disco.
"""
from __future__ import annotations

import time
from types import SimpleNamespace

from fast_mcp.features.workflow.application.bundle import EngineBundle
from fast_mcp.features.workflow.application.orchestrator import (
    OrchestrationService,
    declared_required_fields,
    decision_map_for,
    expected_payload,
    extract_user_message,
    missing_declared_fields,
    prompt_for_view,
    next_action_for,
)
from fast_mcp.features.workflow.domain.entities import (
    SubState,
    SubStateData,
    WorkflowGraph,
    WorkflowNode,
    WorkflowState,
)
from fast_mcp.features.workflow.domain.enums import ExecutionType
from fast_mcp.shared_kernel.build_info import build_info


def _sub(key="STEP", **over):
    base = {"key": key, "execution": ExecutionType.MANUAL}
    base.update(over)
    return SubState(**base)


def _graph(*subs):
    return WorkflowGraph(nodes={"N": WorkflowNode(node_id="N", sub_states=list(subs))})


def _state(sub_key="STEP", index=0, fields=None, context=None):
    return WorkflowState(
        current_node="N",
        current_sub_state=sub_key,
        sub_state_index=index,
        sub_state_data={sub_key: SubStateData(fields=fields or {})},
        context=context or {},
    )


# ── campos requeridos: una sola fuente ──────────────────────────────────

def test_required_viene_de_required_fields_si_existe():
    sub = _sub(required_fields=["facts"], input_schema={"required": ["otro"]})

    assert declared_required_fields(sub) == ["facts"]


def test_required_cae_al_input_y_luego_al_output():
    assert declared_required_fields(_sub(input_schema={"required": ["plan"]})) == ["plan"]
    assert declared_required_fields(_sub(output_schema={"required": ["decision"]})) == ["decision"]


def test_sin_esquemas_no_hay_requeridos():
    assert declared_required_fields(_sub()) == []


def test_missing_usa_los_campos_declarados():
    sub = _sub(input_schema={"required": ["plan", "deviations"]})
    state = _state(fields={"plan": ["a"]})

    assert missing_declared_fields(state, sub) == ["deviations"]


def test_un_contenedor_vacio_cuenta_como_faltante():
    sub = _sub(input_schema={"required": ["plan"]})

    assert missing_declared_fields(_state(fields={"plan": []}), sub) == ["plan"]


# ── contrato de payload ─────────────────────────────────────────────────

def test_expected_payload_trae_tipo_enum_y_required():
    sub = _sub(
        input_schema={"type": "object", "properties": {"plan": {"type": "array"}}},
        output_schema={
            "type": "object",
            "properties": {
                "decision": {"type": "string", "enum": ["approve", "reject"], "description": "veredicto"}
            },
            "required": ["decision"],
        },
    )

    payload = expected_payload(sub)

    assert payload["plan"]["type"] == "array"
    assert payload["decision"]["enum"] == ["approve", "reject"]
    assert payload["decision"]["required"] is True


# ── próxima acción ──────────────────────────────────────────────────────

def test_next_action_pide_submit_si_faltan_campos():
    graph = _graph(_sub("A", execution=ExecutionType.USER_INPUT, output_schema={"required": ["decision"]}), _sub("B"))

    action = next_action_for(_state("A"), graph)

    assert action["tool"] == "submit"
    assert action["missing"] == ["decision"]
    assert action["payload"] == {"decision": "<decision>"}


def test_next_action_pide_advance_en_el_medio():
    graph = _graph(_sub("A", execution=ExecutionType.AUTO), _sub("B"))

    assert next_action_for(_state("A"), graph)["tool"] == "advance"


def test_next_action_pide_transition_en_el_borde():
    graph = _graph(_sub("A", execution=ExecutionType.AUTO))
    from fast_mcp.features.workflow.domain.entities import Gate

    graph.edges = [Gate(name="done", source_node="N", target_node="SPRINT")]
    graph.edges_by_source = {"N": list(graph.edges)}

    action = next_action_for(_state("A"), graph)

    assert action["tool"] == "transition"
    assert action["gates"] == ["done"]


def test_next_action_none_si_el_estado_no_existe():
    assert next_action_for(_state("NO_EXISTE"), _graph(_sub("A"))) is None


# ── submit/advance devuelven next_action ────────────────────────────────

def test_advance_devuelve_next_action():
    graph = _graph(
        _sub("A", execution=ExecutionType.LLM_TASK, action="a1"),
        _sub("B", execution=ExecutionType.USER_INPUT, output_schema={"required": ["decision"]}),
    )
    state = _state("A", fields={"x": 1})
    orch = OrchestrationService(
        bundle=EngineBundle(
            workflow_store=None, workflow_io=None, tracer=None, side_effects=None, chain_dir="/tmp/x"
        )
    )
    orch._load_graph = lambda: graph
    orch._load_state = lambda: state
    orch._save_state = lambda s: True
    orch._trace_submit = lambda *a, **k: None
    orch._execute_action = lambda *a, **k: ({"action": "a1", "data": {}}, True, "")

    result = orch.advance(ctx=None, fields={})

    assert result["ok"] is True
    assert result["next_action"]["tool"] == "submit"
    assert result["next_action"]["missing"] == ["decision"]


# ── handshake ───────────────────────────────────────────────────────────

def test_build_info_tiene_las_claves_esperadas():
    info = build_info(ttl=0)

    assert set(info) == {
        "git_sha",
        "process_started_at",
        "newest_source_mtime",
        "stale",
    }
    assert isinstance(info["stale"], bool)
    assert isinstance(info["git_sha"], str)


def test_build_info_se_cachea():
    first = build_info(ttl=60)
    second = build_info(ttl=60)

    assert first == second
    assert build_info(ttl=0)["newest_source_mtime"] == first["newest_source_mtime"]


def test_transition_limpia_la_data_del_nodo_destino():
    """Un gate de usuario no se puede cruzar con la decisión de la pasada anterior.

    Medido 2026-11-18: DESIGN_REVIEW seguía con el `approve` de SPEC-002 y el
    `advance` de SPEC-001 lo daba por hecho sin preguntar nada.
    """
    from fast_mcp.features.workflow.domain.entities import Gate

    source = WorkflowNode(node_id="SPRINT", sub_states=[_sub("ACTIVE")])
    target = WorkflowNode(
        node_id="DESIGN",
        sub_states=[
            _sub("GATHER", execution=ExecutionType.AUTO),
            _sub("REVIEW", execution=ExecutionType.USER_INPUT),
        ],
    )
    graph = WorkflowGraph(nodes={"SPRINT": source, "DESIGN": target}, initial_state="SPRINT")
    gate = Gate(name="start_task", source_node="SPRINT", target_node="DESIGN")
    graph.edges = [gate]
    graph.edges_by_source = {"SPRINT": [gate]}

    state = WorkflowState(
        current_node="SPRINT",
        current_sub_state="ACTIVE",
        sub_state_index=0,
        sub_state_data={
            "SPRINT.ACTIVE": SubStateData(fields={"status": "active"}),
            "GATHER": SubStateData(fields={"facts": ["viejo"]}),
            "REVIEW": SubStateData(fields={"decision": "approve", "evidence": "de otra spec"}),
            "IMPLEMENT.WORK": SubStateData(fields={"loop_verification": True}),
        },
    )
    captured: dict = {}
    orch = OrchestrationService(
        bundle=EngineBundle(
            workflow_store=None, workflow_io=None, tracer=None, side_effects=None, chain_dir="/tmp/x"
        )
    )
    orch._load_graph = lambda: graph
    orch._load_state = lambda: state
    orch._save_state = lambda s: captured.setdefault("state", s) is not None or True
    orch._trace_transition = lambda *a, **k: None

    result = orch.transition(ctx=None, gate="start_task")

    assert result["ok"] is True, result
    final = captured["state"]
    assert "GATHER" not in final.sub_state_data
    assert "REVIEW" not in final.sub_state_data
    # La data del nodo ORIGEN y la de otros nodos se conserva.
    assert "SPRINT.ACTIVE" in final.sub_state_data
    assert "IMPLEMENT.WORK" in final.sub_state_data


# ── decision_map y user_message ─────────────────────────────────────────

def test_decision_map_sale_del_contrato_del_gate():
    from fast_mcp.features.workflow.domain.entities import Gate

    gates = [
        Gate(
            name="design_approved",
            source_node="DESIGN",
            target_node="ANALYZE",
            contract={"pre": "ckg_has_decision('approve')"},
        ),
        Gate(
            name="design_rejected",
            source_node="DESIGN",
            target_node="REFINE",
            contract={"pre": "ckg_has_decision('reject')"},
        ),
        Gate(name="design_needs_refine", source_node="DESIGN", target_node="REFINE"),
    ]

    mapping = decision_map_for(gates)

    assert mapping["approve"] == "design_approved"
    assert mapping["reject"] == "design_rejected"
    # Sin contrato, el nombre del gate aporta la decisión.
    assert mapping["refine"] == "design_needs_refine"


def test_decision_map_ignora_gates_que_no_son_decision():
    from fast_mcp.features.workflow.domain.entities import Gate

    assert decision_map_for([Gate(name="start_task", source_node="A", target_node="B")]) == {}


def test_user_message_extrae_las_opciones_y_corta_los_comandos():
    template = (
        "REPORTE\n\n"
        "Options:\n"
        "    . proceed -> accepted\n"
        "    . refine  -> needs refinement\n\n"
        "  walker <project>/submit '{\"decision\":\"proceed\"}'\n"
        "texto que no debe aparecer"
    )

    out = extract_user_message(template, "IMPLEMENT.REPORT_GATE")

    assert out["position"] == "IMPLEMENT.REPORT_GATE"
    assert "proceed -> accepted" in out["text"]
    assert "refine" in out["text"]
    assert "walker" not in out["text"]
    assert "no debe aparecer" not in out["text"]


def test_user_message_sin_opciones_es_none():
    assert extract_user_message("solo instrucciones", "X") is None


# ── submit autotransiciona ──────────────────────────────────────────────

def test_submit_cruza_el_gate_cuando_la_decision_mapea():
    from fast_mcp.features.workflow.domain.entities import Gate

    n1 = WorkflowNode(
        node_id="VERIFY",
        sub_states=[
            _sub("PO_ACCEPT", execution=ExecutionType.USER_INPUT, output_schema={"required": ["decision"]})
        ],
    )
    n2 = WorkflowNode(node_id="SPRINT", sub_states=[_sub("ACTIVE")])
    graph = WorkflowGraph(nodes={"VERIFY": n1, "SPRINT": n2}, initial_state="VERIFY")
    gate = Gate(
        name="task_complete",
        source_node="VERIFY",
        target_node="SPRINT",
        source_sub_state="PO_ACCEPT",
        contract={"pre": "ckg_has_decision('accept')"},
    )
    graph.edges = [gate]
    graph.edges_by_source = {"VERIFY": [gate]}

    state = WorkflowState(
        current_node="VERIFY", current_sub_state="PO_ACCEPT", sub_state_index=0
    )
    saved: dict = {}
    orch = OrchestrationService(
        bundle=EngineBundle(
            workflow_store=None, workflow_io=None, tracer=None, side_effects=None, chain_dir="/tmp/x"
        )
    )
    orch._load_graph = lambda: graph
    orch._load_state = lambda: saved.get("state", state)
    orch._save_state = lambda s: saved.setdefault("state", s) is not None or True
    orch._trace_submit = lambda *a, **k: None
    orch._trace_transition = lambda *a, **k: None

    result = orch.submit(ctx=None, fields={"decision": "accept"})

    assert result["ok"] is True, result
    assert result.get("submitted") is True
    assert result.get("to_node") == "SPRINT", result
    assert result.get("position") == "SPRINT.ACTIVE"


# ── vista light/full (presupuesto de atención) ─────────────────────────

def test_prompt_for_view_light_quita_data():
    prompt = {"template_id": "x", "template": "hola", "data": {"a": 1}}

    assert "data" not in prompt_for_view(prompt, "light")
    assert prompt_for_view(prompt, "full")["data"] == {"a": 1}
    assert prompt_for_view(None, "light") is None


def _orch_for_view():
    node = WorkflowNode(
        node_id="N",
        sub_states=[_sub("S", execution=ExecutionType.USER_INPUT)],
    )
    graph = WorkflowGraph(nodes={"N": node}, initial_state="N")
    state = WorkflowState(
        current_node="N",
        current_sub_state="S",
        sub_state_index=0,
        history=["A.B", "C.D"],
        node_executions=[
            SimpleNamespace(
                node="N", sub_state="S", execution_type="auto", action="a", task_ok=True, gate="", error=None, duration_ms=1
            )
        ],
    )
    orch = OrchestrationService(
        bundle=EngineBundle(
            workflow_store=None, workflow_io=None, tracer=None, side_effects=None, chain_dir="/tmp/x"
        )
    )
    orch._load_graph = lambda: graph
    orch._load_state = lambda: state
    return orch


def test_help_light_no_manda_data_ni_historial():
    orch = _orch_for_view()
    payload = orch.help(ctx=None, view="light")

    assert "history" not in payload
    assert "gates_detail" not in payload
    assert payload["next_action"] is not None


def test_help_full_agrega_historial_y_gates_detail():
    orch = _orch_for_view()
    payload = orch.help(ctx=None, view="full")

    assert "history" in payload
    assert "gates_detail" in payload


def test_status_light_no_manda_ejecuciones_ni_historial():
    orch = _orch_for_view()
    payload = orch.status(ctx=SimpleNamespace(task_id="", session_id="s"), view="light")

    assert "node_executions" not in payload
    assert "history" not in payload
    # `available_gates` conserva su forma (dicts con target y descripción).
    assert isinstance(payload["available_gates"], list)
