"""BDD: control de reuse advisory (F1a / SPEC-004).

  - DESIGN_ANALYZE rechaza un `reuse_decision` sin candidatos.
  - Los candidatos se buscan en CKG, Delta y CPG.
  - El contexto de diseño inyecta el bloque `reuse`.
"""

from __future__ import annotations

from types import SimpleNamespace

from fast_mcp.features.tasks.handlers import design
from fast_mcp.features.workflow.application.prompt_context import spec_context
from fast_mcp.features.workflow.domain.artifacts import validate_artifact


def _valid_brief() -> dict:
    return {
        "problem": "problema suficientemente largo",
        "options": [
            {"id": "A", "summary": "a", "cost": "bajo", "risk": "bajo"},
            {"id": "B", "summary": "b", "cost": "alto", "risk": "alto"},
        ],
        "chosen": "A",
        "rejected_because": {"B": "no sirve"},
        "invariants_touched": ["INV-004"],
        "verification": [{"criterion": "c", "check": "test", "ref": "t.py"}],
    }


def test_reuse_opcional_no_bloquea_sin_decision():
    payload = {"design_brief": _valid_brief()}
    assert validate_artifact("design_and_reuse", payload) is None


def test_reuse_decision_sin_candidatos_es_rechazado():
    payload = {"design_brief": _valid_brief(), "reuse_decision": {"candidates": []}}
    err = validate_artifact("design_and_reuse", payload)
    assert err is not None
    assert any("candidates" in m for m in err["missing"])


def test_reuse_decision_con_candidato_valido_pasa():
    payload = {
        "design_brief": _valid_brief(),
        "reuse_decision": {"candidates": [{"source": "ckg", "ref": "adr-1", "reason": "x"}]},
    }
    assert validate_artifact("design_and_reuse", payload) is None


def test_reuse_decision_source_invalido_es_rechazado():
    payload = {
        "design_brief": _valid_brief(),
        "reuse_decision": {"candidates": [{"source": "otro", "ref": "x"}]},
    }
    assert validate_artifact("design_and_reuse", payload) is not None


def test_reuse_candidates_busca_en_ckg_delta_cpg(monkeypatch):
    monkeypatch.setattr(
        design,
        "_memory_runner",
        lambda name: (lambda **kw: {
            "ckg": {"decisions": [{"id": "adr-1", "statement": "reuse X"}]},
            "cpg": {"symbols": [{"name": "foo"}]},
        }),
    )

    class _Svc:
        def get_session(self, sid):
            return {"spring_id": "SPR-1"}

        def get_active_spring(self):
            return {"spring_id": "SPR-1"}

        def get_specs(self, spring):
            return [
                {"spec_id": "SPEC-001", "objective": "otra"},
                {"spec_id": "SPEC-004", "objective": "self"},
            ]

    import fast_mcp.composition as composition

    monkeypatch.setattr(composition, "build_sprint_service", lambda chain: _Svc())

    ctx = SimpleNamespace(chain_dir="/tmp/x", task_id="SPEC-004", session_id="s1", cpg_adapter=None)
    out = design.reuse_candidates(ctx, {"query": "reuse"})

    assert out["ckg"] and out["ckg"][0]["ref"] == "adr-1"
    assert out["cpg"] and out["cpg"][0]["ref"] == "foo"
    refs = {d["ref"] for d in out["delta"]}
    assert "SPEC-001" in refs
    assert "SPEC-004" not in refs  # no se propone a sí misma


def test_spec_context_inyecta_reuse():
    spec = {
        "spec_id": "SPEC-004",
        "objective": "reuse",
        "task_context": {"work_contract": {"reuse": {"candidates": ["x"]}}},
    }
    out = spec_context(spec)
    assert "reuse" in out
