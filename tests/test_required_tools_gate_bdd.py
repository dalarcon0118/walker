"""Tests BDD (pytest-bdd) — Gate de required_tools en submit (CKG doc-28).

Feature value: un sub-estado con required_tools declara capas que el agente
DEBE consultar antes de submit; el engine BLOQUEA el submit si el TraceGraph
no registra evidencia de consulta. Fail-open si el tracer falla.

Escenarios: tests/features/required_tools_gate.feature
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pytest_bdd import given, scenarios, then, when

scenarios("features/required_tools_gate.feature")

NODE_PATH = os.path.join(
    "features", "engine", "workflow", "nodes", "implement.node.json"
)
WALKER_CHAIN_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "data", "spring", "walker-sprint")
)


@pytest.fixture
def gate_ctx() -> dict:
    return {}


# ── Helpers ───────────────────────────────────────────────────────


def _load_node() -> dict:
    with open(NODE_PATH) as f:
        return json.load(f)


def _substate(node: dict, key: str) -> dict:
    for ss in node.get("sub_states", []):
        if ss.get("key") == key:
            return ss
    raise AssertionError(f"sub-estado {key} no encontrado")


def _work_substate_obj():
    from features.engine.infrastructure.repositories import WorkflowRepository

    graph = WorkflowRepository().load(WALKER_CHAIN_DIR)
    node = graph.get_node("IMPLEMENT")
    for ss in node.sub_states:
        if ss.key == "WORK":
            return ss
    raise AssertionError("sub-estado WORK no encontrado en el grafo")


def _make_ctx(chain_dir: str, task_id: str = "TASK-001", session_id: str = "sess-1"):
    return SimpleNamespace(
        chain_dir=chain_dir,
        task_id=task_id,
        session_id=session_id,
        node_id="implement",
        sub_state="WORK",
        baggage={},
    )


class _TraceLogLike:
    def __init__(self, events):
        self.events = events

    def has_kg_query(self, chain_dir, task_id="", session_id="", layers=None):
        layers = {l for l in (layers or set()) if l}
        for ev in self.events:
            if ev.get("event_type") != "kg_query":
                continue
            data = ev.get("data") or {}
            idx = data.get("index")
            if layers and idx not in layers and idx != "multi":
                continue
            ev_task = str(ev.get("task_id", "") or "")
            ev_sess = str(ev.get("session_id", "") or "")
            if task_id and ev_task == task_id:
                return True
            if session_id and session_id not in ("", "cli") and ev_sess == session_id:
                return True
            if ev_sess in ("", "cli"):
                return True
        return False


class StubTracer:
    def __init__(self, events):
        self.events = events

    def has_kg_query(self, chain_dir, task_id="", session_id="", layers=None):
        return _TraceLogLike(self.events).has_kg_query(
            chain_dir, task_id=task_id, session_id=session_id, layers=layers
        )

    def trace(self, *a, **kw):
        pass

    def trace_submit(self, *a, **kw):
        pass

    def trace_error(self, *a, **kw):
        pass


def _make_submit_orchestrator(tracer_events: list[dict]):
    from features.engine.infrastructure.repositories import WorkflowRepository

    graph = WorkflowRepository().load(WALKER_CHAIN_DIR)
    state_repo = Mock()

    class _StateStub:
        def __init__(self):
            self.current_node = "IMPLEMENT"
            self.current_sub_state = "WORK"
            self.context = {}

        def with_context(self, k, v):
            self.context[k] = v
            return self

        def record_node_execution(self, **kw):
            self.last_execution = kw
            return self

    state_repo.load.return_value = _StateStub()
    workflow_repo = Mock()
    workflow_repo.load.return_value = graph
    submit_uc = Mock()
    submit_uc.execute.return_value = {
        "ok": True,
        "action": "advanced",
        "message": "submitted",
        "advanced_to": "CODE_REVIEW_SCAN",
    }
    from features.engine.application.submit_orchestrator import SubmitOrchestrator

    orch = SubmitOrchestrator(
        state_repo=state_repo,
        workflow_repo=workflow_repo,
        submit_uc=submit_uc,
        handle_error=Mock(),
        tracer=StubTracer(tracer_events),
        transition=Mock(),
        dispatch_events_from_side_effects=Mock(return_value=0),
    )
    return orch, submit_uc


# ── Given ─────────────────────────────────────────────────────────


@given("el nodo IMPLEMENT del workflow")
def _implement_node(gate_ctx: dict):
    gate_ctx["node"] = _load_node()


@given("el workflow cargado desde el chain_dir")
def _workflow_loaded(gate_ctx: dict):
    from features.engine.infrastructure.repositories import WorkflowRepository

    gate_ctx["graph"] = WorkflowRepository().load(WALKER_CHAIN_DIR)


@given("un SubmitOrchestrator con tracer sin eventos")
def _orch_no_events(gate_ctx: dict):
    orch, submit_uc = _make_submit_orchestrator(tracer_events=[])
    gate_ctx["orch"] = orch
    gate_ctx["submit_uc"] = submit_uc


@given("un SubmitOrchestrator con tracer con kg_query de TASK-001")
def _orch_with_task_match(gate_ctx: dict):
    events = [
        {
            "event_type": "kg_query",
            "task_id": "TASK-001",
            "session_id": "composer",
            "data": {"index": "ckg"},
        }
    ]
    orch, submit_uc = _make_submit_orchestrator(tracer_events=events)
    gate_ctx["orch"] = orch
    gate_ctx["submit_uc"] = submit_uc


@given("un SubmitOrchestrator con tracer con kg_query session cli índice cpg")
def _orch_with_cli_query(gate_ctx: dict):
    events = [
        {
            "event_type": "kg_query",
            "task_id": "",
            "session_id": "cli",
            "data": {"index": "cpg"},
        }
    ]
    orch, submit_uc = _make_submit_orchestrator(tracer_events=events)
    gate_ctx["orch"] = orch
    gate_ctx["submit_uc"] = submit_uc


@given("un SubmitOrchestrator con tracer con kg_query index multi")
def _orch_with_multi(gate_ctx: dict):
    events = [
        {
            "event_type": "kg_query",
            "task_id": "",
            "session_id": "cli",
            "data": {"index": "multi", "query": {"query": "workflow"}},
        }
    ]
    orch, submit_uc = _make_submit_orchestrator(tracer_events=events)
    gate_ctx["orch"] = orch
    gate_ctx["submit_uc"] = submit_uc


@given("un SubmitOrchestrator con tracer que lanza excepción")
def _orch_exploding_tracer(gate_ctx: dict):
    class ExplodingTracer:
        def has_kg_query(self, *a, **kw):
            raise RuntimeError("trace corrupto")

        def trace(self, *a, **kw):
            pass

        def trace_submit(self, *a, **kw):
            pass

        def trace_error(self, *a, **kw):
            pass

    from features.engine.application.submit_orchestrator import SubmitOrchestrator

    orch = SubmitOrchestrator(
        state_repo=Mock(),
        workflow_repo=Mock(),
        submit_uc=Mock(),
        handle_error=Mock(),
        tracer=ExplodingTracer(),
        transition=Mock(),
        dispatch_events_from_side_effects=Mock(return_value=0),
    )
    gate_ctx["orch"] = orch
    gate_ctx["sub"] = _work_substate_obj()


@given("el SubState USER_GATE sin required_tools")
def _user_gate_no_required(gate_ctx: dict):
    from features.engine.infrastructure.repositories import WorkflowRepository

    graph = WorkflowRepository().load(WALKER_CHAIN_DIR)
    node = graph.get_node("IMPLEMENT")
    gate_ctx["sub"] = next(ss for ss in node.sub_states if ss.key == "USER_GATE")
    orch, _ = _make_submit_orchestrator(tracer_events=[])
    gate_ctx["orch"] = orch


@given("el SubState WORK de IMPLEMENT con required_tools")
def _work_substate_with_tools(gate_ctx: dict):
    gate_ctx["sub"] = _work_substate_obj()


@given("un contexto en IMPLEMENT.WORK con required_tools")
def _context_with_required(gate_ctx: dict):
    gate_ctx["ctx"] = _make_ctx("/tmp/chain")


@given("un proyecto con 80 eventos kg_query en el trace")
def _project_with_80_events(gate_ctx: dict, tmp_path):
    import gzip

    project_root = str(tmp_path / "proj")
    os.makedirs(os.path.join(project_root, "data"), exist_ok=True)
    with open(os.path.join(project_root, "data", "project.json"), "w") as f:
        json.dump({"project_id": "test-project"}, f)
    docs = [
        {
            "_id": f"ev-{i:06d}",
            "event_type": "kg_query",
            "task_id": "",
            "session_id": "cli",
            "data": {"index": "ckg"},
        }
        for i in range(80)
    ]
    trace_payload = {"index": "trace", "docs": docs, "edges": []}
    with open(os.path.join(project_root, "data", "trace.json.gz"), "wb") as f:
        f.write(gzip.compress(json.dumps(trace_payload).encode()))
    gate_ctx["project_root"] = project_root


# ── When ──────────────────────────────────────────────────────────


@when("ejecuto submit")
def _submit(gate_ctx: dict):
    orch = gate_ctx["orch"]
    ctx = _make_ctx("/tmp/chain")
    gate_ctx["result"] = orch.submit(ctx)


@when("ejecuto _check_required_tools_gate")
def _check_gate(gate_ctx: dict):
    orch = gate_ctx["orch"]
    ctx = gate_ctx.get("ctx", _make_ctx("/tmp/chain"))
    gate_ctx["gate"] = orch._check_required_tools_gate(ctx, gate_ctx["sub"])


@when("genero el node briefing")
def _build_briefing(gate_ctx: dict):
    from features.engine.application.orchestrator import OrchestrationService
    from unittest.mock import Mock

    svc = OrchestrationService.__new__(OrchestrationService)
    sub = _work_substate_obj()
    lines = svc._build_node_briefing(Mock(), sub)
    gate_ctx["briefing"] = "\n".join(lines) if isinstance(lines, list) else str(lines)


@when("el TraceLogger hace replay")
def _trace_replay(gate_ctx: dict):
    from features.engine.infrastructure.trace_log import TraceLogger

    tl = TraceLogger()
    gate_ctx["events"] = tl.replay(gate_ctx["project_root"])
    gate_ctx["trace_logger"] = tl


@when("examino los sub-estados WORK, CODE_REVIEW_SCAN y KNOWLEDGE_UPDATE")
def _examine_llm(gate_ctx: dict):
    node = gate_ctx["node"]
    keys = ["WORK", "CODE_REVIEW_SCAN", "KNOWLEDGE_UPDATE"]
    gate_ctx["results"] = {}
    for key in keys:
        ss = _substate(node, key)
        gate_ctx["results"][key] = ss.get("required_tools", [])


@when("examino los sub-estados USER_GATE, CODE_REVIEW_RESULT y REPORT_GATE")
def _examine_manual(gate_ctx: dict):
    node = gate_ctx["node"]
    keys = ["USER_GATE", "CODE_REVIEW_RESULT", "REPORT_GATE"]
    gate_ctx["results"] = {}
    for key in keys:
        ss = _substate(node, key)
        gate_ctx["results"][key] = ss.get("required_tools", [])


@when("obtengo el SubState WORK de IMPLEMENT")
def _get_work_substate(gate_ctx: dict):
    gate_ctx["sub"] = _work_substate_obj()


# ── Then ──────────────────────────────────────────────────────────


@then("cada uno declara required_tools")
def _all_declare(gate_ctx: dict):
    for key, required in gate_ctx["results"].items():
        assert isinstance(required, list) and required, (
            f"{key} no declara required_tools"
        )


@then("cada lista incluye ckg")
def _list_includes_ckg(gate_ctx: dict):
    for key, required in gate_ctx["results"].items():
        ids = [t.get("id") for t in required if isinstance(t, dict)]
        assert "ckg" in ids, f"{key}: required_tools debe incluir ckg. Got {ids}"


@then("ninguno declara required_tools")
def _none_declare(gate_ctx: dict):
    for key, required in gate_ctx["results"].items():
        assert not required, f"{key} no debe tener required_tools"


@then("required_tools está parseado como lista")
def _parsed_as_list(gate_ctx: dict):
    required = getattr(gate_ctx["sub"], "required_tools", None)
    assert isinstance(required, list) and required, "required_tools no parseado"


@then("el primer elemento tiene id ckg y command_hint")
def _first_has_id_and_hint(gate_ctx: dict):
    required = getattr(gate_ctx["sub"], "required_tools")
    assert required[0]["id"] == "ckg"
    assert "command_hint" in required[0]


@then("la acción es gate_blocked")
def _action_gate_blocked(gate_ctx: dict):
    assert gate_ctx["result"].get("action") == "gate_blocked", gate_ctx["result"]


@then("la acción es advanced")
def _action_advanced(gate_ctx: dict):
    assert gate_ctx["result"].get("action") == "advanced", gate_ctx["result"]


@then("el error_type es required_tools_gate")
def _error_type_gate(gate_ctx: dict):
    assert gate_ctx["result"].get("error_type") == "required_tools_gate", gate_ctx[
        "result"
    ]


@then("el mensaje menciona las capas requeridas")
def _msg_mentions_layers(gate_ctx: dict):
    msg = gate_ctx["result"].get("message", "")
    assert "ckg" in msg and "cpg" in msg, msg


@then("submit_uc no es llamado")
def _submit_not_called(gate_ctx: dict):
    gate_ctx["submit_uc"].execute.assert_not_called()


@then("submit_uc es llamado una vez")
def _submit_called_once(gate_ctx: dict):
    gate_ctx["submit_uc"].execute.assert_called_once()


@then("el gate retorna ok")
def _gate_ok(gate_ctx: dict):
    assert gate_ctx["gate"].get("ok") is True, gate_ctx["gate"]


@then("el gate retorna ok con advertencia")
def _gate_ok_with_warn(gate_ctx: dict):
    assert gate_ctx["gate"].get("ok") is True, gate_ctx["gate"]
    assert "warn" in gate_ctx["gate"], gate_ctx["gate"]


@then("el texto incluye REQUIRED TOOLS")
def _text_required_tools(gate_ctx: dict):
    assert "REQUIRED TOOLS" in gate_ctx["briefing"], gate_ctx["briefing"]


@then("el texto incluye ckg y cpg")
def _text_ckg_cpg(gate_ctx: dict):
    assert "ckg" in gate_ctx["briefing"] and "cpg" in gate_ctx["briefing"]


@then("el texto incluye advertencia de bloqueo")
def _text_block_warning(gate_ctx: dict):
    assert "BLOCK" in gate_ctx["briefing"], gate_ctx["briefing"]


@then("se recuperan los 80 eventos")
def _replay_80(gate_ctx: dict):
    assert len(gate_ctx["events"]) == 80, (
        f"Esperaba 80, obtuvo {len(gate_ctx['events'])}"
    )


@then("has_kg_query detecta la consulta")
def _has_kg_query_detected(gate_ctx: dict):
    assert (
        gate_ctx["trace_logger"].has_kg_query(gate_ctx["project_root"], layers={"ckg"})
        is True
    )
