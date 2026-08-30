"""Tests BDD (pytest-bdd) — Certificación del indexado CPG (SPEC-002 G-W1).

Feature value: SideEffectEngine emite un evento `cpg_indexed` al TraceGraph
cuando un build del CPG (index_code / build_project_graph / update_code_index)
termina exitoso — certificando el espejo CPG↔código (fingerprint_match),
stats del indexado y calidad (SPEC-002).

Se emite DESDE el engine (punto de paso), sin acoplar los handlers modulares:
index_code solo devuelve su contrato (signals con fingerprint_match); el
engine registra la telemetría.

Escenarios: bdd/features/cpg_indexed.feature
"""

from __future__ import annotations

import pytest
from pytest_bdd import given, scenarios, then, when

from shared.core.engine.domain.models import SideEffectDef
from shared.core.engine.infrastructure.side_effects import SideEffectEngine
from shared.common.shared_domain import ExecutionContext

scenarios("bdd/features/cpg_indexed.feature")


class FakeTracer:
    """Tracer de prueba: acumula eventos en memoria (fidelidad SPEC-002)."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def trace(
        self,
        chain_dir: str,
        event_type: str,
        data: dict | None = None,
        task_id: str | None = None,
        session_id: str | None = None,
    ) -> None:
        self.events.append(
            {
                "chain_dir": chain_dir,
                "event_type": event_type,
                "source": "walker",
                "tool": (data or {}).get("tool", ""),
                "data": data or {},
                "task_id": task_id or "",
                "session_id": session_id or "",
            }
        )

    def trace_event(self, event) -> None:
        self.events.append(
            {
                "chain_dir": "",
                "event_type": event.event_type,
                "source": "walker",
                "tool": (event.payload or {}).get("tool", ""),
                "data": event.payload or {},
                "task_id": event.task_id or "",
                "session_id": event.session_id or "",
            }
        )


@pytest.fixture
def se_ctx() -> dict:
    return {}


def _make_ctx() -> ExecutionContext:
    return ExecutionContext.create(
        session_id="test",
        chain_dir="/tmp/fake-chain-cpg-indexed",
        task_id="T1",
    )


def _cpg_ok(fingerprint: bool, quality: bool = True) -> dict:
    from shared.core.engine.domain.side_effect_contract import ok

    return ok(
        type="index_code",
        scope="code_index",
        summary="CPG indexado",
        signals={
            "quality_passed": quality,
            "files_updated": 12,
            "total_nodes": 340,
            "total_edges": 512,
            "fingerprint_match": fingerprint,
        },
        metadata={"source_fingerprint": "abc123"},
    )


def _trace_events(se_ctx: dict, event_type: str) -> list[dict]:
    return [
        e for e in se_ctx["tracer"].events if e["event_type"] == event_type
    ]


# ── Given ────────────────────────────────────────────────────────────────────


@given("SideEffectEngine se construye con un tracer")
def _engine_with_tracer(se_ctx: dict):
    se_ctx["tracer"] = FakeTracer()
    se_ctx["engine"] = SideEffectEngine(tracer=se_ctx["tracer"])


@given("SideEffectEngine se construye sin tracer")
def _engine_without_tracer(se_ctx: dict):
    se_ctx["tracer"] = FakeTracer()
    se_ctx["engine"] = SideEffectEngine(tracer=None)


@given("registro update_cpg con fingerprint verify true y quality ok")
def _register_cpg_ok(se_ctx: dict):
    se_ctx["engine"].register(
        "index_code", lambda ctx, params: _cpg_ok(fingerprint=True)
    )


@given("registro update_cpg con fingerprint verify false y quality ok")
def _register_cpg_no_fp(se_ctx: dict):
    se_ctx["engine"].register(
        "index_code", lambda ctx, params: _cpg_ok(fingerprint=False)
    )


@given("registro update_cpg que lanza")
def _register_cpg_boom(se_ctx: dict):
    def boom(ctx, params):
        raise RuntimeError("build failed")

    se_ctx["engine"].register("index_code", boom)


# ── When ─────────────────────────────────────────────────────────────────────


@when("ejecuto update_cpg con un ExecutionContext con chain_dir")
def _execute_cpg(se_ctx: dict):
    se_ctx["engine"].execute(_make_ctx(), [SideEffectDef(type="index_code")])


# ── Then ─────────────────────────────────────────────────────────────────────


@then("se emite un evento cpg_indexed")
def _assert_emitted(se_ctx: dict):
    assert _trace_events(se_ctx, "cpg_indexed"), "No se emitió cpg_indexed"


@then('el evento cpg_indexed lleva scope "code_index" y quality_passed true')
def _assert_scope_quality(se_ctx: dict):
    ev = _trace_events(se_ctx, "cpg_indexed")[0]
    assert ev["data"]["scope"] == "code_index"
    assert ev["data"]["quality_passed"] is True


@then("el evento cpg_indexed lleva fingerprint_match true")
def _assert_fp_true(se_ctx: dict):
    assert _trace_events(se_ctx, "cpg_indexed")[0]["data"]["fingerprint_match"] is True


@then("el evento cpg_indexed lleva fingerprint_match false")
def _assert_fp_false(se_ctx: dict):
    assert _trace_events(se_ctx, "cpg_indexed")[0]["data"]["fingerprint_match"] is False


@then('el evento cpg_indexed lleva source "walker" y task_id "T1"')
def _assert_source_task(se_ctx: dict):
    ev = _trace_events(se_ctx, "cpg_indexed")[0]
    assert ev["source"] == "walker"
    assert ev["task_id"] == "T1"


@then("el evento cpg_indexed lleva files_updated y total_nodes y total_edges")
def _assert_stats(se_ctx: dict):
    ev = _trace_events(se_ctx, "cpg_indexed")[0]
    assert ev["data"]["files_updated"] == 12
    assert ev["data"]["total_nodes"] == 340
    assert ev["data"]["total_edges"] == 512


@then("el evento cpg_indexed lleva source_fingerprint")
def _assert_source_fp(se_ctx: dict):
    ev = _trace_events(se_ctx, "cpg_indexed")[0]
    assert ev["data"]["source_fingerprint"] == "abc123"


@then("se emite un evento side_effect_execute")
def _assert_side_effect(se_ctx: dict):
    assert _trace_events(se_ctx, "side_effect_execute"), "No se emitió side_effect_execute"


@then("NO se emite ningún evento cpg_indexed")
def _assert_not_emitted(se_ctx: dict):
    assert not _trace_events(se_ctx, "cpg_indexed"), "No debería emitirse cpg_indexed"


@then("no se emite ningún evento cpg_indexed")
def _assert_no_events(se_ctx: dict):
    assert not _trace_events(se_ctx, "cpg_indexed"), "No debería emitirse evento sin tracer"