"""
Test: GATE DE TRABAJO SIN CAPTURAR (anti-pérdida al salir del sprint).

El watcher registra eventos file_impact en el TraceGraph (archivos
modificados), pero convertirlos en specs del session-log es MANUAL
(capture_task). El gate detecta, al salir del sprint (SPRINT.REVIEW /
SPRINT.CLOSE_CONFIRM), si hay cambios de código sin spec que los cubra y
BLOQUEA el cierre — para no perder trabajo.

Valida:
  1. Contrato del resultado UncapturedWorkResult: status clean/uncaptured/
     error, should_block, to_dict, gate_message.
  2. detect_uncaptured_work: compara file_impact del trace contra specs del
     delta (spring activo).
  3. SubmitOrchestrator bloquea (gate_blocked) en SPRINT.REVIEW cuando hay
     trabajo sin capturar.
  4. SubmitOrchestrator deja pasar cuando todo está cubierto o sin cambios.
  5. No aplica fuera del nodo SPRINT (no bloquea trabajo normal).
"""

import gzip
import json
import os
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock

WALKER_CHAIN_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "data", "spring", "walker-sprint")
)


# ════════════════════════════════════════════════════════════════
# Fixtures: proyecto cognitivo aislado en tempdir
# ════════════════════════════════════════════════════════════════

def _make_project(files_changed: list[str], covered_files: list[str]) -> str:
    """Crea un proyecto aislado con trace (file_impact) + delta (specs).

    Estructura: <tmp>/proj/data/{project.json, trace.json.gz, delta.json.gz}
    y <tmp>/proj/data/spring/chain (chain_dir del workflow).
    """
    tmpdir = tempfile.mkdtemp()
    proj = os.path.join(tmpdir, "proj")
    data_dir = os.path.join(proj, "data")
    chain_dir = os.path.join(data_dir, "spring", "chain")
    os.makedirs(chain_dir, exist_ok=True)

    with open(os.path.join(data_dir, "project.json"), "w") as f:
        json.dump({"project_id": "test-proj", "name": "test"}, f)

    # ── change_detected.json del watcher (estado actual) ──
    with open(os.path.join(data_dir, "change_detected.json"), "w") as f:
        json.dump(
            {
                "files_changed": files_changed,
                "total_changes": len(files_changed),
                "cpg_status": "stale",
            },
            f,
        )

    # ── TraceGraph: evento file_impact del watcher ──
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

    # ── DeltaGraph: spring activo + spec con files cubiertos ──
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
    """Proyecto sin trace ni delta (no hay eventos ni spring)."""
    tmpdir = tempfile.mkdtemp()
    proj = os.path.join(tmpdir, "proj")
    data_dir = os.path.join(proj, "data")
    chain_dir = os.path.join(data_dir, "spring", "chain")
    os.makedirs(chain_dir, exist_ok=True)
    with open(os.path.join(data_dir, "project.json"), "w") as f:
        json.dump({"project_id": "test-proj"}, f)
    return chain_dir


# ════════════════════════════════════════════════════════════════
# 1. Contrato del resultado
# ════════════════════════════════════════════════════════════════

def test_result_clean_no_bloquea():
    from features.engine.application.uncaptured_work import (
        UncapturedWorkResult,
        UncapturedStatus,
    )

    r = UncapturedWorkResult(
        status=UncapturedStatus.CLEAN,
        impacted_files=["a.py"],
        covered_files=["a.py"],
        uncaptured_files=[],
    )
    assert r.status == UncapturedStatus.CLEAN
    assert r.should_block is False
    assert r.gate_message() == ""
    d = r.to_dict()
    assert d["status"] == "clean" and d["should_block"] is False
    print("  ✅ test_result_clean_no_bloquea")


def test_result_uncaptured_bloquea_con_mensaje():
    from features.engine.application.uncaptured_work import (
        UncapturedWorkResult,
        UncapturedStatus,
    )

    r = UncapturedWorkResult(
        status=UncapturedStatus.UNCAPTURED,
        impacted_files=["src/a.py"],
        covered_files=["src/b.py"],
        uncaptured_files=["src/a.py"],
        spring_id="session-1",
    )
    assert r.should_block is True
    msg = r.gate_message()
    assert "BLOQUEA" in msg and "src/a.py" in msg
    assert "capture_task" in msg
    assert "session-1" in msg
    print("  ✅ test_result_uncaptured_bloquea_con_mensaje")


