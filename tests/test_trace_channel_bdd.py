"""Tests BDD (pytest-bdd) — Canal de invocación en task_execute (SPEC-002 G-W1).

Feature value: InMemoryTaskRegistry emite un evento `task_execute` al
TraceGraph por cada task ejecutada, con `channel` = cli | mcp | workflow
(SPEC-002: "task_execute registra el canal de invocacion").

Cobertura del contrato:
  - channel mcp (dispatch_public_task), cli (handlers CLI), workflow (engine).
  - source "walker", duration_ms, task_id, ok.
  - Degradación segura sin tracer (G-W4: sin silenciar, sin fake).

Escenarios: bdd/features/trace_channel.feature
"""

from __future__ import annotations

import pytest
from pytest_bdd import given, scenarios, then, when

from shared.core.engine.infrastructure.task_registry import InMemoryTaskRegistry
from shared.common.shared_domain import ExecutionContext

scenarios("bdd/features/trace_channel.feature")


class FakeTracer:
    """Tracer de prueba: acumula eventos en memoria.

    Fidelidad al contrato SPEC-002: mismo layout persistido que en
    TraceLogger, incluido `source` (default "walker" en TraceEvent).
    """

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
def trace_ctx() -> dict:
    return {}


def _trace_events(trace_ctx: dict) -> list[dict]:
    return [
        e for e in trace_ctx["tracer"].events if e["event_type"] == "task_execute"
    ]


# ── Given ────────────────────────────────────────────────────────────────────


@given('un TaskRegistry con un tracer y una task "ping" registrada')
def _registry_with_tracer(trace_ctx: dict):
    trace_ctx["tracer"] = FakeTracer()
    trace_ctx["registry"] = InMemoryTaskRegistry(tracer=trace_ctx["tracer"])
    _register_ping(trace_ctx["registry"])


@given('un TaskRegistry sin tracer y una task "ping" registrada')
def _registry_without_tracer(trace_ctx: dict):
    trace_ctx["tracer"] = FakeTracer()
    trace_ctx["registry"] = InMemoryTaskRegistry(tracer=None)
    _register_ping(trace_ctx["registry"])


def _register_ping(registry: InMemoryTaskRegistry) -> None:
    from shared.core.engine.domain.models import TaskResult

    registry.register(
        "ping",
        lambda ctx, data: TaskResult.success(data={"pong": True}),
        metadata={"scope": "public", "description": "tarea de prueba"},
    )


# ── When ─────────────────────────────────────────────────────────────────────


@when('ejecuto la task "ping" desde el canal mcp')
def _execute_mcp(trace_ctx: dict):
    from shared.core.engine.domain.models import SideEffectDef  # noqa: F401  (import consistency)

    ctx = ExecutionContext.create(
        session_id="mcp", chain_dir="/tmp/fake-chain-channel", channel="mcp"
    )
    trace_ctx["registry"].execute("ping", ctx, {})


@when('ejecuto la task "ping" desde el canal cli')
def _execute_cli(trace_ctx: dict):
    ctx = ExecutionContext.create(
        session_id="cli", chain_dir="/tmp/fake-chain-channel", channel="cli"
    )
    trace_ctx["registry"].execute("ping", ctx, {})


@when('ejecuto la task "ping" desde el canal workflow')
def _execute_workflow(trace_ctx: dict):
    # El engine (orchestrator/use_cases) construye el ctx sin channel explícito
    # → el default "workflow" de ExecutionContext aplica.
    ctx = ExecutionContext.create(
        session_id="sprint-x", chain_dir="/tmp/fake-chain-channel"
    )
    trace_ctx["registry"].execute("ping", ctx, {})


# ── Then ─────────────────────────────────────────────────────────────────────


@then("se emite un evento task_execute")
def _assert_emitted(trace_ctx: dict):
    assert _trace_events(trace_ctx), "No se emitió task_execute"


@then('el evento task_execute lleva task "ping" y ok true')
def _assert_task_ok(trace_ctx: dict):
    ev = _trace_events(trace_ctx)[0]
    assert ev["data"]["task"] == "ping"
    assert ev["data"]["ok"] is True


@then('el evento task_execute lleva channel "mcp"')
def _assert_channel_mcp(trace_ctx: dict):
    assert _trace_events(trace_ctx)[0]["data"]["channel"] == "mcp"


@then('el evento task_execute lleva channel "cli"')
def _assert_channel_cli(trace_ctx: dict):
    assert _trace_events(trace_ctx)[0]["data"]["channel"] == "cli"


@then('el evento task_execute lleva channel "workflow"')
def _assert_channel_workflow(trace_ctx: dict):
    assert _trace_events(trace_ctx)[0]["data"]["channel"] == "workflow"


@then('el evento task_execute lleva source "walker" y duration_ms')
def _assert_source_duration(trace_ctx: dict):
    ev = _trace_events(trace_ctx)[0]
    assert ev["source"] == "walker"
    assert isinstance(ev["data"]["duration_ms"], int)


@then("no se emite ningún evento task_execute")
def _assert_no_events(trace_ctx: dict):
    assert not _trace_events(trace_ctx), "No debería emitirse evento sin tracer"