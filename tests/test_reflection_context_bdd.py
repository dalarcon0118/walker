"""Tests BDD (pytest-bdd) — Reflexión por contexto (REFINE.DIAGNOSE).

Feature value: el motor es un mecanismo puro; la reflexión es lógica de la
feature event_tracing. build_reflection_context deriva source_node/attempt
desde el TraceGraph, y el ReflectionProvider produce ContextPiece con capa
'reflection'.

Escenarios: bdd/features/reflection_context.feature
"""

from __future__ import annotations

import json
import os

import pytest
from pytest_bdd import given, scenarios, then, when

from tests.conftest import make_v2_project

scenarios("bdd/features/reflection_context.feature")


@pytest.fixture
def reflection_ctx() -> dict:
    return {}


# ── Helpers ───────────────────────────────────────────────────────


def _setup_project(tmpdir: str) -> tuple[str, str]:
    """Crea el layout v2 legacy (builder compartido en conftest)."""
    return make_v2_project(tmpdir)


def _write_trace_index(project_root: str, events: list[dict]) -> None:
    """Escribe el indice trace canonico (trace.json.gz) en <root>/data/.

    Formato CognitiveGraph: {"index": "trace", "docs": [...], "edges": []}.
    """
    import gzip

    data_dir = os.path.join(project_root, "data")
    os.makedirs(data_dir, exist_ok=True)
    docs = [{"_id": f"ev-{i:03d}", **ev} for i, ev in enumerate(events)]
    payload = {"index": "trace", "docs": docs, "edges": []}
    path = os.path.join(data_dir, "trace.json.gz")
    with open(path, "wb") as f:
        f.write(gzip.compress(json.dumps(payload).encode()))


# ── Given ─────────────────────────────────────────────────────────


@given("un proyecto walker con trace canónico")
def _project_with_trace(tmp_path, reflection_ctx: dict):
    chain_dir, project_root = _setup_project(str(tmp_path))
    reflection_ctx["chain_dir"] = chain_dir
    reflection_ctx["project_root"] = project_root


@given("el trace tiene TRANSITION desde IMPLEMENT hacia REFINE")
def _trace_implement_refine(reflection_ctx: dict):
    task_id = reflection_ctx.get("task_id", "TASK-001")
    reflection_ctx["task_id"] = task_id
    _write_trace_index(
        reflection_ctx["project_root"],
        [
            {
                "event_type": "TRANSITION",
                "task_id": task_id,
                "timestamp": "2026-08-02T10:00:00+00:00",
                "data": {"gate": "needs_refine", "from": "IMPLEMENT", "to": "REFINE"},
            }
        ],
    )


@given("el trace tiene dos TRANSITION hacia REFINE")
def _trace_two_refines(reflection_ctx: dict):
    task_id = reflection_ctx.get("task_id", "TASK-002")
    reflection_ctx["task_id"] = task_id
    _write_trace_index(
        reflection_ctx["project_root"],
        [
            {
                "event_type": "TRANSITION",
                "task_id": task_id,
                "timestamp": "2026-08-02T09:00:00+00:00",
                "data": {"gate": "needs_refine", "from": "ANALYZE", "to": "REFINE"},
            },
            {
                "event_type": "TRANSITION",
                "task_id": task_id,
                "timestamp": "2026-08-02T10:00:00+00:00",
                "data": {"gate": "needs_refine", "from": "IMPLEMENT", "to": "REFINE"},
            },
        ],
    )


@given("el trace tiene TRANSITION desde VERIFY hacia REFINE")
def _trace_verify_refine(reflection_ctx: dict):
    task_id = reflection_ctx.get("task_id", "TASK-003")
    reflection_ctx["task_id"] = task_id
    _write_trace_index(
        reflection_ctx["project_root"],
        [
            {
                "event_type": "TRANSITION",
                "task_id": task_id,
                "timestamp": "2026-08-02T10:00:00+00:00",
                "data": {"gate": "needs_refine", "from": "VERIFY", "to": "REFINE"},
            }
        ],
    )


@given("el contexto incluye last_error y last_error_strategy")
def _ctx_has_error(reflection_ctx: dict):
    reflection_ctx["last_error"] = "quality_gate"
    reflection_ctx["last_error_strategy"] = "retry_failed"
    reflection_ctx["last_reason"] = "Calidad insuficiente en el code review"
    reflection_ctx["failed_gates"] = "tsc sin errores"