def test_result_error_fail_open_visible():
    """Estado ERROR = detección fallida, NO disfrazada de éxito."""
    from features.engine.application.uncaptured_work import (
        UncapturedWorkResult,
        UncapturedStatus,
    )

    r = UncapturedWorkResult(
        status=UncapturedStatus.ERROR, error="boom: trace corrupto"
    )
    assert r.status == UncapturedStatus.ERROR
    assert r.should_block is False  # fail-open: no bloquear por infra
    assert r.error
    assert r.gate_message() == ""
    d = r.to_dict()
    assert d["status"] == "error"
    assert d["should_block"] is False
    print("  ✅ test_result_error_fail_open_visible")


# ════════════════════════════════════════════════════════════════
# 2. Detector: comparación trace (file_impact) vs delta (specs)
# ════════════════════════════════════════════════════════════════

def test_detector_sin_trabajo_sin_capturar():
    """Archivos modificados cubiertos por specs → clean."""
    from features.engine.application.uncaptured_work import (
        detect_uncaptured_work,
        UncapturedStatus,
    )

    chain_dir = _make_project(
        files_changed=["features/engine/x.py"],
        covered_files=["features/engine/x.py"],
    )
    r = detect_uncaptured_work(chain_dir)
    assert r.status == UncapturedStatus.CLEAN, r
    assert r.should_block is False
    print("  ✅ test_detector_sin_trabajo_sin_capturar")


def test_detector_captura_trabajo_sin_capturar():
    """Archivo modificado sin spec que lo cubra → uncaptured."""
    from features.engine.application.uncaptured_work import (
        detect_uncaptured_work,
        UncapturedStatus,
    )

    chain_dir = _make_project(
        files_changed=["features/engine/x.py", "features/engine/y.py"],
        covered_files=["features/engine/x.py"],
    )
    r = detect_uncaptured_work(chain_dir)
    assert r.status == UncapturedStatus.UNCAPTURED, r
    assert r.uncaptured_files == ["features/engine/y.py"]
    assert r.should_block is True
    print("  ✅ test_detector_captura_trabajo_sin_capturar")


def test_detector_cubre_por_directorio():
    """Spec cubre un directorio → archivos dentro quedan cubiertos."""
    from features.engine.application.uncaptured_work import (
        detect_uncaptured_work,
        UncapturedStatus,
    )

    chain_dir = _make_project(
        files_changed=["features/engine/application/foo.py"],
        covered_files=["features/engine"],
    )
    r = detect_uncaptured_work(chain_dir)
    assert r.status == UncapturedStatus.CLEAN, r
    print("  ✅ test_detector_cubre_por_directorio")


def test_detector_sin_spring_no_bloquea():
    """Sin spring activo no hay contra qué comparar → clean (no bloquea)."""
    from features.engine.application.uncaptured_work import (
        detect_uncaptured_work,
        UncapturedStatus,
    )

    chain_dir = _empty_project()
    r = detect_uncaptured_work(chain_dir)
    assert r.status == UncapturedStatus.CLEAN
    assert r.has_spring is False
    assert r.should_block is False
    print("  ✅ test_detector_sin_spring_no_bloquea")


def test_detector_lee_change_detected_sin_trace():
    """change_detected.json (watcher) alcanza para detectar trabajo perdido.

    Caso real: el watcher acumula files_changed pero puede no haber eventos
    file_impact en el TraceGraph. El detector debe usar AMBAS fuentes.
    """
    import tempfile
    from features.engine.application.uncaptured_work import (
        detect_uncaptured_work,
        UncapturedStatus,
    )

    tmpdir = tempfile.mkdtemp()
    proj = os.path.join(tmpdir, "proj")
    data_dir = os.path.join(proj, "data")
    chain_dir = os.path.join(data_dir, "spring", "chain")
    os.makedirs(chain_dir, exist_ok=True)
    with open(os.path.join(data_dir, "project.json"), "w") as f:
        json.dump({"project_id": "test-proj"}, f)
    # Solo change_detected.json, SIN trace.json.gz con file_impact
    with open(os.path.join(data_dir, "change_detected.json"), "w") as f:
        json.dump({"files_changed": ["features/engine/z.py"]}, f)

    r = detect_uncaptured_work(chain_dir)
    assert "features/engine/z.py" in r.impacted_files, r
    print("  ✅ test_detector_lee_change_detected_sin_trace")


