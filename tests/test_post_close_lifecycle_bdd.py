"""BDD: ciclo de vida después de cerrar un sprint.

`closed` es terminal (ADR-WALKER-078). Dos consecuencias que se arreglan acá:

  1. `session_bind mode=new` crea un sprint NUEVO con id sin colisión y
     reposiciona el workflow (si está en SPRINT.CLOSE) para que el auto-advance
     no cierre el sprint recién creado.
  2. `back_to_active` NO reabre: desde CLOSE exige un sprint activo. Si no hay,
     rechaza y manda a crear uno nuevo. Nunca deja ACTIVE sobre un sprint
     cerrado sin aviso.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from fast_mcp.composition import back_to_active_guard, reset_workflow_after_close
from fast_mcp.features.workflow.context import WorkflowContext


class FakeOrchestrator:
    def __init__(self, position: str) -> None:
        self.position = position
        self.transitions: list[tuple[str, str]] = []

    def status(self, _ctx):
        node, _, sub = self.position.partition(".")
        return {"node": node, "sub_state": sub}

    def transition(self, _ctx, gate: str, reason: str = ""):
        self.transitions.append((gate, reason))
        self.position = "SPRINT.ACTIVE"
        return {"ok": True, "position": "SPRINT.ACTIVE"}


def _patch_workflow(monkeypatch, orchestrator):
    monkeypatch.setattr(
        WorkflowContext,
        "resolve",
        staticmethod(lambda chain_dir: SimpleNamespace(orchestrator=orchestrator)),
    )


def test_reset_desde_close_mueve_a_active(monkeypatch):
    orch = FakeOrchestrator("SPRINT.CLOSE")
    _patch_workflow(monkeypatch, orch)

    out = reset_workflow_after_close("/tmp/proj")

    assert out["ok"] is True and out["changed"] is True
    assert orch.transitions and orch.transitions[0][0] == "back_to_active"
    assert out["position"] == "SPRINT.ACTIVE"


def test_reset_fuera_de_close_es_noop(monkeypatch):
    orch = FakeOrchestrator("SPRINT.ACTIVE")
    _patch_workflow(monkeypatch, orch)

    out = reset_workflow_after_close("/tmp/proj")

    assert out["ok"] is True and out["changed"] is False
    assert orch.transitions == []


def test_reset_falla_si_no_hay_workflow(monkeypatch):
    monkeypatch.setattr(
        WorkflowContext, "resolve", staticmethod(lambda chain_dir: None)
    )

    out = reset_workflow_after_close("/tmp/proj")

    assert out["ok"] is False and "workflow" in out["error"]


def test_reset_propaga_el_rechazo_del_motor(monkeypatch):
    class Rejecting(FakeOrchestrator):
        def transition(self, _ctx, gate, reason=""):
            return {"ok": False, "error": "gate no disponible"}

    _patch_workflow(monkeypatch, Rejecting("SPRINT.CLOSE"))

    out = reset_workflow_after_close("/tmp/proj")

    assert out["ok"] is False
    assert "gate no disponible" in out["error"]


@pytest.mark.parametrize(
    "position,active,expected",
    [
        ("SPRINT.CLOSE", None, True),
        ("SPRINT.CLOSE", {"spring_id": "SPR-1"}, False),
        ("SPRINT.ACTIVE", None, False),
        ("SPRINT.PLANNING", None, False),
    ],
)
def test_guard_back_to_active(position, active, expected):
    reason = back_to_active_guard(position, active)

    assert (reason is not None) is expected
    if expected:
        assert "mode=new" in reason
