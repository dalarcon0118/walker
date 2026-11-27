"""BDD: goal control advisory (F1b / SPEC-005).

  - cada spec expone el goal que la justifica;
  - la cadena goal→spec→criterio→archivo es visible;
  - los criterios sin predicado quedan en `unmapped`.
"""

from __future__ import annotations

from fast_mcp.features.tasks.handlers.spec_derive import goal_chain


def _spec() -> dict:
    return {
        "spec_id": "SPEC-005",
        "objective": "objetivo de la spec",
        "files": ["fast_mcp/x.py"],
        "acceptance_criteria": ["c1", "c2"],
        "predicates": {
            "items": [{"criterion": "c1", "type": "command_exit", "command": "pytest"}],
            "unmapped": [{"criterion": "c2", "reason": "sin predicado ejecutable"}],
        },
        "task_context": {"work_contract": {"goal": "goal que justifica"}},
    }


def test_goal_chain_expone_el_goal_del_contrato():
    gc = goal_chain(_spec())
    assert gc["goal"] == "goal que justifica"
    assert gc["spec_id"] == "SPEC-005"


def test_goal_chain_cae_al_objective_sin_contrato():
    spec = _spec()
    del spec["task_context"]
    assert goal_chain(spec)["goal"] == "objetivo de la spec"


def test_cadena_goal_spec_criterio_archivo():
    gc = goal_chain(_spec())
    rows = {r["criterion"]: r for r in gc["chain"]}

    assert rows["c1"]["status"] == "mapped"
    assert rows["c1"]["files"] == ["fast_mcp/x.py"]
    assert rows["c1"]["predicate"]["command"] == "pytest"
    assert rows["c2"]["status"] == "unmapped"


def test_criterio_sin_predicado_queda_en_unmapped():
    gc = goal_chain(_spec())
    criterios = {u["criterion"] for u in gc["unmapped"]}
    assert "c2" in criterios


def test_unmapped_del_bundle_no_se_pierde():
    spec = _spec()
    spec["acceptance_criteria"] = ["c1"]  # c2 vive solo en el bundle
    gc = goal_chain(spec)
    assert any(u["criterion"] == "c2" for u in gc["unmapped"])
