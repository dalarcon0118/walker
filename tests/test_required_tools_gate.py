"""
Test: GATE DE REQUIRED_TOOLS (enforcement duro en submit).

Convención CKG doc-28: "si el contrato falla, bloquea e informa al agente;
el agente reintenta". Un sub-estado con required_tools declara capas que el
agente DEBE consultar (kg_query) antes de hacer submit; el engine BLOQUEA el
submit si el TraceGraph no registra evidencia de consulta.

Valida:
  1. El nodo implement declara required_tools en WORK, CODE_REVIEW_SCAN y
     KNOWLEDGE_UPDATE (solo sub-estados llm_task).
  2. repositories.py parsea required_tools al construir SubState.
  3. SubmitOrchestrator bloquea el submit (gate_blocked) cuando el tracer
     no registra kg_query para las capas requeridas.
  4. SubmitOrchestrator deja pasar el submit cuando el tracer registra
     kg_query (task/session match o consulta MCP "cli").
  5. El briefing (_build_node_briefing) renderiza la sección REQUIRED TOOLS.
"""

import json
import os
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock

NODE_PATH = os.path.join("features", "engine", "workflow", "nodes", "implement.node.json")

# chain_dir real del walker para que WorkflowRepository.load() resuelva al
# workflow.v3 canónico del proyecto.
WALKER_CHAIN_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "data", "spring", "walker-sprint")
)


def _load_node() -> dict:
    with open(NODE_PATH) as f:
        return json.load(f)


def _substate(node: dict, key: str) -> dict:
    for ss in node.get("sub_states", []):
        if ss.get("key") == key:
            return ss
    raise AssertionError(f"sub-estado {key} no encontrado")


def _work_substate_obj():
    """SubState real de WORK via WorkflowRepository (con required_tools)."""
    from features.engine.infrastructure.repositories import WorkflowRepository

    graph = WorkflowRepository().load(WALKER_CHAIN_DIR)
    node = graph.get_node("IMPLEMENT")
    for ss in node.sub_states:
        if ss.key == "WORK":
            return ss
    raise AssertionError("sub-estado WORK no encontrado en el grafo")


# ════════════════════════════════════════════════════════════════
# 1. Declaración declarativa en el nodo
# ════════════════════════════════════════════════════════════════

def test_llm_substates_declaran_required_tools():
    """WORK, CODE_REVIEW_SCAN y KNOWLEDGE_UPDATE declaran required_tools."""
    node = _load_node()
    for key in ("WORK", "CODE_REVIEW_SCAN", "KNOWLEDGE_UPDATE"):
        ss = _substate(node, key)
        required = ss.get("required_tools", [])
        assert isinstance(required, list) and required, (
            f"{key} no declara required_tools"
        )
        ids = [t.get("id") for t in required if isinstance(t, dict)]
        assert "ckg" in ids, f"{key}: required_tools debe incluir ckg. Got {ids}"
    print("  ✅ test_llm_substates_declaran_required_tools")


def test_gates_manuales_no_declaran_required_tools():
    """Los sub-estados user_input no llevan gate de auto-consulta."""
    node = _load_node()
    for key in ("USER_GATE", "CODE_REVIEW_RESULT", "REPORT_GATE"):
        ss = _substate(node, key)
        assert not ss.get("required_tools"), f"{key} no debe tener required_tools"
    print("  ✅ test_gates_manuales_no_declaran_required_tools")


# ════════════════════════════════════════════════════════════════
# 2. Parseo en repositories.py (SubState.required_tools)
# ════════════════════════════════════════════════════════════════

def test_repositories_parsea_required_tools():
    """WorkflowRepository construye SubState con required_tools desde el JSON."""
    sub = _work_substate_obj()
    required = getattr(sub, "required_tools", None)
    assert isinstance(required, list) and required, "required_tools no parseado"
    assert required[0]["id"] == "ckg"
    assert "command_hint" in required[0]
    print("  ✅ test_repositories_parsea_required_tools")


# ════════════════════════════════════════════════════════════════
# 3. Enforcement en SubmitOrchestrator
# ════════════════════════════════════════════════════════════════

def _make_ctx(chain_dir: str, task_id: str = "TASK-001", session_id: str = "sess-1"):
    return SimpleNamespace(
        chain_dir=chain_dir,
        task_id=task_id,
        session_id=session_id,
        node_id="implement",
        sub_state="WORK",
        baggage={},
    )


