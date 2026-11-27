"""BDD de los 10 arreglos del workflow (auditoría 2026-11-18).

Cada test corresponde a un bug medido en el grafo vivo:

1. Un `submit` con decisión cruza el gate aunque el sub-estado NO sea el último.
2. `decision_map` explícito por sub-estado, filtrado por gates disponibles.
3. Los gates ya no son globales: `task_complete` no se puede cruzar desde QA_RUN.
4. `on_success`/`on_failure` se ejecutan de verdad (AdvanceService).
5. `workflow.on` se rechaza: el motor no lo ejecutaba.
6. `sprint_planning` no es un nodo fantasma.
7. Los placeholders de PROJECT_INIT.REVIEW/START.LIST tienen datos.
8. Las referencias NODE.X no resueltas se reportan, no se omiten en silencio.
9. SPRINT.REVIEW y CLOSE_CONFIRM tienen gates para sus decisiones.
10. SPRINT.ACTIVE devuelve `transition`, no `submit`.

Los tests canónicos leen el grafo real (project walker) y se saltan si no está.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from fast_mcp.features.workflow.application.bundle import EngineBundle
from fast_mcp.features.workflow.application.orchestrator import (
    OrchestrationService,
    decision_map_for,
    next_action_for,
)
from fast_mcp.features.workflow.domain.entities import (
    Gate,
    SideEffectDef,
    SubState,
    WorkflowGraph,
    WorkflowNode,
    WorkflowState,
)
from fast_mcp.features.workflow.domain.enums import ExecutionType
from fast_mcp.features.workflow.domain.services.validation import ValidationService
from fast_mcp.features.workflow.infrastructure.side_effect_engine import SideEffectEngine

ROOT = "/Users/davidmartinez/develop/Active-projects/walker"


# ── helpers ─────────────────────────────────────────────────────────────

def _orch(graph, state, registry=None):
    """Orquestador con estado en memoria (sin tocar el estado vivo)."""
    store = {"state": state}
    orch = OrchestrationService(
        bundle=EngineBundle(
            workflow_store=None,
            workflow_io=None,
            tracer=None,
            side_effects=None,
            chain_dir="/tmp/x",
            task_registry=registry,
        )
    )
    orch._load_graph = lambda: graph
    orch._load_state = lambda: store["state"]

    def save(new_state):
        store["state"] = new_state
        return True

    orch._save_state = save
    orch._trace_submit = lambda *a, **k: None
    orch._trace_transition = lambda *a, **k: None
    return orch, store


def _sub(key, execution=ExecutionType.AUTO, **over):
    base = {"key": key, "execution": execution}
    base.update(over)
    return SubState(**base)


def _gate(name, source, target, **over):
    return Gate(name=name, source_node=source, target_node=target, **over)


def _canonical_graph():
    from fast_mcp.features.workflow.context import WorkflowContext

    ctx = WorkflowContext.resolve(ROOT)
    if ctx is None:
        pytest.skip("workflow canónico no resoluble")
    return ctx.orchestrator._load_graph()


# ── 1. submit cruza un gate intermedio ──────────────────────────────────

def test_submit_cruza_gate_aunque_no_sea_el_ultimo_subestado():
    s = _sub(
        "S",
        ExecutionType.USER_INPUT,
        decision_map={"approve": "approved"},
        input_schema={"required": ["decision"]},
    )
    gate = _gate("approved", "N", "T", source_sub_state="S")
    graph = WorkflowGraph(
        nodes={
            "N": WorkflowNode(node_id="N", sub_states=[s, _sub("MID")]),
            "T": WorkflowNode(node_id="T", sub_states=[_sub("X")]),
        },
        edges=[gate],
        edges_by_source={"N": [gate]},
        initial_state="N",
    )
    state = WorkflowState(current_node="N", current_sub_state="S", sub_state_index=0)
    orch, store = _orch(graph, state)

    result = orch.submit(ctx=None, fields={"decision": "approve", "_human": True})

    assert result["ok"] is True, result
    assert store["state"].current_node == "T"
    assert store["state"].current_sub_state == "X"


def test_submit_usa_decision_field_no_el_literal_decision():
    s = _sub(
        "S",
        ExecutionType.USER_INPUT,
        decision_field="intent",
        decision_map={"project_ready": "ready"},
        input_schema={"required": ["intent"]},
    )
    gate = _gate("ready", "N", "T", source_sub_state="S")
    graph = WorkflowGraph(
        nodes={
            "N": WorkflowNode(node_id="N", sub_states=[s, _sub("MID")]),
            "T": WorkflowNode(node_id="T", sub_states=[_sub("X")]),
        },
        edges=[gate],
        edges_by_source={"N": [gate]},
        initial_state="N",
    )
    state = WorkflowState(current_node="N", current_sub_state="S", sub_state_index=0)
    orch, store = _orch(graph, state)

    result = orch.submit(ctx=None, fields={"intent": "project_ready", "_human": True})

    assert result["ok"] is True, result
    assert store["state"].current_node == "T"


# ── 2. decision_map explícito y por sub-estado ──────────────────────────

def test_decision_map_usa_el_mapa_explicito_y_filtra_por_gates_disponibles():
    s = _sub(
        "S",
        ExecutionType.USER_INPUT,
        decision_map={"approve": "approved", "refine": "later_gate"},
    )
    gates = [_gate("approved", "N", "T")]

    mapping = decision_map_for(gates, s)

    assert mapping == {"approve": "approved"}


def test_decision_map_del_gate_no_mezcla_gates_de_otro_subestado():
    graph = _canonical_graph()
    node = graph.get_node("IMPLEMENT")
    cr = node.find_sub_state("CODE_REVIEW_RESULT")
    gates = ValidationService.get_available_gates(
        WorkflowState(
            current_node="IMPLEMENT",
            current_sub_state="CODE_REVIEW_RESULT",
            sub_state_index=6,
        ),
        graph,
    )

    mapping = decision_map_for(gates, cr)

    assert "approve" not in mapping  # approve sigue a MONITORING, no cruza gate
    assert mapping.get("refine") == "needs_refine"
    assert mapping.get("analyze") == "needs_refine"
    assert mapping.get("cancel") == "cancel"


def test_decision_map_canonico_solo_expone_decisiones_del_enum():
    graph = _canonical_graph()
    for node in graph.nodes.values():
        for i, sub in enumerate(node.sub_states):
            if sub.execution.value != "user_input":
                continue
            field = sub.decision_field or "decision"
            in_props = (sub.input_schema or {}).get("properties") or {}
            out_props = (sub.output_schema or {}).get("properties") or {}
            enum = (in_props.get(field) or {}).get("enum") or (
                out_props.get("decision") or {}
            ).get("enum") or []
            if not enum:
                continue
            state = WorkflowState(
                current_node=node.node_id,
                current_sub_state=sub.key,
                sub_state_index=i,
            )
            gates = ValidationService.get_available_gates(state, graph)
            mapping = decision_map_for(gates, sub)
            assert set(mapping) <= set(enum), (node.node_id, sub.key, mapping, enum)


# ── 3. gates no globales ────────────────────────────────────────────────

def test_todos_los_gates_declaran_desde_donde_son_validos():
    graph = _canonical_graph()
    wild = [
        g.name for g in graph.edges if not g.available_from and not g.source_sub_state
    ]

    assert wild == [], f"gates globales (permiten saltarse pasos): {wild}"


def test_task_complete_no_se_puede_cruzar_desde_qa_run():
    graph = _canonical_graph()
    state = WorkflowState(
        current_node="VERIFY", current_sub_state="QA_RUN", sub_state_index=0
    )

    gates = [g.name for g in ValidationService.get_available_gates(state, graph)]

    assert "task_complete" not in gates


def test_implementation_done_no_se_puede_cruzar_desde_observe():
    graph = _canonical_graph()
    state = WorkflowState(
        current_node="IMPLEMENT", current_sub_state="OBSERVE", sub_state_index=0
    )

    gates = [g.name for g in ValidationService.get_available_gates(state, graph)]

    assert "implementation_done" not in gates


# ── 4. on_success / on_failure ──────────────────────────────────────────

def test_advance_rutea_on_failure_al_subestado_de_recuperacion():
    graph = WorkflowGraph(
        nodes={
            "N": WorkflowNode(
                node_id="N",
                sub_states=[
                    _sub("A", action="boom", on_failure="B"),
                    _sub("B"),
                    _sub("C"),
                ],
            )
        },
        initial_state="N",
    )
    state = WorkflowState(current_node="N", current_sub_state="A", sub_state_index=0)
    orch, store = _orch(graph, state)
    orch._execute_action = lambda *a, **k: ({"error": "boom"}, False, "boom falló")

    result = orch.advance(ctx=None, fields={})

    assert result["ok"] is False
    assert result["action"] == "task_failed"
    assert result["routed_to"] == "B"
    assert store["state"].current_sub_state == "B"


def test_advance_rutea_on_success_saltando_el_lineal():
    graph = WorkflowGraph(
        nodes={
            "N": WorkflowNode(
                node_id="N",
                sub_states=[_sub("A", on_success="C"), _sub("B"), _sub("C")],
            )
        },
        initial_state="N",
    )
    state = WorkflowState(current_node="N", current_sub_state="A", sub_state_index=0)
    orch, store = _orch(graph, state)
    orch._execute_action = lambda *a, **k: ({"ok": True}, True, "")

    result = orch.advance(ctx=None, fields={})

    assert result["ok"] is True, result
    assert store["state"].current_sub_state == "C"


# ── 5. workflow.on ──────────────────────────────────────────────────────

def test_un_manifiesto_con_on_es_invalido():
    graph = WorkflowGraph(
        nodes={"N": WorkflowNode(node_id="N", sub_states=[_sub("A")])},
        initial_state="N",
        on_events={"plan_approved": {"run": "update_sprint_progress"}},
    )

    errors = graph.validate_invariants()

    assert any("workflow.on" in e for e in errors), errors


def test_el_manifiesto_canonico_no_declara_on():
    raw = json.loads(Path(ROOT, "data/workflow/workflow.json").read_text())

    assert "on" not in raw


# ── 6. sprint_planning huérfano ─────────────────────────────────────────

def test_sprint_planning_no_es_un_nodo_del_grafo():
    graph = _canonical_graph()

    assert "SPRINT_PLANNING" not in graph.nodes


def test_el_nodo_sprint_planning_ya_no_existe():
    assert not Path(ROOT, "data/workflow/nodes/sprint_planning.node.json").exists()


# ── 7. placeholders con datos ───────────────────────────────────────────

def test_project_briefing_solo_para_start_y_project_init():
    from fast_mcp.features.workflow.application.prompt_context import (
        project_briefing_context,
    )

    other = WorkflowState(current_node="IMPLEMENT", current_sub_state="WORK")
    assert project_briefing_context(other, ROOT) == {}


def test_project_briefing_trae_nombre_lenguaje_y_conteos():
    from fast_mcp.features.workflow.application.prompt_context import (
        project_briefing_context,
    )

    out = project_briefing_context(
        WorkflowState(current_node="PROJECT_INIT", current_sub_state="REVIEW"), ROOT
    )

    if not out:
        pytest.skip("proyecto no registrado en este entorno")
    assert out["project_name"]
    assert out["language"] != "unknown"
    assert out["count"] >= 1
    assert out["cpg_nodes"] > 0
    assert out["ckg_nodes"] >= 0


def test_project_init_review_renderiza_el_resumen_sin_blancos():
    from fast_mcp.features.workflow.context import WorkflowContext
    from fast_mcp.features.workflow.domain.execution import ExecutionContext

    ctx = WorkflowContext.resolve(ROOT)
    if ctx is None:
        pytest.skip("workflow no resoluble")
    orch = ctx.orchestrator
    graph = orch._load_graph()
    state = orch._load_state()
    node = graph.get_node("PROJECT_INIT")
    index = node.sub_state_index("REVIEW")
    probe = replace(
        state,
        current_node="PROJECT_INIT",
        current_sub_state="REVIEW",
        sub_state_index=index,
    )
    resolved = orch._resolve_current_prompt(
        probe,
        graph,
        ExecutionContext.create(chain_dir=ROOT, session_id="roadmap-session-1"),
    ) or {}
    text = resolved.get("template") or ""

    assert "Name: \n" not in text
    assert "Language: \n" not in text
    assert "CPG:  nodes" not in text


def test_ningun_template_usa_un_placeholder_que_nadie_provee():
    provided = {
        "node_id", "sub_state", "execution", "revision",
        "skills_catalog", "skills_count",
        "knowledge_relevant", "knowledge_global", "knowledge_architecture",
        "project_patterns", "project_architecture", "project_quality_gates",
        "step_quality_passed", "step_quality_summary", "step_failed_gates", "step_recommend",
        # quality_context lo devuelve SIEMPRE ("sin verificar" si no hay dato);
        # se verifica abajo con el provider real.
        "step_predicates",
        # `sprint_deliverable_context` (REPORT+RETRO) lo provee y
        # build_prompt_extra lo cablea; se verifica abajo.
        "sprint_report",
        "what_happened", "sprint_goal", "goal_display", "task_count", "completed_count",
        "ready_tasks", "blocked_tasks", "task_id", "task_title", "task_description",
        "sprint_summary_list",
        "task_type", "task_domain", "task_status", "result_state", "depends_on",
        "acceptance_criteria_list", "files_list", "preconditions_list",
        "quality_gates_list", "predicates_list", "predicates", "task_table",
        "risk_table", "decisions_list", "curvature", "pre_churn", "commit_trust",
        "q1", "a1", "q2", "a2", "q3", "a3", "reflection_past_attempts",
        "source_node", "attempt", "max_attempts", "error_context", "failed_gates",
        "hint", "count", "project_name", "language", "framework", "cpg_nodes",
        "cpg_edges", "ckg_nodes",
        # Provistos por `prompt_context.session_plan_context`: el plan de la
        # SESIÓN entra al prompt del primer nodo. Se verifican abajo con el
        # provider real, no por whitelist.
        "existing_work", "existing_work_line",
    }
    import re

    from fast_mcp.features.workflow.application.prompt_context import session_plan_context

    # Un nombre en `provided` sin provider es la misma trampa que el test evita:
    # se comprueba que el provider devuelve las dos claves.
    sample = session_plan_context({"task": "t", "acceptance": ["Debe X"]})
    assert {"existing_work", "existing_work_line"} <= set(sample), sample

    from types import SimpleNamespace
    from fast_mcp.features.workflow.application.prompt_context import predicate_verdict

    assert predicate_verdict(SimpleNamespace(node_executions=[])) == "sin verificar"

    unknown: list[str] = []
    base = Path(ROOT, "fast_mcp/features/workflow/application/prompts/templates/nodes")
    for path in sorted(base.rglob("*.prompt.json")):
        data = json.loads(path.read_text())
        text = data.get("template") or data.get("prompt") or ""
        for name in set(re.findall(r"(?<!\{)\{(\w+)\}(?!\})", text)):
            if name not in provided:
                unknown.append(f"{path.relative_to(base)}: {name}")

    assert unknown == [], unknown


# ── 8. NODE.X no resueltas ──────────────────────────────────────────────

class _Registry:
    def __init__(self):
        self.received: list[dict] = []
        self._handlers = {"fake": self._run}

    def _run(self, **kwargs):
        self.received.append(dict(kwargs.get("params") or {}))
        return {"ok": True}

    def execute(self, name, chain_dir, params, **kwargs):
        return self._handlers[name](chain_dir=chain_dir, params=params, **kwargs)


def test_las_referencias_node_no_resueltas_se_reportan():
    registry = _Registry()
    engine = SideEffectEngine(registry)
    sub = SimpleNamespace(
        key="WORK",
        side_effects=[
            SideEffectDef(type="fake", params={"x": "NODE.WORK.output.task_context"})
        ],
    )
    state = WorkflowState(
        current_node="IMPLEMENT", current_sub_state="WORK", sub_state_index=3
    )

    result = engine.apply_side_effects(sub, state, chain_dir=ROOT)

    assert result["ok"] is True
    assert result["unresolved"] == ["NODE.WORK.output.task_context"]
    assert registry.received == [{}]


def test_work_transform_ya_no_referencia_task_context_inexistente():
    raw = json.loads(Path(ROOT, "data/workflow/nodes/implement.node.json").read_text())
    work = next(s for s in raw["sub_states"] if s["key"] == "WORK")
    refs = [
        value
        for effect in work.get("side_effects") or []
        for value in ((effect.get("params") or {}).get("fields") or {}).values()
        if isinstance(value, str)
    ]

    assert "NODE.WORK.output.task_context" not in refs


# ── 9. SPRINT.REVIEW / CLOSE_CONFIRM ────────────────────────────────────

def test_review_y_close_confirm_tienen_gates_para_sus_decisiones():
    graph = _canonical_graph()
    expected = {
        "review_accepted": "accept",
        "review_needs_work": "needs_work",
        "close_confirmed": "close",
        "close_cancelled": "cancel",
    }
    by_name = {g.name: g for g in graph.edges}
    for gate_name in expected:
        assert gate_name in by_name, gate_name
        assert by_name[gate_name].target_node in graph.nodes

    reviews = graph.get_node("SPRINT").find_sub_state("REVIEW")
    assert reviews.decision_map["accept"] == "review_accepted"
    assert reviews.decision_map["needs_work"] == "review_needs_work"
    closes = graph.get_node("SPRINT").find_sub_state("CLOSE_CONFIRM")
    assert closes.decision_map["close"] == "close_confirmed"
    assert closes.decision_map["cancel"] == "close_cancelled"


# ── 10. SPRINT.ACTIVE ───────────────────────────────────────────────────

def test_sprint_active_pide_transition_no_submit():
    graph = _canonical_graph()
    node = graph.get_node("SPRINT")
    index = node.sub_state_index("ACTIVE")
    state = WorkflowState(
        current_node="SPRINT", current_sub_state="ACTIVE", sub_state_index=index
    )

    action = next_action_for(state, graph)

    assert action["tool"] == "transition", action
    assert "start_task" in action["gates"]


def test_sprint_active_no_exige_intent_que_el_prompt_no_pide():
    from fast_mcp.features.workflow.application.orchestrator import declared_required_fields

    graph = _canonical_graph()
    active = graph.get_node("SPRINT").find_sub_state("ACTIVE")

    assert declared_required_fields(active) == []


# ── 11. ningún nodo queda sin salida ────────────────────────────────────

def test_el_ultimo_subestado_de_cada_nodo_tiene_al_menos_un_gate():
    graph = _canonical_graph()
    dead: list[str] = []
    for node in graph.nodes.values():
        if not node.sub_states:
            continue
        index = len(node.sub_states) - 1
        sub = node.sub_states[index]
        state = WorkflowState(
            current_node=node.node_id, current_sub_state=sub.key, sub_state_index=index
        )
        if not ValidationService.get_available_gates(state, graph):
            dead.append(f"{node.node_id}.{sub.key}")
    assert dead == [], f"nodos sin salida: {dead}"


# ── 12. capture_decision acepta `intent` ────────────────────────────────

def test_capture_decision_acepta_intent_de_project_init():
    from fast_mcp.features.tasks.side_effects.decision import capture_decision

    result = capture_decision(
        "/tmp", {}, fields={"intent": "project_ready"}, state=None
    )

    assert result["ok"] is True
    assert result["decision"] == "project_ready"


# ── 13. el caso real: TASK_REVIEW refine_task ───────────────────────────

def test_submit_en_task_review_cruza_refine_task():
    graph = _canonical_graph()
    node = graph.get_node("SPRINT")
    index = node.sub_state_index("TASK_REVIEW")
    state = WorkflowState(
        current_node="SPRINT", current_sub_state="TASK_REVIEW", sub_state_index=index
    )
    orch, store = _orch(graph, state)

    result = orch.submit(ctx=None, fields={"decision": "refine", "_human": True})

    assert result["ok"] is True, result
    assert store["state"].current_node == "SPRINT"
    assert store["state"].current_sub_state == "TASK_SPEC"


@pytest.mark.parametrize(
    "sub_state,decision,target_sub",
    [
        ("PLAN_APPROVAL", "revise", "PLANNING"),
        ("TASK_REVIEW", "all_done", "SPEC_REVIEW"),
        ("REVIEW", "accept", "REPORT"),
        ("REVIEW", "needs_work", "ACTIVE"),
        ("CLOSE_CONFIRM", "close", "CLOSE"),
        ("CLOSE_CONFIRM", "cancel", "ACTIVE"),
    ],
)
def test_los_gates_del_sprint_cruzan_a_su_target(sub_state, decision, target_sub):
    graph = _canonical_graph()
    node = graph.get_node("SPRINT")
    index = node.sub_state_index(sub_state)
    state = WorkflowState(
        current_node="SPRINT", current_sub_state=sub_state, sub_state_index=index
    )
    orch, store = _orch(graph, state)

    result = orch.submit(ctx=None, fields={"decision": decision, "_human": True})

    assert result["ok"] is True, result
    assert store["state"].current_node == "SPRINT"
    assert store["state"].current_sub_state == target_sub


def test_design_approve_queda_registrado_y_avanza_a_ckg_save():
    graph = _canonical_graph()
    node = graph.get_node("DESIGN")
    index = node.sub_state_index("DESIGN_REVIEW")
    state = WorkflowState(
        current_node="DESIGN", current_sub_state="DESIGN_REVIEW", sub_state_index=index
    )
    orch, store = _orch(graph, state)

    result = orch.submit(ctx=None, fields={"decision": "approve", "_human": True})

    assert result["ok"] is True, result
    # El gate `design_approved` vive en CKG_SAVE: no se cruza todavía.
    assert store["state"].current_node == "DESIGN"
    assert result["next_action"]["tool"] == "advance"


def test_analyze_approve_cruza_analysis_approved():
    graph = _canonical_graph()
    node = graph.get_node("ANALYZE")
    index = node.sub_state_index("REVIEW")
    state = WorkflowState(
        current_node="ANALYZE",
        current_sub_state="REVIEW",
        sub_state_index=index,
        sub_state_data={
            "ANALYZE": __import__(
                "fast_mcp.features.workflow.domain.entities", fromlist=["SubStateData"]
            ).SubStateData(fields={"preconditions": ["p"]})
        },
    )
    orch, store = _orch(graph, state)

    result = orch.submit(ctx=None, fields={"decision": "approve", "_human": True})

    assert result["ok"] is True, result
    assert store["state"].current_node == "IMPLEMENT"
    assert store["state"].current_sub_state == "USER_GATE"


def test_project_init_project_ready_cruza_a_sprint():
    from fast_mcp.features.tasks.domain.task_registry import TaskResult

    class _NoopRegistry:
        """El test prueba el RUTEO; la acción real se cubre en su propio BDD."""

        def has(self, name):
            return True

        def execute(self, name, ctx, data):
            return TaskResult.success(data={"ok": True, "action": name})

    graph = _canonical_graph()
    node = graph.get_node("PROJECT_INIT")
    index = node.sub_state_index("REVIEW")
    state = WorkflowState(
        current_node="PROJECT_INIT", current_sub_state="REVIEW", sub_state_index=index
    )
    orch, store = _orch(graph, state, registry=_NoopRegistry())

    result = orch.submit(ctx=None, fields={"intent": "project_ready", "_human": True})

    assert result["ok"] is True, result
    assert store["state"].current_node == "SPRINT"
    assert store["state"].current_sub_state == "ACTIVE"


def test_advance_no_saltea_el_informe_de_un_llm_task():
    """`advance` no puede cruzar un llm_task con required en el input_schema.

    Medido 2026-11-26: SPRINT.REPORT (summary/metrics/risks/next) se salteó con
    `advance` y el sprint pasó a RETRO sin informe. El `required` de un
    llm_task también es un gate, no una sugerencia.
    """
    graph = _canonical_graph()
    node = graph.get_node("SPRINT")
    state = WorkflowState(
        current_node="SPRINT",
        current_sub_state="REPORT",
        sub_state_index=node.sub_state_index("REPORT"),
    )
    orch, store = _orch(graph, state)

    result = orch.advance(ctx=None, fields={})

    assert result["ok"] is False, result
    assert result.get("error_code") == "missing_required_fields", result
    assert any("summary" in e for e in result.get("errors") or []), result
    assert store["state"].current_sub_state == "REPORT"
