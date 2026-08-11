"""Tests BDD (pytest-bdd) — Phase intelligence tasks.

Feature value: valida que las 4 tasks públicas de fase registren
input_schema y output_schema, y que el output_schema derive del DTO de
contrato (SSOT del contrato ADR-043 F1).

Escenarios: bdd/features/phase_intelligence.feature
"""

from __future__ import annotations

import pytest
from pytest_bdd import given, scenarios, then, when

scenarios("bdd/features/phase_intelligence.feature")


class FakeRegistry:
    def __init__(self):
        self.registered = {}

    def register(self, name, fn, *, metadata=None):
        self.registered[name] = {"fn": fn, "metadata": metadata or {}}


@pytest.fixture
def phase_intelligence_ctx() -> dict:
    return {}


# ── Given ─────────────────────────────────────────────────────────────────────


@given("un registry vacío")
def _empty_phase_registry(phase_intelligence_ctx: dict):
    phase_intelligence_ctx["registry"] = FakeRegistry()


@given("un execution context con chain_dir inexistente")
def _ctx_no_existe(phase_intelligence_ctx: dict):
    from libs.shared_domain import ExecutionContext

    phase_intelligence_ctx["ctx"] = ExecutionContext.create(
        session_id="s1", chain_dir="/no/existe"
    )


@given("el bootstrap de tasks")
def _bootstrap_tasks_ready(phase_intelligence_ctx: dict):
    from features.engine.infrastructure.task_registry import InMemoryTaskRegistry

    phase_intelligence_ctx["registry"] = InMemoryTaskRegistry(tracer=None)


# ── When ──────────────────────────────────────────────────────────────────────


@when("registro las tasks de fase")
def _register_phase_tasks(phase_intelligence_ctx: dict):
    import features.task_execution.tasks.phase_intelligence as phase_tasks

    registry = phase_intelligence_ctx["registry"]
    phase_tasks.register(registry)


@when("ejecuto kg_task_context")
def _run_kg_task_context(phase_intelligence_ctx: dict):
    import features.task_execution.tasks.phase_intelligence as phase_tasks

    ctx = phase_intelligence_ctx["ctx"]
    phase_intelligence_ctx["result"] = phase_tasks.kg_task_context(
        ctx, {"_payload": {"file": "src/auth.py"}}
    )


@when("registro las tasks públicas")
def _bootstrap_public_tasks(phase_intelligence_ctx: dict):
    from features.engine.bootstrap import bootstrap_tasks

    registry = phase_intelligence_ctx["registry"]
    bootstrap_tasks(registry)
    phase_intelligence_ctx["metas"] = {
        t["name"]: t for t in (registry.list_tasks_with_meta(scope="public") or [])
    }


# ── Then ──────────────────────────────────────────────────────────────────────


@then("hay 4 tools registradas")
def _four_tools(phase_intelligence_ctx: dict):
    assert set(phase_intelligence_ctx["registry"].registered) == {
        "kg_task_context",
        "kg_diagnose",
        "kg_qa_evidence",
        "kg_sprint_evidence",
    }


@then("cada una tiene input_schema y output_schema")
def _each_has_schemas(phase_intelligence_ctx: dict):
    for name, entry in phase_intelligence_ctx["registry"].registered.items():
        meta = entry["metadata"]
        assert "input_schema" in meta, f"{name}: falta input_schema"
        assert "output_schema" in meta, f"{name}: falta output_schema"


@then("kg_task_context output_schema es TaskFileIntelligenceDTO.to_schema()")
def _kg_task_context_schema(phase_intelligence_ctx: dict):
    from features.task_execution.application.contracts import TaskFileIntelligenceDTO

    meta = phase_intelligence_ctx["registry"].registered["kg_task_context"]["metadata"]
    assert meta["output_schema"] == TaskFileIntelligenceDTO.to_schema()


@then("kg_diagnose output_schema es FailureDiagnosisDTO.to_schema()")
def _kg_diagnose_schema(phase_intelligence_ctx: dict):
    from features.task_execution.application.contracts import FailureDiagnosisDTO

    meta = phase_intelligence_ctx["registry"].registered["kg_diagnose"]["metadata"]
    assert meta["output_schema"] == FailureDiagnosisDTO.to_schema()


@then("kg_qa_evidence output_schema es QaCoverageDTO.to_schema()")
def _kg_qa_schema(phase_intelligence_ctx: dict):
    from features.task_execution.application.contracts import QaCoverageDTO

    meta = phase_intelligence_ctx["registry"].registered["kg_qa_evidence"]["metadata"]
    assert meta["output_schema"] == QaCoverageDTO.to_schema()


@then("kg_sprint_evidence output_schema es SprintEvidenceDTO.to_schema()")
def _kg_sprint_schema(phase_intelligence_ctx: dict):
    from features.task_execution.application.contracts import SprintEvidenceDTO

    meta = phase_intelligence_ctx["registry"].registered["kg_sprint_evidence"][
        "metadata"
    ]
    assert meta["output_schema"] == SprintEvidenceDTO.to_schema()


@then('kg_task_context output_schema tiene properties.phase.const="IMPLEMENT"')
def _phase_implement(phase_intelligence_ctx: dict):
    s = phase_intelligence_ctx["registry"].registered["kg_task_context"]["metadata"][
        "output_schema"
    ]
    assert s["properties"]["phase"]["const"] == "IMPLEMENT"


@then('kg_task_context tiene property "file" y risk con enum LOW MED HIGH')
def _file_and_risk(phase_intelligence_ctx: dict):
    s = phase_intelligence_ctx["registry"].registered["kg_task_context"]["metadata"][
        "output_schema"
    ]
    assert "file" in s["properties"]
    assert s["properties"]["risk"]["enum"] == ["LOW", "MED", "HIGH"]


@then('kg_qa_evidence output_schema tiene properties.phase.const="VERIFY"')
def _phase_verify(phase_intelligence_ctx: dict):
    s = phase_intelligence_ctx["registry"].registered["kg_qa_evidence"]["metadata"][
        "output_schema"
    ]
    assert s["properties"]["phase"]["const"] == "VERIFY"


@then("el resultado tiene atributo ok")
def _has_ok(phase_intelligence_ctx: dict):
    result = phase_intelligence_ctx["result"]
    assert hasattr(result, "ok")


@then("cada una está en el registry con scope public")
def _public_scope(phase_intelligence_ctx: dict):
    metas = phase_intelligence_ctx["metas"]
    for name in (
        "kg_task_context",
        "kg_diagnose",
        "kg_qa_evidence",
        "kg_sprint_evidence",
    ):
        assert name in metas, f"{name}: no registrada como pública en bootstrap"


@then("cada una tiene input_schema y output_schema en el registry")
def _public_schemas(phase_intelligence_ctx: dict):
    metas = phase_intelligence_ctx["metas"]
    for name in (
        "kg_task_context",
        "kg_diagnose",
        "kg_qa_evidence",
        "kg_sprint_evidence",
    ):
        assert "output_schema" in metas[name], f"{name}: falta output_schema"
        assert "input_schema" in metas[name], f"{name}: falta input_schema"
