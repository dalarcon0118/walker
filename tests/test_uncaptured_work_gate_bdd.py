"""Tests BDD (pytest-bdd) — Gate de trabajo sin capturar.

Feature value: el trabajo sin capturar se detecta automáticamente para que
el sprint no avance con trabajo invisible, bloqueando el cierre cuando
hay cambios de código sin spec que los cubra.

Escenarios: bdd/features/uncaptured_work_gate.feature
"""

from __future__ import annotations

import gzip
import json
import os
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pytest_bdd import given, scenarios, then, when

scenarios("bdd/features/uncaptured_work_gate.feature")

WALKER_CHAIN_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "data", "spring", "walker-sprint")
)


@pytest.fixture
def ctx() -> dict:
    return {}


# ════════════════════════════════════════════════════════════════
# Helpers: proyecto cognitivo aislado
# ════════════════════════════════════════════════════════════════


def _make_project(files_changed: list[str], covered_files: list[str]) -> str:
    tmpdir = tempfile.mkdtemp()
    proj = os.path.join(tmpdir, "proj")
    data_dir = os.path.join(proj, "data")
    chain_dir = os.path.join(data_dir, "spring", "chain")
    os.makedirs(chain_dir, exist_ok=True)

    with open(os.path.join(data_dir, "project.json"), "w") as f:
        json.dump({"project_id": "test-proj", "name": "test"}, f)

    with open(os.path.join(data_dir, "change_detected.json"), "w") as f:
        json.dump(
            {
                "files_changed": files_changed,
                "total_changes": len(files_changed),
                "cpg_status": "stale",
            },
            f,
        )

    trace_docs = [
        {
            "_id": "ev-file-impact-1",
            "event_type": "file_impact",
            "source": "walker_watcher",
            "files": files_changed,
        }
    ]
    with open(os.path.join(data_dir, "trace.json.gz"), "wb") as f:
        f.write(
            gzip.compress(
                json.dumps({"index": "trace", "docs": trace_docs, "edges": []}).encode()
            )
        )

    spring = {
        "type": "spring",
        "spring_id": "session-20260803",
        "status": "active",
        "goal": "test",
    }
    spec = {
        "type": "spec",
        "spec_id": "SPEC-001",
        "spec_index": 0,
        "spring_id": "session-20260803",
        "status": "specified",
        "files": covered_files,
        "objective": "test spec",
    }
    delta_docs = {
        "session-20260803": spring,
        "session-20260803:SPEC-001": spec,
    }
    with open(os.path.join(data_dir, "delta.json.gz"), "wb") as f:
        f.write(
            gzip.compress(
                json.dumps({"index": "delta", "docs": delta_docs, "edges": []}).encode()
            )
        )

    return chain_dir


def _empty_project() -> str:
    tmpdir = tempfile.mkdtemp()
    proj = os.path.join(tmpdir, "proj")
    data_dir = os.path.join(proj, "data")
    chain_dir = os.path.join(data_dir, "spring", "chain")
    os.makedirs(chain_dir, exist_ok=True)
    with open(os.path.join(data_dir, "project.json"), "w") as f:
        json.dump({"project_id": "test-proj"}, f)
    return chain_dir


def _make_ctx(chain_dir: str, sub_state: str = "REVIEW", node: str = "SPRINT"):
    return SimpleNamespace(
        chain_dir=chain_dir,
        task_id="TASK-001",
        session_id="sess-1",
        node_id=node.lower(),
        sub_state=sub_state,
        baggage={},
    )