# ════════════════════════════════════════════════════════════════
# 3. Enforcement en SubmitOrchestrator
# ════════════════════════════════════════════════════════════════

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
    """SubmitOrchestrator con state/workflow reales del proyecto walker.

    El gate de uncaptured_work se evalúa ANTES de submit_uc, con state
    apuntando a SPRINT.<sub_state>. detect_uncaptured_work usa chain_dir
    real del fixture (el tracer stub no interfiere porque el detector
    instancia su propio TraceLogger).
    """
    from features.engine.infrastructure.repositories import WorkflowRepository
    from features.engine.application.submit_orchestrator import SubmitOrchestrator

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


def test_submit_bloquea_en_review_con_trabajo_sin_capturar():
    """SPRINT.REVIEW con archivo sin capturar → gate_blocked, sin submit."""
    chain_dir = _make_project(
        files_changed=["features/engine/x.py", "features/engine/y.py"],
        covered_files=["features/engine/x.py"],
    )
    orch, submit_uc, state_repo = _make_orchestrator(chain_dir)
    result = orch.submit(_make_ctx(chain_dir))
    assert result.get("action") == "gate_blocked", f"Esperaba gate_blocked: {result}"
    assert result.get("blocked") is True
    assert result.get("error_type") == "uncaptured_work_gate"
    assert "features/engine/y.py" in result.get("message", "")
    assert "capture_task" in result.get("message", "")
    submit_uc.execute.assert_not_called()
    print("  ✅ test_submit_bloquea_en_review_con_trabajo_sin_capturar")


def test_submit_pasa_cuando_todo_cubierto():
    """SPRINT.REVIEW sin trabajo sin capturar → submit avanza."""
    chain_dir = _make_project(
        files_changed=["features/engine/x.py"],
        covered_files=["features/engine/x.py"],
    )
    orch, submit_uc, _ = _make_orchestrator(chain_dir)
    result = orch.submit(_make_ctx(chain_dir))
    assert result.get("action") != "gate_blocked", f"No debería bloquear: {result}"
    submit_uc.execute.assert_called_once()
    print("  ✅ test_submit_pasa_cuando_todo_cubierto")


def test_gate_no_aplica_fuera_del_sprint():
    """En IMPLEMENT.WORK el gate de salida de sprint no se evalúa."""
    chain_dir = _make_project(
        files_changed=["features/engine/x.py", "features/engine/y.py"],
        covered_files=["features/engine/x.py"],
    )
    orch, submit_uc, state_repo = _make_orchestrator(chain_dir)
    # Estado origen: IMPLEMENT.WORK (no es salida de sprint)
    state_repo.load.return_value.current_node = "IMPLEMENT"
    state_repo.load.return_value.current_sub_state = "WORK"
    result = orch.submit(_make_ctx(chain_dir, sub_state="WORK", node="IMPLEMENT"))
    assert result.get("action") != "gate_blocked", f"No debería bloquear: {result}"
    submit_uc.execute.assert_called_once()
    print("  ✅ test_gate_no_aplica_fuera_del_sprint")


def test_gate_requiere_solo_subestados_de_salida():
    """SPRINT.ACTIVE (dashboard) no dispara el gate de cierre."""
    chain_dir = _make_project(
        files_changed=["features/engine/x.py", "features/engine/y.py"],
        covered_files=["features/engine/x.py"],
    )
    orch, submit_uc, state_repo = _make_orchestrator(chain_dir)
    state_repo.load.return_value.current_sub_state = "ACTIVE"
    result = orch.submit(_make_ctx(chain_dir, sub_state="ACTIVE"))
    assert result.get("action") != "gate_blocked", f"No debería bloquear: {result}"
    submit_uc.execute.assert_called_once()
    print("  ✅ test_gate_requiere_solo_subestados_de_salida")