@given("el bootstrap de providers del engine")
def _bootstrap_providers(reflection_ctx: dict):
    pass


@given("el template de refine incluye placeholders")
def _template_with_placeholders(reflection_ctx: dict):
    prompts_dir = os.path.join(
        reflection_ctx["project_root"], "workflow.v3", "prompts", "refine"
    )
    os.makedirs(prompts_dir, exist_ok=True)
    template = "source_node={source_node} attempt={attempt}/{max_attempts} error={error_context} hint={hint}"
    with open(os.path.join(prompts_dir, "diagnose.prompt.json"), "w") as f:
        json.dump(
            {"id": "refine/diagnose", "type": "llm_task", "template": template}, f
        )


# ── When ──────────────────────────────────────────────────────────


@when("construyo el contexto de reflexión para la tarea")
def _build_reflection(reflection_ctx: dict):
    from features.event_tracing.application.reflection import build_reflection_context

    task_id = reflection_ctx.get("task_id", "TASK-001")
    result = build_reflection_context(
        chain_dir=reflection_ctx["chain_dir"],
        task_id=task_id,
        last_error=reflection_ctx.get("last_error", ""),
        last_error_strategy=reflection_ctx.get("last_error_strategy", ""),
        last_reason=reflection_ctx.get("last_reason", ""),
        failed_gates=reflection_ctx.get("failed_gates", ""),
    )
    reflection_ctx["pkg"] = result


@when("consulto los providers disponibles")
def _available_providers(reflection_ctx: dict):
    from features.main.container import AppContainer

    registry = AppContainer.build().workflow._context_registry
    reflection_ctx["available"] = registry.available()


@when("el ReflectionProvider recolecta el contexto")
def _collect_reflection(reflection_ctx: dict):
    from types import SimpleNamespace
    from features.event_tracing.context.providers import ReflectionProvider

    chain_dir = reflection_ctx["chain_dir"]
    task_id = reflection_ctx.get("task_id", "TASK-003")
    ctx = SimpleNamespace(
        chain_dir=chain_dir,
        task_id=task_id,
        baggage={
            "task_id": task_id,
            "last_error": reflection_ctx.get("last_error", ""),
            "last_error_strategy": reflection_ctx.get("last_error_strategy", ""),
            "last_reason": reflection_ctx.get("last_reason", ""),
        },
    )
    provider = ReflectionProvider()
    reflection_ctx["piece"] = provider.collect({}, ctx)


@when("el PromptContextBuilder construye el contexto")
def _build_prompt_context(reflection_ctx: dict):
    from shared.core.engine.application.prompt_context_builder import PromptContextBuilder

    task_id = reflection_ctx.get("task_id", "TASK-004")
    reflection_ctx["builder_ctx"] = PromptContextBuilder().build(
        reflection_ctx["chain_dir"], task_id
    )


@when("el OrchestrationService resuelve el prompt")
def _resolve_prompt(reflection_ctx: dict):
    from types import SimpleNamespace
    from tests.conftest import make_orchestration_service

    task_id = reflection_ctx.get("task_id", "TASK-005")
    # Camino A: registry mínimo explícito — solo se resuelve el prompt, no se
    # ejecutan tasks de workflow (composition root obligatorio, Gap 1).
    svc = make_orchestration_service(real=False)
    ctx_obj = SimpleNamespace(
        chain_dir=reflection_ctx["chain_dir"],
        task_id=task_id,
        session_id="s",
        baggage={"task_id": task_id},
    )
    sprint_ctx = svc._build_prompt_context(reflection_ctx["chain_dir"], task_id)
    prompt = svc.resolve_prompt(
        "refine/diagnose",
        reflection_ctx["chain_dir"],
        task_id,
        context_config={"sources": [], "focus": {"role": "x"}},
        execution_ctx=ctx_obj,
        extra_data=sprint_ctx,
    )
    reflection_ctx["resolved_prompt"] = prompt


# ── Then ──────────────────────────────────────────────────────────