def _make_orchestrator(chain_dir: str):
    from core.engine.infrastructure.repositories import WorkflowRepository
    from core.engine.application.submit_orchestrator import SubmitOrchestrator

    graph = WorkflowRepository().load(WALKER_CHAIN_DIR)

    class _StateStub:
        def __init__(self, node, sub):
            self.current_node = node
            self.current_sub_state = sub
            self.context = {}

        def with_context(self, k, v):
            self.context[k] = v
            return self

        def record_node_execution(self, **kw):
            self.last_execution = kw
            return self

    class StubTracer:
        def trace(self, *a, **kw):
            self.traces = getattr(self, "traces", [])
            self.traces.append(("trace", a, kw))

        def trace_submit(self, *a, **kw):
            self.traces = getattr(self, "traces", [])
            self.traces.append(("trace_submit", a, kw))

        def trace_error(self, *a, **kw):
            self.traces = getattr(self, "traces", [])
            self.traces.append(("trace_error", a, kw))

        def trace_auto(self, *a, **kw):
            self.traces = getattr(self, "traces", [])
            self.traces.append(("trace_auto", a, kw))

    state_repo = Mock()
    state_repo.load.return_value = _StateStub("SPRINT", "REVIEW")
    workflow_repo = Mock()
    workflow_repo.load.return_value = graph

    submit_uc = Mock()
    submit_uc.execute.return_value = {
        "ok": True,
        "action": "advanced",
        "message": "submitted",
        "advanced_to": "RETRO",
    }

    orch = SubmitOrchestrator(
        state_repo=state_repo,
        workflow_repo=workflow_repo,
        submit_uc=submit_uc,
        handle_error=Mock(),
        tracer=StubTracer(),
        transition=Mock(),
        dispatch_events_from_side_effects=Mock(return_value=0),
    )
    return orch, submit_uc, state_repo


# ════════════════════════════════════════════════════════════════
# Steps: contrato del resultado
# ════════════════════════════════════════════════════════════════


@given("un resultado limpio sin trabajo sin capturar")
def _clean_result(ctx: dict):
    from core.engine.application.uncaptured_work import (
        UncapturedWorkResult,
        UncapturedStatus,
    )

    ctx["result"] = UncapturedWorkResult(
        status=UncapturedStatus.CLEAN,
        impacted_files=["a.py"],
        covered_files=["a.py"],
        uncaptured_files=[],
    )


@given("un resultado con trabajo sin capturar en archivos específicos")
def _uncaptured_result(ctx: dict):
    from core.engine.application.uncaptured_work import (
        UncapturedWorkResult,
        UncapturedStatus,
    )

    ctx["result"] = UncapturedWorkResult(
        status=UncapturedStatus.UNCAPTURED,
        impacted_files=["src/a.py"],
        covered_files=["src/b.py"],
        uncaptured_files=["src/a.py"],
        spring_id="session-1",
    )


@given("un resultado de error de detección")
def _error_result(ctx: dict):
    from core.engine.application.uncaptured_work import (
        UncapturedWorkResult,
        UncapturedStatus,
    )

    ctx["result"] = UncapturedWorkResult(
        status=UncapturedStatus.ERROR, error="boom: trace corrupto"
    )


@when("se consulta el estado del resultado")
def _query_result(ctx: dict):
    pass


@then("el estado es limpio")
def _assert_clean(ctx: dict):
    from core.engine.application.uncaptured_work import UncapturedStatus

    assert ctx["result"].status == UncapturedStatus.CLEAN, ctx["result"]


@then("el estado indica trabajo sin capturar")
def _assert_uncaptured(ctx: dict):
    from core.engine.application.uncaptured_work import UncapturedStatus

    assert ctx["result"].status == UncapturedStatus.UNCAPTURED, ctx["result"]


@then("el estado indica error")
def _assert_error(ctx: dict):
    from core.engine.application.uncaptured_work import UncapturedStatus

    assert ctx["result"].status == UncapturedStatus.ERROR, ctx["result"]


@then("no bloquea el avance")
def _assert_no_block(ctx: dict):
    assert ctx["result"].should_block is False, ctx["result"]


@then("bloquea el avance con un mensaje accionable")
def _assert_blocks_with_message(ctx: dict):
    assert ctx["result"].should_block is True, ctx["result"]
    msg = ctx["result"].gate_message()
    assert "BLOQUEA" in msg, msg


@then("el mensaje incluye los archivos afectados")
def _assert_message_includes_files(ctx: dict):
    result = ctx["result"]
    if hasattr(result, "gate_message"):
        msg = result.gate_message()
        files = result.uncaptured_files
    else:
        msg = result.get("message", "")
        files = result.get("uncaptured_files", [])
    for f in files:
        assert f in msg, f"Expected '{f}' in message: {msg}"


