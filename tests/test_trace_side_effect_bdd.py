"""Tests BDD (pytest-bdd) — Trazabilidad de side-effects (SPEC-002 G-W1).

Feature value: SideEffectEngine emite un evento `side_effect_execute` al
TraceGraph por cada side-effect que ejecuta (ok / fallo / idempotente),
desde el punto de paso (execute) — sin acoplar los handlers modulares.

Escenarios: bdd/features/trace_side_effect.feature
"""

from __future__ import annotations

import time

import pytest
from pytest_bdd import given, scenarios, then, when

from shared.core.engine.domain.models import SideEffectDef
from shared.core.engine.infrastructure.side_effects import SideEffectEngine
from shared.common.shared_domain import ExecutionContext

scenarios("bdd/features/trace_side_effect.feature")


class FakeTracer:
    """Tracer de prueba: acumula eventos en memoria."""

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
        # Fidelidad al contrato SPEC-002: el evento persistido lleva source
        # (default "walker" en TraceEvent.from_dict) y tool/task_id a nivel
        # de evento (TraceLogger cg.store).
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


@pytest.fixture
def se_ctx() -> dict:
    return {}


def _make_ctx() -> ExecutionContext:
    return ExecutionContext.create(
        session_id="test",
        chain_dir="/tmp/fake-chain-gw1",
        task_id="T1",
    )


def _standard_ok(tool: str, scope: str = "tracking", **signals) -> dict:
    from shared.core.engine.domain.side_effect_contract import ok

    if not signals:
        signals = {"quality_passed": True}
    return ok(type=tool, scope=scope, signals=signals)


def _trace_events(se_ctx: dict) -> list[dict]:
    return [e for e in se_ctx["tracer"].events if e["event_type"] == "side_effect_execute"]


# ── Given ────────────────────────────────────────────────────────────────────


@given("SideEffectEngine se construye con un tracer")
def _engine_with_tracer(se_ctx: dict):
    se_ctx["tracer"] = FakeTracer()
    se_ctx["engine"] = SideEffectEngine(tracer=se_ctx["tracer"])


@given("SideEffectEngine se construye sin tracer")
def _engine_without_tracer(se_ctx: dict):
    se_ctx["tracer"] = FakeTracer()
    se_ctx["engine"] = SideEffectEngine(tracer=None)


@given('registro un side-effect exitoso "ok_effect"')
def _register_ok(se_ctx: dict):
    se_ctx["engine"].register(
        "ok_effect",
        lambda ctx, params: _standard_ok("ok_effect", quality_passed=True),
    )


@given('registro un side-effect que lanza "fail_effect"')
def _register_fail(se_ctx: dict):
    def boom(ctx, params):
        raise RuntimeError("boom")

    se_ctx["engine"].register("fail_effect", boom)


@given('ejecuto ese side-effect con idempotency_key "k1"')
def _execute_idempotent_first(se_ctx: dict):
    se_ctx["engine"].execute(
        _make_ctx(), [SideEffectDef(type="ok_effect", idempotency_key="k1")]
    )


# ── When ─────────────────────────────────────────────────────────────────────


@when("ejecuto ese side-effect con un ExecutionContext con chain_dir")
def _execute(se_ctx: dict):
    se_ctx["engine"].execute(_make_ctx(), [SideEffectDef(type="ok_effect")])


@when('ejecuto ese side-effect "fail_effect" con un ExecutionContext con chain_dir')
def _execute_fail(se_ctx: dict):
    from shared.core.engine.domain.models import SideEffectDef

    se_ctx["engine"].execute(_make_ctx(), [SideEffectDef(type="fail_effect")])


@when('ejecuto el mismo side-effect de nuevo con idempotency_key "k1"')
def _execute_idempotent_second(se_ctx: dict):
    se_ctx["engine"].execute(
        _make_ctx(), [SideEffectDef(type="ok_effect", idempotency_key="k1")]
    )


@when('ejecuto un side-effect "ok_effect" sin tracer')
def _execute_no_tracer(se_ctx: dict):
    se_ctx["engine"].register(
        "ok_effect",
        lambda ctx, params: _standard_ok("ok_effect", quality_passed=True),
    )
    se_ctx["engine"].execute(_make_ctx(), [SideEffectDef(type="ok_effect")])


# ── Then ─────────────────────────────────────────────────────────────────────


@then("se emite un evento side_effect_execute")
def _assert_emitted(se_ctx: dict):
    assert _trace_events(se_ctx), "No se emitió side_effect_execute"


@then('el evento lleva tool "ok_effect" y ok true')
def _assert_ok_fields(se_ctx: dict):
    ev = _trace_events(se_ctx)[0]
    assert ev["data"]["tool"] == "ok_effect"
    assert ev["data"]["ok"] is True


@then('el evento lleva tool "fail_effect" y ok false')
def _assert_fail_fields(se_ctx: dict):
    ev = _trace_events(se_ctx)[0]
    assert ev["data"]["tool"] == "fail_effect"
    assert ev["data"]["ok"] is False


@then('el evento lleva channel "workflow" y scope "tracking"')
def _assert_channel_scope(se_ctx: dict):
    ev = _trace_events(se_ctx)[0]
    assert ev["data"]["channel"] == "workflow"
    assert ev["data"]["scope"] == "tracking"


@then('el evento lleva source "walker"')
def _assert_source(se_ctx: dict):
    ev = _trace_events(se_ctx)[0]
    assert ev["source"] == "walker"


@then('el evento lleva task_id "T1"')
def _assert_task_id(se_ctx: dict):
    ev = _trace_events(se_ctx)[0]
    assert ev["task_id"] == "T1"


@then("el primer evento lleva idempotent false")
def _assert_not_idempotent(se_ctx: dict):
    assert _trace_events(se_ctx)[0]["data"]["idempotent"] is False


@then("el evento lleva duration_ms y signals_keys")
def _assert_telemetry(se_ctx: dict):
    ev = _trace_events(se_ctx)[0]
    assert isinstance(ev["data"]["duration_ms"], int)
    assert isinstance(ev["data"]["signals_keys"], list)
    assert "quality_passed" in ev["data"]["signals_keys"]


@then('el evento NO incluye el campo "data" en signals_keys')
def _assert_no_data_key(se_ctx: dict):
    ev = _trace_events(se_ctx)[0]
    assert "data" not in ev["data"]["signals_keys"]


@then("se emiten 2 eventos side_effect_execute")
def _assert_two_events(se_ctx: dict):
    assert len(_trace_events(se_ctx)) == 2


@then("el segundo evento lleva idempotent true")
def _assert_idempotent(se_ctx: dict):
    assert _trace_events(se_ctx)[1]["data"]["idempotent"] is True


@then("no se emite ningún evento side_effect_execute")
def _assert_no_events(se_ctx: dict):
    assert not _trace_events(se_ctx), "No debería emitirse evento sin tracer"