def _make_submit_orchestrator(tracer_events: list[dict], execute_ok: bool = True) -> tuple:
    """Crea SubmitOrchestrator con tracer stub y un submit_uc espiado.

    state_repo/workflow_repo devuelven el nodo IMPLEMENT real (con
    required_tools en WORK) para que submit() resuelva el gate.
    """
    from features.engine.infrastructure.repositories import WorkflowRepository

    class StubTracer:
        def __init__(self, events):
            self.events = events
            self.traces = []

        def has_kg_query(self, chain_dir, task_id="", session_id="", layers=None):
            return TraceLogLike(self.events).has_kg_query(
                chain_dir, task_id=task_id, session_id=session_id, layers=layers
            )

        def trace(self, *a, **kw):
            self.traces.append(("trace", a, kw))

        def trace_submit(self, *a, **kw):
            self.traces.append(("trace_submit", a, kw))

        def trace_error(self, *a, **kw):
            self.traces.append(("trace_error", a, kw))

    class TraceLogLike:
        def __init__(self, events):
            self.events = events

        def has_kg_query(self, chain_dir, task_id="", session_id="", layers=None):
            layers = {l for l in (layers or set()) if l}
            for ev in self.events:
                if ev.get("event_type") != "kg_query":
                    continue
                data = ev.get("data") or {}
                idx = data.get("index")
                # "multi" (kg_multi) consulta varias capas → cubre cualquiera.
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

    graph = WorkflowRepository().load(WALKER_CHAIN_DIR)
    state_repo = Mock()

    class _StateStub:
        """Estado inmutable (ELM) mínimo para el post-gate de submit()."""

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

    _state_stub = _StateStub()
    state_repo.load.return_value = _state_stub

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

    dispatch = Mock()
    dispatch.return_value = 0

    orch = SubmitOrchestrator(
        state_repo=state_repo,
        workflow_repo=workflow_repo,
        submit_uc=submit_uc,
        handle_error=Mock(),
        tracer=StubTracer(tracer_events),
        transition=Mock(),
        dispatch_events_from_side_effects=dispatch,
    )
    return orch, submit_uc


def _work_substate_obj():
    """SubState real de WORK via WorkflowRepository (con required_tools)."""
    from features.engine.infrastructure.repositories import WorkflowRepository

    graph = WorkflowRepository().load(WALKER_CHAIN_DIR)
    node = graph.get_node("IMPLEMENT")
    for ss in node.sub_states:
        if ss.key == "WORK":
            return ss
    raise AssertionError("sub-estado WORK no encontrado en el grafo")


def test_submit_bloquea_sin_kg_query():
    """Sin kg_query en el trace → gate_blocked, submit_uc NO se ejecuta."""
    orch, submit_uc = _make_submit_orchestrator(tracer_events=[])
    ctx = _make_ctx("/tmp/chain")
    result = orch.submit(ctx)
    assert result.get("action") == "gate_blocked", f"Esperaba gate_blocked: {result}"
    assert result.get("blocked") is True
    assert result.get("error_type") == "required_tools_gate"
    assert "ckg" in result.get("message", "")
    assert "cpg" in result.get("message", "")
    submit_uc.execute.assert_not_called()
    print("  ✅ test_submit_bloquea_sin_kg_query")


def test_submit_avanza_con_kg_query_task_match():
    """kg_query con task_id coincidente → submit avanza."""
    events = [
        {
            "event_type": "kg_query",
            "task_id": "TASK-001",
            "session_id": "composer",
            "data": {"index": "ckg"},
        }
    ]
    orch, submit_uc = _make_submit_orchestrator(tracer_events=events)
    ctx = _make_ctx("/tmp/chain")
    result = orch.submit(ctx)
    assert result.get("action") == "advanced", f"Esperaba advanced: {result}"
    submit_uc.execute.assert_called_once()
    print("  ✅ test_submit_avanza_con_kg_query_task_match")


def test_submit_avanza_con_consulta_cli():
    """Consulta MCP/CLI (session cli) de la capa requerida → pasa el gate."""
    events = [
        {
            "event_type": "kg_query",
            "task_id": "",
            "session_id": "cli",
            "data": {"index": "cpg"},
        }
    ]
    orch, submit_uc = _make_submit_orchestrator(tracer_events=events)
    ctx = _make_ctx("/tmp/chain")
    result = orch.submit(ctx)
    assert result.get("action") == "advanced", f"Esperaba advanced: {result}"
    submit_uc.execute.assert_called_once()
    print("  ✅ test_submit_avanza_con_consulta_cli")