@then("el mensaje sugiere capturar el trabajo")
def _assert_message_suggests_capture(ctx: dict):
    msg = ctx["result"].gate_message()
    assert "capture_task" in msg, msg


@then("la detección no oculta el fallo")
def _assert_error_visible(ctx: dict):
    assert ctx["result"].error, ctx["result"]
    assert ctx["result"].gate_message() == "", ctx["result"]


@then("el resultado se puede serializar a diccionario")
def _assert_serializable(ctx: dict):
    d = ctx["result"].to_dict()
    assert isinstance(d, dict)
    assert "status" in d


# ════════════════════════════════════════════════════════════════
# Steps: detector
# ════════════════════════════════════════════════════════════════


@given("un proyecto con archivos modificados cubiertos por sus especificaciones")
def _project_covered(tmp_path, ctx: dict):
    chain_dir = _make_project(
        files_changed=["features/engine/x.py"],
        covered_files=["features/engine/x.py"],
    )
    ctx["chain_dir"] = chain_dir


@given("un proyecto con trabajo sin capturar")
def _project_uncaptured(tmp_path, ctx: dict):
    chain_dir = _make_project(
        files_changed=["features/engine/x.py", "features/engine/y.py"],
        covered_files=["features/engine/x.py"],
    )
    ctx["chain_dir"] = chain_dir


@given("un proyecto donde una especificación cubre un directorio completo")
def _project_directory_coverage(tmp_path, ctx: dict):
    chain_dir = _make_project(
        files_changed=["features/engine/application/foo.py"],
        covered_files=["features/engine"],
    )
    ctx["chain_dir"] = chain_dir


@given("un proyecto sin sprint activo")
def _project_no_spring(tmp_path, ctx: dict):
    chain_dir = _empty_project()
    ctx["chain_dir"] = chain_dir


@given("un proyecto con cambios del watcher pero sin eventos de trace")
def _project_change_detected_only(tmp_path, ctx: dict):
    import tempfile

    tmpdir = tempfile.mkdtemp()
    proj = os.path.join(tmpdir, "proj")
    data_dir = os.path.join(proj, "data")
    chain_dir = os.path.join(data_dir, "spring", "chain")
    os.makedirs(chain_dir, exist_ok=True)
    with open(os.path.join(data_dir, "project.json"), "w") as f:
        json.dump({"project_id": "test-proj"}, f)
    with open(os.path.join(data_dir, "change_detected.json"), "w") as f:
        json.dump({"files_changed": ["features/engine/z.py"]}, f)

    ctx["chain_dir"] = chain_dir


@when("se ejecuta la detección de trabajo sin capturar")
def _run_detection(ctx: dict):
    from core.engine.application.uncaptured_work import detect_uncaptured_work

    ctx["result"] = detect_uncaptured_work(ctx["chain_dir"])


@then("el detector determina que no hay trabajo invisible")
def _assert_detector_clean(ctx: dict):
    from core.engine.application.uncaptured_work import UncapturedStatus

    assert ctx["result"].status == UncapturedStatus.CLEAN, ctx["result"]


@then("el detector identifica el trabajo sin capturar")
def _assert_detector_uncaptured(ctx: dict):
    from core.engine.application.uncaptured_work import UncapturedStatus

    assert ctx["result"].status == UncapturedStatus.UNCAPTURED, ctx["result"]


@then("los archivos sin capturar son solo los desprotegidos")
def _assert_uncaptured_files(ctx: dict):
    assert ctx["result"].uncaptured_files == ["features/engine/y.py"], ctx["result"]


@then("bloquea el avance del sprint")
def _assert_detector_blocks(ctx: dict):
    assert ctx["result"].should_block is True, ctx["result"]


@then("no bloquea el avance del sprint")
def _assert_detector_no_block(ctx: dict):
    assert ctx["result"].should_block is False, ctx["result"]


@then("no hay spring activo")
def _assert_no_spring(ctx: dict):
    assert ctx["result"].has_spring is False, ctx["result"]


@then("los archivos afectados incluyen los cambios del watcher")
def _assert_impacted_files(ctx: dict):
    assert "features/engine/z.py" in ctx["result"].impacted_files, ctx["result"]


