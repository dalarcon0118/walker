"""BDD: persistencia atómica del WorkContract en capture/spec_status (F0b).

Contrato (SPEC-002, ADR-WALKER-086):
  - `capture` y `spec_status` validan el contrato con el validador de F0a
    ANTES de persistir.
  - Un contrato válido se guarda en la spec (`task_context.work_contract`)
    en UN solo save.
  - Un contrato inválido NO toca el estado y devuelve los errores accionables.
  - R1: tasks NO importa delta_tracking; el validador llega por DI (_di).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fast_mcp.features.tasks.handlers import _di
from fast_mcp.features.tasks.handlers.capture import capture
from fast_mcp.features.tasks.handlers.spec_status import spec_status


# ── Fake SprintService con conteo de saves ────────────────────────────────


class FakeSprintService:
    def __init__(self, specs: list[dict], sprint_id: str = "SPR-1"):
        self.specs = {s["spec_id"]: dict(s) for s in specs}
        self.sprint_id = sprint_id
        self.saves = 0

    def get_session(self, session_id):
        return {"session_id": session_id, "spring_id": self.sprint_id}

    def get_active_spring(self):
        return {"spring_id": self.sprint_id, "status": "active"}

    def get_specs(self, spring_id):
        if spring_id != self.sprint_id:
            return []
        return [dict(s) for s in self.specs.values()]

    def get_spec(self, spring_id, spec_id):
        if spring_id != self.sprint_id:
            return None
        spec = self.specs.get(spec_id)
        return dict(spec) if spec else None

    def update_spec(self, spring_id, spec_id, updates):
        if spring_id != self.sprint_id or spec_id not in self.specs:
            return None
        self.specs[spec_id].update(updates)
        self.saves += 1
        return dict(self.specs[spec_id])

    def update_spec_status(self, spring_id, spec_id, status, artifacts=None):
        if spring_id != self.sprint_id or spec_id not in self.specs:
            return None
        spec = self.specs[spec_id]
        for key, value in (artifacts or {}).items():
            if value not in (None, "", []):
                spec[key] = value
        spec["status"] = status
        self.saves += 1
        return dict(spec)


def _spec(status: str = "in_progress", spec_id: str = "SPEC-002") -> dict:
    return {
        "spec_id": spec_id,
        "status": status,
        "objective": "F0b persistencia del contrato",
        "files": ["fast_mcp/features/tasks/handlers/capture.py"],
        "acceptance_criteria": ["el contrato válido se persiste"],
        "predicates": {},
        "task_context": {"task_id": spec_id},
    }


def _valid_contract() -> dict:
    return {
        "version": "1",
        "goal": "F0b persistir el contrato",
        "scope": ["fast_mcp/features/tasks/handlers/capture.py"],
        "criteria": ["el contrato válido se persiste"],
        "verification": [{"criterion": "tests", "check": "test"}],
        "budget": {"max_files": 3, "max_loc": 120},
        "recurrence": {"level": "full"},
    }


def _ctx(task_id: str = "SPEC-002") -> SimpleNamespace:
    return SimpleNamespace(
        chain_dir="/tmp/proyecto",
        task_id=task_id,
        session_id="ses-1",
        cpg_adapter=None,
    )


@pytest.fixture()
def service(monkeypatch):
    from fast_mcp.features.delta_tracking.domain.work_contract import (
        validate_work_contract,
    )

    svc = FakeSprintService([_spec()])
    monkeypatch.setattr(_di, "_SPRINT_FACTORY", lambda chain_dir: svc)
    monkeypatch.setattr(_di, "_CONTRACT_VALIDATOR", validate_work_contract)
    yield svc
    monkeypatch.setattr(_di, "_SPRINT_FACTORY", None)
    monkeypatch.setattr(_di, "_CONTRACT_VALIDATOR", None)


# ── capture ────────────────────────────────────────────────────────────────


def test_capture_persiste_contrato_valido(service):
    result = capture(_ctx(), {"contract": _valid_contract(), "force": True})

    assert result.ok is True, result.error
    assert result.data["contract"]["saved"] is True
    saved = service.specs["SPEC-002"]["task_context"]["work_contract"]
    assert saved["goal"] == "F0b persistir el contrato"
    assert service.saves == 1


def test_capture_rechaza_contrato_invalido_y_no_persiste(service):
    bad = _valid_contract()
    del bad["goal"]

    result = capture(_ctx(), {"contract": bad, "force": True})

    assert result.ok is False
    assert "contrato inválido" in result.error
    assert "goal" in result.error
    assert service.saves == 0
    assert "work_contract" not in service.specs["SPEC-002"]["task_context"]


def test_capture_sin_contrato_no_escribe(service):
    result = capture(_ctx(), {"evidence": "nada", "force": True})

    assert result.ok is True
    assert result.data["contract"]["saved"] is False
    assert service.saves == 0


# ── spec_status ────────────────────────────────────────────────────────────


def test_spec_status_persiste_contrato_junto_con_status(service):
    result = spec_status(
        _ctx(),
        {
            "spec_id": "SPEC-002",
            "status": "completed",
            "contract": _valid_contract(),
            "completed_at": "2026-11-24T00:00:00Z",
            "commit_hash": "abc",
            "review_verdict": "approved",
            "evidence": ["tests verdes"],
            "reviewed_by": "po",
        },
    )

    assert result.ok is True, result.error
    spec = service.specs["SPEC-002"]
    assert spec["status"] == "completed"
    assert spec["task_context"]["work_contract"]["goal"] == "F0b persistir el contrato"
    assert service.saves == 1


def test_spec_status_rechaza_contrato_invalido_y_no_cambia_status(service):
    bad = _valid_contract()
    bad["version"] = "99"

    result = spec_status(
        _ctx(),
        {"spec_id": "SPEC-002", "status": "completed", "contract": bad},
    )

    assert result.ok is False
    assert "contrato inválido" in result.error
    assert "version" in result.error
    assert service.saves == 0
    assert service.specs["SPEC-002"]["status"] == "in_progress"


# ── DI / R1 ─────────────────────────────────────────────────────────────────


def test_validate_contract_sin_validador_reporta_error(monkeypatch):
    monkeypatch.setattr(_di, "_CONTRACT_VALIDATOR", None)

    errors = _di.validate_contract(_valid_contract())

    assert errors and "no configurado" in errors[0]


def test_capture_NO_importa_delta_tracking():
    """R1: el handler no importa delta_tracking (solo via _di)."""
    import ast
    import pathlib

    path = pathlib.Path(capture.__code__.co_filename)
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert "delta_tracking" not in node.module, (
                f"capture.py importa {node.module} (viola R1)"
            )
        elif isinstance(node, ast.Import):
            for n in node.names:
                assert "delta_tracking" not in n.name, (
                    f"capture.py importa {n.name} (viola R1)"
                )
