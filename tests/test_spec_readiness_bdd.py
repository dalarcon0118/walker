"""BDD: no se puede implementar con specs vacías.

Hueco medido 2026-11-24: el workflow declaraba PLAN_APPROVAL/TASK_SPEC/
TASK_REVIEW, pero `back_to_active` estaba disponible desde PLANNING y TASK_REVIEW
y el router lo ofrecía como próximo paso. El agente llegaba a ACTIVE con 7 specs
en `draft` y empezaba a implementar. Además no había tool para escribir los
campos obligatorios (`spec_predicates` sólo escribía predicados y
`spec_status` sólo el estado).

Contrato:
  - Desde PLANNING el próximo paso es `advance`, no `back_to_active`.
  - `start_task` y `all_done` exigen specs completas (composition root).
  - `spec_start` rechaza una spec `draft`/incompleta con remedio accionable.
  - `spec_update` escribe los campos y reporta qué falta.
  - `spec_predicates` escribe el criterio en `acceptance_criteria`.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fast_mcp import composition
from fast_mcp.features.tasks.handlers import _di
from fast_mcp.features.tasks.handlers import spec_predicates as predicates_handler
from fast_mcp.features.tasks.handlers import spec_update as update_handler
from fast_mcp.features.tasks.handlers.session import spec_start
from fast_mcp.features.workflow.application.orchestrator import next_action_for
from fast_mcp.features.workflow.domain.entities import Gate, WorkflowGraph, WorkflowState
from fast_mcp.features.workflow.domain.enums import ExecutionType


# ── Fake SprintService ────────────────────────────────────────────────────


class FakeSprintService:
    def __init__(self, specs: list[dict], plan: dict | None = None):
        self.specs = {s["spec_id"]: dict(s) for s in specs}
        self.plan = plan or {}
        self.sprint_id = "SPR-1"

    def get_session(self, session_id):
        return {"session_id": session_id, "spring_id": self.sprint_id}

    def get_active_spring(self):
        return {"spring_id": self.sprint_id, "status": "planning"}

    def get_session_plan(self, session_id):
        return dict(self.plan)

    def get_specs(self, spring_id):
        if spring_id != self.sprint_id:
            return []
        return list(self.specs.values())

    def get_spec(self, spring_id, spec_id):
        if spring_id != self.sprint_id:
            return None
        return self.specs.get(spec_id)

    def update_spec(self, spring_id, spec_id, updates):
        if spring_id != self.sprint_id or spec_id not in self.specs:
            return None
        self.specs[spec_id].update(updates)
        return self.specs[spec_id]


def _draft_spec(spec_id: str = "SPEC-001") -> dict:
    return {
        "spec_id": spec_id,
        "status": "draft",
        "objective": "",
        "files": [],
        "acceptance_criteria": [],
        "predicates": {},
        "task_context": {"task_id": spec_id},
    }


def _ready_spec(status: str = "specified", spec_id: str = "SPEC-001") -> dict:
    return {
        "spec_id": spec_id,
        "status": status,
        "objective": "implementar el control",
        "files": ["x.py"],
        "acceptance_criteria": ["los tests pasan"],
        "predicates": {"items": [{"type": "command_exit", "command": "pytest -q"}], "unmapped": []},
        "task_context": {"task_id": spec_id},
    }


@pytest.fixture()
def service(monkeypatch):
    from fast_mcp.features.delta_tracking.domain.spec_service import SpecValidator

    svc = FakeSprintService([_draft_spec()], plan={"task": "SPEC-001"})

    def fake_factory(chain_dir):
        return svc

    monkeypatch.setattr(_di, "_SPRINT_FACTORY", fake_factory)
    monkeypatch.setattr(
        _di, "_SPEC_VALIDATOR", lambda spec: SpecValidator.validate_spec(spec)
    )
    yield svc
    monkeypatch.setattr(_di, "_SPRINT_FACTORY", None)
    monkeypatch.setattr(_di, "_SPEC_VALIDATOR", None)


def _ctx():
    return SimpleNamespace(
        chain_dir="/tmp/proyecto", task_id="SPEC-001", session_id="ses-1", cpg_adapter=None
    )


# ── El router no ofrece el atajo ─────────────────────────────────────────


def _graph_with_sprint_planning() -> WorkflowGraph:
    graph = WorkflowGraph()
    graph.nodes["SPRINT"] = _node(
        "SPRINT", ["PLANNING", "PLAN_APPROVAL", "TASK_SPEC", "TASK_REVIEW", "ACTIVE"]
    )
    graph.edges = [
        Gate(
            name="back_to_active",
            source_node="SPRINT",
            target_node="SPRINT",
            target_sub_state="ACTIVE",
            available_from=["ACTIVE"],
        ),
    ]
    graph.edges_by_source = {"SPRINT": list(graph.edges)}
    return graph


def _node(node_id: str, keys: list[str]):
    from fast_mcp.features.workflow.domain.entities import SubState, WorkflowNode

    return WorkflowNode(
        node_id=node_id,
        label=node_id,
        sub_states=[
            SubState(key=key, label=key, execution=ExecutionType.LLM_TASK) for key in keys
        ],
    )


def test_desde_planning_el_router_pide_advance_y_no_back_to_active():
    graph = _graph_with_sprint_planning()
    state = WorkflowState(current_node="SPRINT", current_sub_state="PLANNING")

    action = next_action_for(state, graph)

    assert action["tool"] == "advance"
    assert "back_to_active" not in (action.get("gates") or [])


def test_transition_rechaza_un_gate_fuera_de_su_available_from():
    from fast_mcp.features.workflow.application.bundle import EngineBundle
    from fast_mcp.features.workflow.application.orchestrator import OrchestrationService

    graph = _graph_with_sprint_planning()
    state = WorkflowState(current_node="SPRINT", current_sub_state="PLANNING")
    orch = OrchestrationService(
        bundle=EngineBundle(
            workflow_store=None,
            workflow_io=None,
            tracer=None,
            side_effects=None,
            chain_dir="/tmp/x",
        )
    )
    orch._load_graph = lambda: graph
    orch._load_state = lambda: state
    orch._save_state = lambda s: True
    orch._trace_transition = lambda *a, **k: None

    out = orch.transition(ctx=None, gate="back_to_active")

    assert out["ok"] is False
    assert out["action"] == "invalid_source_sub_state"
    assert "PLANNING" in out["error"]


def test_next_task_limpia_los_campos_de_la_spec_anterior():
    """Una tarea nueva no hereda `spec_id`/campos de TASK_SPEC de la anterior."""
    from fast_mcp.features.workflow.application.bundle import EngineBundle
    from fast_mcp.features.workflow.application.orchestrator import OrchestrationService
    from fast_mcp.features.workflow.domain.entities import SubState, WorkflowNode
    from fast_mcp.features.workflow.domain.values import SubStateData

    node = WorkflowNode(
        node_id="SPRINT",
        sub_states=[
            SubState(key="TASK_SPEC", label="TASK_SPEC", required_fields=["spec_id"]),
            SubState(key="TASK_REVIEW", label="TASK_REVIEW"),
            SubState(key="ACTIVE", label="ACTIVE"),
        ],
    )
    gate = Gate(
        name="next_task",
        source_node="SPRINT",
        target_node="SPRINT",
        target_sub_state="TASK_SPEC",
        source_sub_state="TASK_REVIEW",
        available_from=["TASK_REVIEW"],
    )
    graph = WorkflowGraph(
        nodes={"SPRINT": node},
        edges=[gate],
        edges_by_source={"SPRINT": [gate]},
        initial_state="SPRINT",
    )
    state = WorkflowState(
        current_node="SPRINT",
        current_sub_state="TASK_REVIEW",
        sub_state_index=1,
        sub_state_data={
            "TASK_SPEC": SubStateData(fields={"spec_id": "SPEC-001", "status": "pending"}),
            "TASK_REVIEW": SubStateData(fields={"decision": "approve", "_human": True}),
        },
    )
    saved: dict = {}
    orch = OrchestrationService(
        bundle=EngineBundle(
            workflow_store=None,
            workflow_io=None,
            tracer=None,
            side_effects=None,
            chain_dir="/tmp/x",
        )
    )
    orch._load_graph = lambda: graph
    orch._load_state = lambda: state
    orch._save_state = lambda s: saved.setdefault("state", s) or True
    orch._trace_transition = lambda *a, **k: None

    out = orch.transition(ctx=None, gate="next_task")

    assert out["ok"] is True
    assert out["to_sub_state"] == "TASK_SPEC"
    assert "TASK_SPEC" not in saved["state"].sub_state_data
    # El router vuelve a pedir los campos de la spec nueva.
    assert out["next_action"]["tool"] == "submit"


def test_advance_limpia_la_data_vieja_del_subestado_destino():
    """Al entrar a TASK_REVIEW no debe sobrevivir el `approve` anterior."""
    from fast_mcp.features.workflow.application.bundle import EngineBundle
    from fast_mcp.features.workflow.application.orchestrator import OrchestrationService
    from fast_mcp.features.workflow.domain.entities import SubState, WorkflowNode
    from fast_mcp.features.workflow.domain.values import SubStateData

    node = WorkflowNode(
        node_id="SPRINT",
        sub_states=[
            SubState(key="TASK_SPEC", label="TASK_SPEC", execution=ExecutionType.LLM_TASK),
            SubState(
                key="TASK_REVIEW",
                label="TASK_REVIEW",
                execution=ExecutionType.USER_INPUT,
                input_schema={"required": ["decision"]},
            ),
        ],
    )
    graph = WorkflowGraph(nodes={"SPRINT": node}, initial_state="SPRINT")
    state = WorkflowState(
        current_node="SPRINT",
        current_sub_state="TASK_SPEC",
        sub_state_index=0,
        sub_state_data={
            "TASK_SPEC": SubStateData(fields={"spec_id": "SPEC-002"}),
            "TASK_REVIEW": SubStateData(fields={"decision": "approve", "_human": True}),
        },
    )
    saved: dict = {}
    orch = OrchestrationService(
        bundle=EngineBundle(
            workflow_store=None,
            workflow_io=None,
            tracer=None,
            side_effects=None,
            chain_dir="/tmp/x",
        )
    )
    orch._load_graph = lambda: graph
    orch._load_state = lambda: state
    orch._save_state = lambda s: saved.setdefault("state", s) or True
    orch._trace_submit = lambda *a, **k: None
    orch._execute_action = lambda *a, **k: (None, True, "")

    out = orch.advance(ctx=None, fields={})

    assert out["ok"] is True
    assert out["sub_state"] == "TASK_REVIEW"
    assert "TASK_REVIEW" not in saved["state"].sub_state_data
    # La decisión vieja ya no se ofrece: el paso pide el campo.
    assert out["next_action"]["tool"] == "submit"
    assert out["next_action"]["missing"] == ["decision"]


# ── Guards de transición (composition root) ──────────────────────────────


def test_start_task_bloquea_spec_draft(monkeypatch):
    svc = FakeSprintService([_draft_spec()], plan={"task": "SPEC-001"})
    monkeypatch.setattr(composition, "_workflow_task_id", lambda chain_dir: "SPEC-001")

    blocked = composition._start_task_spec_guard("/tmp/p", svc, "ses-1")

    assert blocked is not None
    assert blocked["action"] == "spec_not_ready"
    assert blocked["spec_id"] == "SPEC-001"
    assert any("objective" in e for e in blocked["errors"])
    assert "spec_update" in blocked["error"]


def test_start_task_permite_spec_completa(monkeypatch):
    svc = FakeSprintService([_ready_spec()], plan={"task": "SPEC-001"})
    monkeypatch.setattr(composition, "_workflow_task_id", lambda chain_dir: "SPEC-001")

    assert composition._start_task_spec_guard("/tmp/p", svc, "ses-1") is None


def test_start_task_bloquea_spec_backlog(monkeypatch):
    svc = FakeSprintService([_ready_spec(status="backlog")], plan={"task": "SPEC-001"})
    monkeypatch.setattr(composition, "_workflow_task_id", lambda chain_dir: "SPEC-001")

    blocked = composition._start_task_spec_guard("/tmp/p", svc, "ses-1")

    assert blocked is not None
    assert blocked["status"] == "backlog"


def test_all_done_bloquea_si_queda_una_spec_draft():
    svc = FakeSprintService([_ready_spec("specified", "SPEC-001"), _draft_spec("SPEC-002")])

    blocked = composition._all_done_specs_guard(svc, "ses-1")

    assert blocked is not None
    assert blocked["action"] == "specs_pending"
    assert [p["spec_id"] for p in blocked["pending"]] == ["SPEC-002"]


def test_all_done_ignora_backlog_y_completas():
    svc = FakeSprintService([
        _ready_spec("specified", "SPEC-001"),
        _ready_spec("backlog", "SPEC-002"),
    ])

    assert composition._all_done_specs_guard(svc, "ses-1") is None


# ── spec_start ───────────────────────────────────────────────────────────


def test_spec_start_rechaza_draft_con_remedio(service):
    out = spec_start(_ctx(), {"session_id": "ses-1", "spec_id": "SPEC-001"})

    assert out.ok is False
    assert out.data["status"] == "draft"
    assert out.data["errors"]
    assert "spec_update" in out.data["remedy"]["fields"]
    assert service.specs["SPEC-001"]["status"] == "draft"


def test_spec_start_permite_spec_completa(service):
    service.specs["SPEC-001"] = _ready_spec("specified")

    out = spec_start(_ctx(), {"session_id": "ses-1", "spec_id": "SPEC-001"})

    assert out.ok is True
    assert service.specs["SPEC-001"]["status"] == "in_progress"


# ── spec_update ──────────────────────────────────────────────────────────


def test_spec_update_escribe_campos_y_reporta_listo(service):
    out = update_handler.spec_update(_ctx(), {
        "fields": {
            "objective": "implementar el control",
            "files": ["x.py"],
            "acceptance_criteria": "los tests pasan",
            "task_context": {"nota": "ok"},
        },
    })

    assert out.ok is True
    spec = service.specs["SPEC-001"]
    assert spec["objective"] == "implementar el control"
    assert spec["files"] == ["x.py"]
    assert spec["acceptance_criteria"] == ["los tests pasan"]
    # task_context se MERGEA: no pierde el task_id que ya existía.
    assert spec["task_context"]["task_id"] == "SPEC-001"
    assert spec["task_context"]["nota"] == "ok"
    assert out.data["errors"] == []
    assert out.data["next"] == "spec_status status=specified"


def test_spec_update_rechaza_status_y_predicates(service):
    out = update_handler.spec_update(_ctx(), {"fields": {"status": "approved", "predicates": {}}})

    assert out.ok is False
    assert "spec_status" in out.error
    assert "spec_predicates" in out.error
    assert service.specs["SPEC-001"]["status"] == "draft"


def test_spec_update_rechaza_campo_desconocido(service):
    out = update_handler.spec_update(_ctx(), {"fields": {"inventado": 1}})

    assert out.ok is False
    assert "inventado" in out.error


def test_spec_update_sin_campos_falla_con_la_lista(service):
    out = update_handler.spec_update(_ctx(), {})

    assert out.ok is False
    assert "objective" in out.error


# ── spec_predicates escribe la aceptación ────────────────────────────────


def test_spec_predicates_escribe_el_criterio_en_acceptance(service):
    out = predicates_handler.spec_predicates(_ctx(), {
        "criteria": [{
            "criterion": "los tests pasan",
            "predicate": {"type": "command_exit", "command": "pytest -q"},
        }],
    })

    assert out.ok is True
    assert service.specs["SPEC-001"]["acceptance_criteria"] == ["los tests pasan"]
    assert out.data["criteria_added"] == ["los tests pasan"]