# ════════════════════════════════════════════════════════════════
# Steps: enforcement en SubmitOrchestrator
# ════════════════════════════════════════════════════════════════


@given("un sprint en revisión con trabajo sin capturar")
def _sprint_review_uncaptured(tmp_path, ctx: dict):
    chain_dir = _make_project(
        files_changed=["features/engine/x.py", "features/engine/y.py"],
        covered_files=["features/engine/x.py"],
    )
    orch, submit_uc, state_repo = _make_orchestrator(chain_dir)
    ctx["orchestrator"] = orch
    ctx["submit_uc"] = submit_uc
    ctx["state_repo"] = state_repo
    ctx["chain_dir"] = chain_dir


@given("un sprint en revisión con todo el trabajo capturado")
def _sprint_review_covered(tmp_path, ctx: dict):
    chain_dir = _make_project(
        files_changed=["features/engine/x.py"],
        covered_files=["features/engine/x.py"],
    )
    orch, submit_uc, state_repo = _make_orchestrator(chain_dir)
    ctx["orchestrator"] = orch
    ctx["submit_uc"] = submit_uc
    ctx["state_repo"] = state_repo
    ctx["chain_dir"] = chain_dir


@given("el workflow en un nodo normal de trabajo")
def _workflow_normal_work(tmp_path, ctx: dict):
    chain_dir = _make_project(
        files_changed=["features/engine/x.py", "features/engine/y.py"],
        covered_files=["features/engine/x.py"],
    )
    orch, submit_uc, state_repo = _make_orchestrator(chain_dir)
    state_repo.load.return_value.current_node = "IMPLEMENT"
    state_repo.load.return_value.current_sub_state = "WORK"
    ctx["orchestrator"] = orch
    ctx["submit_uc"] = submit_uc
    ctx["state_repo"] = state_repo
    ctx["chain_dir"] = chain_dir


@given("el workflow en un sub-estado intermedio del sprint")
def _workflow_sprint_active(tmp_path, ctx: dict):
    chain_dir = _make_project(
        files_changed=["features/engine/x.py", "features/engine/y.py"],
        covered_files=["features/engine/x.py"],
    )
    orch, submit_uc, state_repo = _make_orchestrator(chain_dir)
    state_repo.load.return_value.current_sub_state = "ACTIVE"
    ctx["orchestrator"] = orch
    ctx["submit_uc"] = submit_uc
    ctx["state_repo"] = state_repo
    ctx["chain_dir"] = chain_dir


@when("se intenta avanzar el sprint")
@when("se intenta avanzar el flujo")
def _submit(ctx: dict):
    orch = ctx["orchestrator"]
    chain_dir = ctx["chain_dir"]
    state_repo = ctx["state_repo"]
    sub_state = state_repo.load.return_value.current_sub_state
    node = state_repo.load.return_value.current_node
    result = orch.submit(_make_ctx(chain_dir, sub_state=sub_state, node=node))
    ctx["result"] = result


@then("el sprint queda bloqueado")
def _assert_gate_blocked(ctx: dict):
    assert ctx["result"].get("action") == "gate_blocked", ctx["result"]


@then("el tipo de error es de gate de trabajo sin capturar")
def _assert_error_type(ctx: dict):
    assert ctx["result"].get("error_type") == "uncaptured_work_gate", ctx["result"]


@then("no se ejecuta el submit")
def _assert_submit_not_called(ctx: dict):
    ctx["submit_uc"].execute.assert_not_called()


@then("el sprint avanza normalmente")
def _assert_advances(ctx: dict):
    assert ctx["result"].get("action") != "gate_blocked", ctx["result"]


@then("se ejecuta el submit")
def _assert_submit_called(ctx: dict):
    ctx["submit_uc"].execute.assert_called_once()


@then("la gate no aplica fuera del sprint")
def _assert_gate_not_applied_outside(ctx: dict):
    assert ctx["result"].get("action") != "gate_blocked", ctx["result"]


@then("la gate no aplica en sub-estados intermedios")
def _assert_gate_not_applied_intermediate(ctx: dict):
    assert ctx["result"].get("action") != "gate_blocked", ctx["result"]