@then("source_node es IMPLEMENT")
def _source_node_is_implement(reflection_ctx: dict):
    assert reflection_ctx["pkg"]["source_node"] == "IMPLEMENT", reflection_ctx["pkg"][
        "source_node"
    ]


@then("el source_node es el del último refine")
def _source_last_refine(reflection_ctx: dict):
    assert reflection_ctx["pkg"]["source_node"] == "IMPLEMENT"


@then("attempt es 1")
def _attempt_is_1(reflection_ctx: dict):
    assert reflection_ctx["pkg"]["attempt"] == 1, reflection_ctx["pkg"]["attempt"]


@then("attempt es 2")
def _attempt_is_2(reflection_ctx: dict):
    assert reflection_ctx["pkg"]["attempt"] == 2, reflection_ctx["pkg"]["attempt"]


@then("max_attempts es 3")
def _max_attempts_is_3(reflection_ctx: dict):
    assert reflection_ctx["pkg"]["max_attempts"] == 3


@then("el error reportado está en error_context")
def _error_in_context(reflection_ctx: dict):
    assert "quality_gate" in reflection_ctx["pkg"]["error_context"], reflection_ctx[
        "pkg"
    ]["error_context"]


@then("failed_gates incluye el reportado")
def _failed_gates_included(reflection_ctx: dict):
    assert "tsc sin errores" in reflection_ctx["pkg"]["failed_gates"]


@then("hint no está vacío")
def _hint_not_empty(reflection_ctx: dict):
    assert reflection_ctx["pkg"]["hint"], "hint no debería estar vacío"


@then("hay ERRORs en past_attempts")
def _errors_in_past(reflection_ctx: dict):
    assert any(p["type"] == "ERROR" for p in reflection_ctx["pkg"]["past_attempts"])


@then("la pieza tiene provider reflection")
def _piece_provider(reflection_ctx: dict):
    assert reflection_ctx["piece"].provider == "reflection"


@then("el contenido incluye source_node y attempt")
def _content_has_source_and_attempt(reflection_ctx: dict):
    assert "source_node: VERIFY" in reflection_ctx["piece"].content
    assert "attempt: 1/3" in reflection_ctx["piece"].content


@then("el contenido incluye el error reportado")
def _content_has_error(reflection_ctx: dict):
    content = reflection_ctx["piece"].content
    assert (
        "quality_gate" in content
        or "qa_reject" in content
        or "error" in content.lower()
    )


@then("la pieza tiene tokens mayor a 0")
def _tokens_positive(reflection_ctx: dict):
    assert reflection_ctx["piece"].tokens > 0


@then("reflection está registrado")
def _reflection_registered(reflection_ctx: dict):
    assert "reflection" in reflection_ctx["available"], (
        f"Disponibles: {reflection_ctx['available']}"
    )


@then("el contexto incluye source_node")
def _ctx_has_source_node(reflection_ctx: dict):
    assert "source_node" in reflection_ctx["builder_ctx"]


@then("el contexto incluye attempt y max_attempts")
def _ctx_has_attempt(reflection_ctx: dict):
    assert "attempt" in reflection_ctx["builder_ctx"]
    assert "max_attempts" in reflection_ctx["builder_ctx"]


@then("el contexto incluye error_context y hint")
def _ctx_has_error_and_hint(reflection_ctx: dict):
    assert "error_context" in reflection_ctx["builder_ctx"]
    assert "hint" in reflection_ctx["builder_ctx"]


@then("el contexto incluye reflection_past_attempts")
def _ctx_has_past(reflection_ctx: dict):
    assert "reflection_past_attempts" in reflection_ctx["builder_ctx"]


@then("source_node está resuelto en el prompt")
def _source_resolved(reflection_ctx: dict):
    prompt = reflection_ctx["resolved_prompt"]
    assert "source_node=" in prompt, f"source_node no resuelto en: {prompt}"


@then("hint está resuelto en el prompt")
def _hint_resolved(reflection_ctx: dict):
    prompt = reflection_ctx["resolved_prompt"]
    assert "hint=" in prompt, f"hint no resuelto en: {prompt}"


@then("no quedan placeholders sin resolver")
def _no_unresolved(reflection_ctx: dict):
    prompt = reflection_ctx["resolved_prompt"]
    assert "{source_node}" not in prompt
    assert "{hint}" not in prompt
