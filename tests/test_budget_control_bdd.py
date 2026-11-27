"""BDD: budget control advisory (F1d / SPEC-007).

  - la spec puede declarar budget max_files/max_loc/max_new_files;
  - implement_task toma un snapshot del diff;
  - el checkpoint de review expone what_changed y plan_diff.
"""

from __future__ import annotations

from types import SimpleNamespace

from fast_mcp.features.delta_tracking.domain.spec_service import SpecService, check_budget
from fast_mcp.features.tasks.handlers import implement as impl


def test_check_budget_dentro_del_presupuesto():
    res = check_budget({"max_files": 3, "max_loc": 100, "max_new_files": 1}, 2, 50, 1)
    assert res["ok"] is True
    assert res["violations"] == []


def test_check_budget_reporta_violaciones():
    res = check_budget({"max_files": 1, "max_loc": 10, "max_new_files": 0}, 3, 50, 2)
    assert res["ok"] is False
    assert len(res["violations"]) == 3


def test_check_budget_spec_service_alias():
    res = SpecService.check_budget({"max_files": 2}, changed_files=3)
    assert res["ok"] is False


def test_implement_task_toma_snapshot_del_diff(monkeypatch):
    snapshot = {"files": 2, "loc": 10, "new_files": 1, "at": "2026-01-01T00:00:00Z"}
    monkeypatch.setattr(impl, "_diff_snapshot", lambda chain_dir: snapshot)

    class _NoService:
        def list_springs(self):
            return []

    ctx = SimpleNamespace(
        chain_dir="/tmp/x",
        task_id="SPEC-007",
        session_id="",
        cpg_adapter=None,
    )
    out = impl.implement_task(
        ctx,
        {
            "task_id": "SPEC-007",
            "files": ["a.py", "b.py"],
            "acceptance": ["los tests pasan"],
        },
    )

    assert out.ok is True
    assert out.data["budget_snapshot"] == snapshot


def test_brief_expone_budget_checkpoint(monkeypatch):
    # handle_brief es pesado; verificamos que el bloque budget_checkpoint se
    # agrega con las claves esperadas cuando se llama a un ctx mínimo.
    # Se monkeypatchea _find_task_spec para devolver una spec con contrato.
    from fast_mcp.memories.handlers import knowledge as kn

    monkeypatch.setattr(
        kn,
        "_find_task_spec",
        lambda chain, tid: {
            "spec_id": "SPEC-007",
            "objective": "budget",
            "files": ["a.py"],
            "acceptance_criteria": ["c"],
            "predicates": {},
            "task_context": {
                "work_contract": {"budget": {"max_files": 2}, "goal": "g"}
            },
        },
    )

    class _Ctx:
        chain_dir = "/tmp/x"
        project_id = "walker"
        task_id = "SPEC-007"
        project_root = "/tmp/x"
        session_id = "s1"

        def code_context_service(self):
            raise RuntimeError("sin cpg")

        def code_analysis_service(self):
            raise RuntimeError("sin ca")

    out = kn.handle_brief(_Ctx(), _payload={"task_id": "SPEC-007", "files": ["a.py"]})

    assert "budget_checkpoint" in out
    assert (out["budget_checkpoint"].get("budget") or {}).get("max_files") == 2
    assert "plan_diff" in out["budget_checkpoint"]
    assert "what_changed" in out["budget_checkpoint"]
