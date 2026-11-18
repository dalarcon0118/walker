"""BDD: el grafo respeta el orden (source_sub_state) y el cierre ocurre en VERIFY.

Regresiones medidas 2026-11-18:
- `source_sub_state` existía en el JSON pero el loader leía `from_sub`, así que
  los 34 edges eran globales: `all_done` desde PLANNING y `start_task` desde
  TASK_REVIEW pasaban.
- `IMPLEMENT.REPORT_GATE` marcaba `completed` antes de VERIFY y VERIFY no
  escribía `review_verdict`/`evidence`.
"""
from __future__ import annotations

import json
from pathlib import Path

from fast_mcp.features.workflow.context import WorkflowContext
from fast_mcp.features.workflow.domain.execution import ExecutionContext
from fast_mcp.features.workflow.domain.services.validation import ValidationService

ROOT = Path(__file__).resolve().parents[1]


def _ctx_at(tmp_path: Path, node: str, sub: str, idx: int) -> WorkflowContext:
    (tmp_path / ".workflow-state.json").write_text(
        json.dumps(
            {
                "current_node": node,
                "current_sub_state": sub,
                "sub_state_index": idx,
                "sub_state_data": {},
                "history": [],
                "context": {},
                "revision": 0,
            }
        ),
        encoding="utf-8",
    )
    ctx = WorkflowContext.resolve(str(tmp_path))
    assert ctx is not None
    return ctx


def _gates(ctx: WorkflowContext) -> set[str]:
    state = ctx.orchestrator._load_state()
    graph = ctx.orchestrator._load_graph()
    return {g.name for g in ValidationService.get_available_gates(state, graph)}


def _transition(ctx: WorkflowContext, tmp_path: Path, gate: str) -> dict:
    return ctx.orchestrator.transition(
        ExecutionContext.create(chain_dir=str(tmp_path), session_id="test"), gate=gate
    )


def test_planning_no_puede_saltar_a_active(tmp_path):
    ctx = _ctx_at(tmp_path, "SPRINT", "PLANNING", 0)

    assert "all_done" not in _gates(ctx)
    out = _transition(ctx, tmp_path, "all_done")

    assert out["ok"] is False
    assert out["action"] == "invalid_source_sub_state"


def test_start_task_solo_desde_active(tmp_path):
    ctx = _ctx_at(tmp_path, "SPRINT", "TASK_REVIEW", 3)

    assert "start_task" not in _gates(ctx)
    out = _transition(ctx, tmp_path, "start_task")

    assert out["ok"] is False
    assert out["action"] == "invalid_source_sub_state"


def test_task_review_expone_las_salidas_de_revision(tmp_path):
    ctx = _ctx_at(tmp_path, "SPRINT", "TASK_REVIEW", 3)

    assert {"refine_task", "next_task", "all_done"} <= _gates(ctx)


def test_active_expone_el_flujo_principal(tmp_path):
    ctx = _ctx_at(tmp_path, "SPRINT", "ACTIVE", 4)

    assert {"start_task", "edit_plan", "start_review", "close_sprint"} <= _gates(ctx)


def test_report_gate_deja_la_spec_en_review():
    node = json.loads((ROOT / "data/workflow/nodes/implement.node.json").read_text(encoding="utf-8"))
    report = next(ss for ss in node["sub_states"] if ss["key"] == "REPORT_GATE")
    effect = next(se for se in report["side_effects"] if se["type"] == "transform_spec")

    assert effect["params"]["status"] == "in_review"
    assert {"commit_hash", "commit_url", "completed_at"} <= set(effect["params"]["fields"])


def test_po_accept_es_el_que_cierra_con_evidencia():
    node = json.loads((ROOT / "data/workflow/nodes/verify.node.json").read_text(encoding="utf-8"))
    accept = next(ss for ss in node["sub_states"] if ss["key"] == "PO_ACCEPT")
    effect = next(se for se in accept["side_effects"] if se["type"] == "transform_spec")

    assert effect["params"]["status"] == "completed"
    assert effect["params"]["fields"]["review_verdict"] == "NODE.QA.output.decision"
    assert effect["params"]["fields"]["evidence"] == "NODE.PO_ACCEPT.output.evidence"
