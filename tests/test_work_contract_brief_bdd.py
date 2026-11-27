"""BDD: brief y preflight proyectan el WorkContract y los controles 4D (F0c)."""

from __future__ import annotations

from fast_mcp.memories.handlers import knowledge


def _spec_with_contract():
    return {
        "spec_id": "SPEC-003",
        "task_context": {
            "task_id": "SPEC-003",
            "work_contract": {
                "version": "1",
                "goal": "proyectar el contrato",
                "scope": ["a.py"],
                "criteria": ["brief expone el contrato"],
                "verification": [{"criterion": "tests", "check": "test"}],
                "budget": {"max_files": 3},
                "recurrence": {"level": "full"},
                "reuse": {"candidates": ["x"]},
            },
        },
    }


def test_contract_projection_expone_el_contrato_y_controles():
    proj = knowledge._contract_projection(_spec_with_contract())

    assert proj["present"] is True
    assert proj["contract"]["goal"] == "proyectar el contrato"
    # Los cuatro controles 4D + recurrencia presentes:
    assert {"goal", "verification", "budget", "recurrence", "reuse"} <= set(proj["controls"])
    assert proj["controls"]["verification"][0]["check"] == "test"


def test_contract_projection_sin_contrato_no_inventa():
    proj = knowledge._contract_projection({"spec_id": "SPEC-X", "task_context": {}})

    assert proj["present"] is False
    assert proj["contract"] == {}
    assert proj["controls"] == {}


def test_preflight_incluye_bloque_contract(monkeypatch):
    monkeypatch.setattr(knowledge, "_find_task_spec", lambda chain, tid: _spec_with_contract())

    class _Ctx:
        chain_dir = "/tmp/x"
        project_id = "walker"
        task_id = "SPEC-003"
        project_root = "/tmp/x"

        def code_context_service(self):
            raise RuntimeError("sin cpg")

        def code_analysis_service(self):
            raise RuntimeError("sin ca")

    out = knowledge.handle_preflight(_Ctx(), _payload={"files": ["a.py"], "task_id": "SPEC-003"})

    assert "contract" in out
    assert out["contract"]["present"] is True
    assert out["contract"]["controls"]["goal"] == "proyectar el contrato"