def test_submit_avanza_con_kg_multi():
    """kg_multi (index='multi') cubre las layers requeridas → pasa el gate.

    El agente usa la herramienta multi-capa (walker_kg_multi) recomendada en
    los command_hints; su trace registra index='multi' y NO debe bloquearse.
    """
    events = [
        {
            "event_type": "kg_query",
            "task_id": "",
            "session_id": "cli",
            "data": {"index": "multi", "query": {"query": "workflow orquestador"}},
        }
    ]
    orch, submit_uc = _make_submit_orchestrator(tracer_events=events)
    ctx = _make_ctx("/tmp/chain")
    result = orch.submit(ctx)
    assert result.get("action") == "advanced", f"Esperaba advanced: {result}"
    submit_uc.execute.assert_called_once()
    print("  ✅ test_submit_avanza_con_kg_multi")


def test_gate_no_aplica_sin_required_tools():
    """Sub-estado sin required_tools (USER_GATE) no pasa por el gate."""
    from features.engine.infrastructure.repositories import WorkflowRepository

    graph = WorkflowRepository().load(WALKER_CHAIN_DIR)
    node = graph.get_node("IMPLEMENT")
    sub = next(ss for ss in node.sub_states if ss.key == "USER_GATE")

    orch, submit_uc = _make_submit_orchestrator(tracer_events=[])
    # Monkey-patch del sub-estado origen: el orquestador usa el del repo de estado,
    # pero para este test validamos el gate directo sobre el SubState.
    gate = orch._check_required_tools_gate(_make_ctx("/tmp/chain"), sub)
    assert gate.get("ok") is True, f"USER_GATE no debería pasar gate: {gate}"
    print("  ✅ test_gate_no_aplica_sin_required_tools")


def test_gate_fail_open_si_tracer_falla():
    """Si has_kg_query lanza excepción → fail-open (no bloquear por infra)."""
    class ExplodingTracer:
        def has_kg_query(self, *a, **kw):
            raise RuntimeError("trace corrupto")

    from features.engine.application.submit_orchestrator import SubmitOrchestrator
    sub = _work_substate_obj()
    orch = SubmitOrchestrator(
        state_repo=Mock(), workflow_repo=Mock(), submit_uc=Mock(),
        handle_error=Mock(), tracer=ExplodingTracer(), transition=Mock(),
        dispatch_events_from_side_effects=Mock(),
    )
    gate = orch._check_required_tools_gate(_make_ctx("/tmp/chain"), sub)
    assert gate.get("ok") is True and "warn" in gate
    print("  ✅ test_gate_fail_open_si_tracer_falla")


# ════════════════════════════════════════════════════════════════
# 4. Briefing renderiza REQUIRED TOOLS
# ════════════════════════════════════════════════════════════════

def test_briefing_renderiza_required_tools():
    """_build_node_briefing muestra la sección REQUIRED TOOLS del sub-estado."""
    from features.engine.application.orchestrator import OrchestrationService
    sub = _work_substate_obj()

    svc = OrchestrationService.__new__(OrchestrationService)
    lines = svc._build_node_briefing(Mock(), sub)
    text = "\n".join(lines) if isinstance(lines, list) else str(lines)
    assert "REQUIRED TOOLS" in text, text
    assert "ckg" in text and "cpg" in text, text
    assert "BLOCK" in text, "el briefing debe advertir del bloqueo del engine"
    print("  ✅ test_briefing_renderiza_required_tools")


def test_replay_devuelve_trace_completo():
    """replay() no trunca el trace a 50 eventos (fix del límite del search).

    El gate de required_tools necesita ver el trace completo para no dar
    falsos bloqueos con traces grandes.
    """
    import gzip
    from features.engine.infrastructure.trace_log import TraceLogger

    with tempfile.TemporaryDirectory() as tmpdir:
        project_root = os.path.join(tmpdir, "proj")
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

        tl = TraceLogger()
        events = tl.replay(project_root)
        assert len(events) == 80, f"Esperaba 80 eventos, obtuvo {len(events)}"
        assert tl.has_kg_query(project_root, layers={"ckg"}) is True
        print("  ✅ test_replay_devuelve_trace_completo")
