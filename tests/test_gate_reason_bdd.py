"""BDD: un gate bloqueado SIEMPRE explica el motivo en `error`.

Medido 2026-11-18: `submit` en VERIFY.QA devolvió

    {"ok": false, "action": "gate_blocked", "error": null,
     "error_type": "required_skill_gate", "message": "Antes de avanzar, ..."}

El motivo existía pero viajaba solo en `message`; `error` —el campo que lee
todo el resto del sistema— volvía null, así que el bloqueo se leía como
silencio. El agente tuvo que diagnosticarlo a mano.
"""
from __future__ import annotations

from types import SimpleNamespace

from fast_mcp.features.workflow.application.bundle import EngineBundle
from fast_mcp.features.workflow.application.orchestrator import (
    MIN_SKILL_SCORE_REQUIRED,
    OrchestrationService,
)
from fast_mcp.features.workflow.domain.execution import ExecutionContext


def _service():
    return OrchestrationService(
        bundle=EngineBundle(
            workflow_store=None,
            workflow_io=None,
            tracer=None,
            side_effects=None,
            chain_dir="/tmp/x",
        )
    )


def test_gate_de_skills_bloquea_con_motivo_en_error(monkeypatch):
    orch = _service()
    sub_state = SimpleNamespace(skill_intent={"policy": "required", "needs": "review"})
    state = SimpleNamespace(updated_at="2026-11-18T20:00:00+00:00")

    # Candidatos presentes; sin tracer (bundle.tracer=None) no hay carga
    # verificable, así que el gate bloquea.
    monkeypatch.setattr(
        orch,
        "_skill_candidates_scored",
        lambda ctx, intent: [("code-review-checklist", MIN_SKILL_SCORE_REQUIRED)],
    )

    result = orch._check_skill_gate(None, sub_state, state)

    assert result is not None, "el gate debía bloquear"
    assert result["ok"] is False
    assert result["action"] == "gate_blocked"
    assert result["error_type"] == "required_skill_gate"
    assert isinstance(result["error"], str) and result["error"]
    assert result["error"] == result["message"]
    assert "code-review-checklist" in result["error"]


def test_gate_de_tools_bloquea_con_motivo_en_error(monkeypatch):
    orch = _service()
    sub_state = SimpleNamespace(
        required_tools=[{"id": "ckg", "command_hint": "search_memories ckg"}]
    )

    # Sin tracer, `has_kg_query` no se puede verificar: el gate bloquea.
    result = orch._check_required_tools_gate(
        ExecutionContext.create(chain_dir="/tmp/x", session_id="s1"), sub_state
    )

    assert result is not None, "el gate debía bloquear"
    assert result["action"] == "gate_blocked"
    assert result["error_type"] == "required_tools_gate"
    assert isinstance(result["error"], str) and result["error"]
    assert result["error"] == result["message"]
    assert "search_memories" in result["error"]
