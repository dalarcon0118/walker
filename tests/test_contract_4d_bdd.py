"""BDD: contrato derivado + proyección 4D en session_context (F2, ADR-WALKER-086).

Contrato:
  - `implement_task` hace obligatorio el WorkContract derivándolo de la tarea
    ya declarada (objetivo, alcance, criterios) y lo persiste en el MISMO save.
  - `session_context` publica `contract_4d` con budget declarado vs consumido;
    sin contrato dice `present:false` y NO inventa budget.
  - Los criterios sin predicado se publican como `unmapped` (disfraz visible).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fast_mcp.features.delta_tracking.domain.work_contract import (
    validate_work_contract,
)
from fast_mcp.features.tasks.handlers import _di
from fast_mcp.features.tasks.handlers import implement as implement_handlers
from fast_mcp.features.tasks.handlers.implement import _derive_contract, implement_task
from fast_mcp.memories.handlers.knowledge import _budget_state, _contract_4d


# ── Fake SprintService ────────────────────────────────────────────────────


class FakeSprintService:
    def __init__(self, specs=None, sprint_id: str = "SPR-1"):
        self.sprint_id = sprint_id
        self.specs = {s["spec_id"]: dict(s) for s in (specs or [])}
        self.saves = 0

    def get_session(self, session_id):
        return {"session_id": session_id, "spring_id": self.sprint_id}

    def get_active_spring(self):
        return {"spring_id": self.sprint_id, "status": "active"}

    def get_spring(self, spring_id):
        return {"spring_id": self.sprint_id, "status": "active"} if spring_id == self.sprint_id else None

    def list_springs(self):
        return [{"spring_id": self.sprint_id, "status": "active"}]

    def get_specs(self, spring_id):
        return [dict(s) for s in self.specs.values()] if spring_id == self.sprint_id else []

    def get_spec(self, spring_id, spec_id):
        spec = self.specs.get(spec_id)
        return dict(spec) if spec and spring_id == self.sprint_id else None

    def update_spec(self, spring_id, spec_id, updates):
        if spring_id != self.sprint_id or spec_id not in self.specs:
            return None
        self.specs[spec_id].update(updates)
        self.saves += 1
        return dict(self.specs[spec_id])

    def get_session_plan(self, session_id):
        return {}

    def update_session_plan(self, session_id, plan):
        return plan

    def open_session(self, *args, **kwargs):
        return {"session_id": kwargs.get("session_id", "")}


def _ctx() -> SimpleNamespace:
    return SimpleNamespace(
        chain_dir="/tmp/proyecto-4d",
        task_id="SPEC-002",
        session_id="ses-1",
        cpg_adapter=None,
    )


@pytest.fixture()
def service(monkeypatch):
    svc = FakeSprintService([
        {
            "spec_id": "SPEC-002",
            "spring_id": "SPR-1",
            "status": "in_progress",
            "objective": "F2 presupuesto",
            "files": ["fast_mcp/features/tasks/handlers/implement.py"],
            "acceptance_criteria": ["el contrato se persiste"],
            "predicates": {"items": [], "unmapped": []},
            "task_context": {"task_id": "SPEC-002", "session_id": "ses-1"},
        }
    ])
    monkeypatch.setattr(_di, "_SPRINT_FACTORY", lambda chain_dir: svc)
    monkeypatch.setattr(_di, "_CONTRACT_VALIDATOR", validate_work_contract)
    yield svc
    monkeypatch.setattr(_di, "_SPRINT_FACTORY", None)
    monkeypatch.setattr(_di, "_CONTRACT_VALIDATOR", None)


# ── contrato derivado ─────────────────────────────────────────────────────


def test_derive_contract_es_valido_y_explicito():
    contract = _derive_contract(
        objective="F2 instrumentar el harness",
        files=["a.py", "b.py"],
        criteria=["el harness publica budget"],
        spec_id="SPEC-002",
    )
    assert validate_work_contract(contract) == []
    assert contract["goal"] == "F2 instrumentar el harness"
    assert contract["scope"] == ["a.py", "b.py"]
    # La cota es explícita y deja lugar a un archivo más que lo declarado.
    assert contract["budget"]["max_files"] == 3
    assert contract["budget"]["max_loc"] == 200


def test_implement_task_persiste_contrato_derivado(service, monkeypatch):
    events: list[dict] = []
    monkeypatch.setattr(
        implement_handlers._trace, "push", lambda chain_dir, event: events.append(event) or True
    )
    result = implement_task(
        _ctx(),
        {
            "task_id": "SPEC-002",
            "objective": "F2 presupuesto",
            "files": ["fast_mcp/features/tasks/handlers/implement.py"],
            "acceptance": ["el contrato se persiste"],
            "session_id": "ses-1",
        },
    )
    assert result.ok is True, result.error
    contract = service.specs["SPEC-002"]["task_context"]["work_contract"]
    assert validate_work_contract(contract) == []
    assert contract["goal"] == "F2 presupuesto"
    assert "budget_snapshot" in service.specs["SPEC-002"]["task_context"]
    assert any(e.get("event_type") == "contract_derived" for e in events)


# ── proyección 4D ─────────────────────────────────────────────────────────


def test_contract_4d_sin_contrato_no_inventa_budget():
    out = _contract_4d(None)
    assert out["present"] is False
    assert out["budget"]["max_files"] is None
    assert out["budget"]["measured"] is False
    assert out["budget"]["exceeded"] == []


def test_contract_4d_publica_budget_declarado_y_consumido():
    spec = {
        "spec_id": "SPEC-002",
        "task_context": {
            "work_contract": {
                "version": "1",
                "goal": "g",
                "scope": ["a.py"],
                "criteria": ["c"],
                "verification": [],
                "budget": {"max_files": 1, "max_loc": 10},
                "recurrence": {"source": "derived"},
            },
            "budget_snapshot": {"files": 3, "loc": 40},
        },
        "predicates": {"items": [{"type": "command_exit"}], "unmapped": [{"criterion": "x"}]},
    }
    out = _contract_4d(spec)
    assert out["present"] is True
    assert out["budget"]["max_files"] == 1
    assert out["budget"]["used_files"] == 3
    assert out["budget"]["measured"] is True
    assert out["budget"]["exceeded"] == ["files 3 > max_files 1", "loc 40 > max_loc 10"]
    assert out["criteria"] == {"mapped": 1, "unmapped": 1}


def test_budget_state_sin_snapshot_no_certifica():
    state = _budget_state({"budget": {"max_files": 1}}, None)
    assert state["measured"] is False
    assert state["exceeded"] == []


def test_contract_4d_mide_el_lote_de_la_sesion():
    spec = {
        "spec_id": "SPEC-002",
        "task_context": {
            "work_contract": {
                "version": "1",
                "goal": "g",
                "scope": ["a.py"],
                "criteria": ["c"],
                "verification": [{"criterion": "c", "check": "pending"}],
                "budget": {"max_files": 3, "max_loc": 200},
                "recurrence": {"source": "derived"},
            },
            "budget_snapshot": {"files": 76, "loc": 4735},
        },
    }
    out = _contract_4d(spec, ["a.py", "b.py", "a.py"])
    # El lote de la sesión manda: el árbol sucio completo no certifica nada.
    assert out["budget"]["used_files"] == 2
    assert out["budget"]["used_loc"] is None
    assert out["budget"]["measured"] is True
    assert out["budget"]["exceeded"] == []
    assert out["budget"]["snapshot"]["files"] == 76
