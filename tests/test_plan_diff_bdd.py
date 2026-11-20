"""test_plan_diff_bdd.py — plan de vuelo ↔ spec (AGENT-EFFICIENCY §2.4 / M-04).

El plan y la spec son dos artefactos del mismo trabajo y divergen en silencio:
el plan tenía 4 criterios y la spec se creó con 0 (medido 2026-11-20). El diff
publica lo que está en uno y no en el otro, el scope que se amplió y los
criterios de la spec sin predicado ejecutable.
"""

from __future__ import annotations

from types import SimpleNamespace

import fast_mcp.composition as composition
from fast_mcp.memories.handlers.knowledge import handle_plan_diff


class _Ctx:
    chain_dir = "/tmp/proyecto"


def _service(plan: dict, specs: list[dict]):
    return SimpleNamespace(
        get_session=lambda _sid: {"spring_id": "SPR-1", "flight_plan": plan},
        get_specs=lambda _spr: specs,
    )


def test_plan_diff_publica_las_brechas(monkeypatch):
    plan = {
        "task": "T-1",
        "acceptance": ["golden >=18/20", "suite verde"],
        "scope": ["a.py", "b.py"],
    }
    spec = {
        "spec_id": "SPEC-001",
        "status": "in_progress",
        "task_context": {"task_id": "T-1"},
        "acceptance_criteria": ["suite verde", "sin regresiones"],
        "files": ["a.py"],
        "predicates": {"items": [{"type": "command_exit"}],
                       "unmapped": [{"criterion": "sin regresiones"}]},
    }
    monkeypatch.setattr(composition, "build_sprint_service",
                        lambda _d: _service(plan, [spec]))

    out = handle_plan_diff(_Ctx(), _payload={"session_id": "ses_x"})

    assert out["spec_id"] == "SPEC-001"
    assert out["only_in_plan"] == ["golden >=18/20"]
    assert out["only_in_spec"] == ["sin regresiones"]
    assert out["scope_expanded"] == ["b.py"]
    assert out["spec_files_missing_from_plan"] == []
    assert out["unverified"] == ["sin regresiones"]
    assert out["degraded"] is False


def test_plan_diff_sin_spec_lo_declara(monkeypatch):
    plan = {"task": "T-9", "acceptance": ["x"], "scope": ["a.py"]}
    monkeypatch.setattr(composition, "build_sprint_service",
                        lambda _d: _service(plan, []))

    out = handle_plan_diff(_Ctx(), _payload={"session_id": "ses_x"})

    assert out["degraded"] is True
    assert any("spec" in m for m in out["missing"])
    assert out["only_in_plan"] == ["x"]


def test_plan_diff_exige_session_id():
    out = handle_plan_diff(_Ctx(), _payload={})

    assert "error" in out
