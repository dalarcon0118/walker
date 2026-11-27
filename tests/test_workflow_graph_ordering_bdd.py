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
    import sqlite3, os
    db_path = os.path.join(str(tmp_path), "data", "cognitive.sqlite3")
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("""CREATE TABLE IF NOT EXISTS workflow_state (
        spec_id TEXT NOT NULL DEFAULT '',
        session_id TEXT NOT NULL DEFAULT '',
        state_json TEXT NOT NULL,
        revision INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (spec_id, session_id)
    )""")
    state = {
        "current_node": node,
        "current_sub_state": sub,
        "sub_state_index": idx,
        "sub_state_data": {},
        "history": [],
        "context": {},
        "revision": 0,
    }
    conn.execute(
        "INSERT OR REPLACE INTO workflow_state (spec_id, session_id, state_json, revision, updated_at) VALUES (?,?,?,?,?)",
        ("", "", json.dumps(state), 0, ""),
    )
    conn.commit()
    conn.close()
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

    assert {"start_task", "edit_plan", "start_review"} <= _gates(ctx)
    # Sin atajo de cierre: el sprint se cierra solo tras review + informe + retro.
    assert "close_sprint" not in _gates(ctx)


def test_report_gate_deja_la_spec_en_review():
    node = json.loads((ROOT / "data/workflow/nodes/implement.node.json").read_text(encoding="utf-8"))
    report = next(ss for ss in node["sub_states"] if ss["key"] == "REPORT_GATE")
    effect = next(se for se in report["side_effects"] if se["type"] == "transform_spec")

    assert effect["params"]["status"] == "in_review"
    assert {"commit_hash", "commit_url", "completed_at"} <= set(effect["params"]["fields"])


def test_advance_no_cruza_un_user_input(tmp_path):
    ctx = _ctx_at(tmp_path, "SPRINT", "PLAN_APPROVAL", 1)

    out = ctx.orchestrator.advance(
        ExecutionContext.create(chain_dir=str(tmp_path), session_id="test"),
        fields=None,
    )

    assert out["ok"] is False
    assert out["action"] == "user_input_required"
    assert "decision" in out["missing"]


def test_contrato_bloquea_sin_decision(tmp_path):
    ctx = _ctx_at(tmp_path, "SPRINT", "PLAN_APPROVAL", 1)

    out = _transition(ctx, tmp_path, "plan_approval_approved")

    assert out["ok"] is False
    assert out["action"] == "contract_precondition_failed"
    assert out["expected"] == "approve"


def test_contrato_permite_con_decision(tmp_path):
    ctx = _ctx_at(tmp_path, "SPRINT", "PLAN_APPROVAL", 1)
    state = ctx.orchestrator._load_state().record_submit(
        "PLAN_APPROVAL", {"decision": "approve"}
    )
    assert ctx.orchestrator._save_state(state)

    out = _transition(ctx, tmp_path, "plan_approval_approved")

    assert out["ok"] is True
    assert out["to_sub_state"] == "TASK_SPEC"


def test_task_complete_exige_accept(tmp_path):
    ctx = _ctx_at(tmp_path, "VERIFY", "PO_ACCEPT", 5)
    state = ctx.orchestrator._load_state().record_submit(
        "PO_ACCEPT", {"decision": "reject"}
    )
    assert ctx.orchestrator._save_state(state)

    out = _transition(ctx, tmp_path, "task_complete")

    assert out["ok"] is False
    assert out["action"] == "contract_precondition_failed"
    assert out["expected"] == "accept"


def test_design_approved_solo_despues_de_ckg_save(tmp_path):
    review = _ctx_at(tmp_path, "DESIGN", "DESIGN_REVIEW", 3)
    assert "design_approved" not in _gates(review)

    save_dir = tmp_path / "save"
    save_dir.mkdir()
    save = _ctx_at(save_dir, "DESIGN", "CKG_SAVE", 4)
    assert "design_approved" in _gates(save)


def test_design_approved_lee_la_decision_de_design_review(tmp_path):
    ctx = _ctx_at(tmp_path, "DESIGN", "CKG_SAVE", 4)
    # CKG_SAVE declara `documenting_knowledge`: un gate de avance no puede
    # salir de un paso con su acción sin ejecutar. Se registra la ejecución
    # (el flujo real la corre con `advance`) para probar sólo la decisión.
    state = ctx.orchestrator._load_state().record_submit(
        "DESIGN_REVIEW", {"decision": "approve"}
    ).record_node_execution(
        node="DESIGN", sub_state="CKG_SAVE", action="documenting_knowledge",
        task_ok=True,
    )
    assert ctx.orchestrator._save_state(state)

    out = ctx.orchestrator.transition(
        ExecutionContext.create(chain_dir=str(tmp_path), session_id="test"),
        gate="design_approved",
    )

    assert out["ok"] is True
    assert out["to_node"] == "ANALYZE"
    assert out["to_sub_state"] == "GATHER"


def test_po_accept_es_el_que_cierra_con_evidencia():
    node = json.loads((ROOT / "data/workflow/nodes/verify.node.json").read_text(encoding="utf-8"))
    accept = next(ss for ss in node["sub_states"] if ss["key"] == "PO_ACCEPT")
    effect = next(se for se in accept["side_effects"] if se["type"] == "transform_spec")

    assert effect["params"]["status"] == "completed"
    assert effect["params"]["fields"]["review_verdict"] == "NODE.QA.output.decision"
    # El evidence lo produce VERIFY.QA; PO_ACCEPT solo declara `feedback`.
    assert effect["params"]["fields"]["evidence"] == "NODE.QA.output.evidence"
