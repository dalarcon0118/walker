"""
Tests BDD (pytest-bdd) — Quality gates y dispatch de side-effects del CPG.

Escenarios: tests/features/cpg_quality_gates.feature

Cubre side_effect_contract (envelope ok/failed/degraded) y el dispatch del
orchestrator que dispara quality_gate_failed cuando quality_passed=False.
"""

from __future__ import annotations

import pytest
from pytest_bdd import given, scenarios, then, when

scenarios("features/cpg_quality_gates.feature")


@pytest.fixture
def qg_ctx() -> dict:
    return {}


# ── Escenario 1: envelope estándar ───────────────────────────────


@given("un envelope ok de update_cpg")
def _bdd_envelope(qg_ctx: dict):
    from features.engine.domain.side_effect_contract import ok

    qg_ctx["envelope"] = ok(
        type="update_cpg",
        scope="code_index",
        summary="CPG indexado",
        signals={"quality_passed": True},
    )


@then("el envelope es estándar")
def _bdd_envelope_estandar(qg_ctx: dict):
    from features.engine.domain.side_effect_contract import is_standard

    assert is_standard(qg_ctx["envelope"])


@then("su status es ok")
def _bdd_status_ok(qg_ctx: dict):
    assert qg_ctx["envelope"]["status"] == "ok"


@then("su scope es code_index")
def _bdd_scope(qg_ctx: dict):
    assert qg_ctx["envelope"]["scope"] == "code_index"


# ── Escenario 2: quality gate fallido ────────────────────────────


@given("un resultado update_cpg con quality_passed en False")
def _bdd_result_fail(qg_ctx: dict):
    from features.engine.domain.side_effect_contract import ok

    qg_ctx["result"] = {
        "side_effects": [
            {
                "type": "update_cpg",
                "ok": True,
                "result": ok(
                    type="update_cpg",
                    scope="code_index",
                    summary="CPG indexado; cyclo falla",
                    signals={
                        "quality_passed": False,
                        "failed_gate_names": ["cyclomatic_complexity"],
                        "recommend": "refactorizar_primero",
                    },
                ),
            }
        ]
    }


@when("el orchestrator despacha los side-effects")
def _bdd_dispatch(qg_ctx: dict):
    from features.engine.application.orchestrator import OrchestrationService

    svc = OrchestrationService()

    class FakeWorkflowRepo:
        def load(self, chain_dir):
            class W:
                def find_event_handler(self, name):
                    if name == "quality_gate_failed":
                        return {"transition": "needs_refine"}
                    return None

            return W()

    class FakeStateRepo:
        def load(self, chain_dir):
            class S:
                current_node = "IMPLEMENT"
                current_sub_state = "WORK"

            return S()

    class FakeTransitionUC:
        def execute(self, ctx, gate_name):
            return {"to_node": "REFINE"}

    class FakeTracer:
        def trace(self, *a, **k):
            pass

    class FakeCtx:
        chain_dir = "/tmp/chain"
        task_id = "T1"
        session_id = "s1"
        trace_id = "t1"
        started_at = "2026-01-01"

    svc._workflow_repo = FakeWorkflowRepo()
    svc._state_repo = FakeStateRepo()
    svc._transition_uc = FakeTransitionUC()
    svc._tracer = FakeTracer()

    qg_ctx["count"] = svc._dispatch_events_from_side_effects(FakeCtx(), qg_ctx["result"])


@then("se dispara quality_gate_failed")
def _bdd_dispara(qg_ctx: dict):
    assert qg_ctx["count"] == 1


@then("el workflow tiene failed_gates con cyclomatic_complexity")
def _bdd_failed_gates(qg_ctx: dict):
    assert qg_ctx["result"]["quality"]["last_scan"]["failed_gates"] == [
        "cyclomatic_complexity"
    ]


# ── Escenario 3: quality aprobado ────────────────────────────────


@given("un resultado update_cpg con quality_passed en True")
def _bdd_result_ok(qg_ctx: dict):
    from features.engine.domain.side_effect_contract import ok

    qg_ctx["result"] = {
        "side_effects": [
            {
                "type": "update_cpg",
                "ok": True,
                "result": ok(
                    type="update_cpg",
                    scope="code_index",
                    summary="CPG indexado; calidad OK",
                    signals={"quality_passed": True, "recommend": "indexar_ok"},
                ),
            }
        ]
    }


@then("no se dispara ningún evento")
def _bdd_no_dispara():
    from features.engine.application.orchestrator import OrchestrationService

    svc = OrchestrationService()

    class FakeWorkflowRepo:
        def find_event_handler(self, name):
            return None

    class FakeTransitionCtx:
        def execute(self, ctx, gate_name):
            return {"to_node": "REFINE"}

    class FakeTracer:
        def trace(self, *a, **k):
            pass

    class FakeCtx:
        chain_dir = "/tmp/chain"

    svc._workflow_repo = FakeWorkflowRepo()
    svc._transition_uc = FakeTransitionCtx()
    svc._tracer = FakeTracer()

    from features.engine.domain.side_effect_contract import ok

    result = {
        "side_effects": [
            {
                "type": "update_cpg",
                "ok": True,
                "result": ok(
                    type="update_cpg",
                    scope="code_index",
                    summary="CPG indexado; calidad OK",
                    signals={"quality_passed": True, "recommend": "indexar_ok"},
                ),
            }
        ]
    }
    count = svc._dispatch_events_from_side_effects(FakeCtx(), result)
    assert count == 0