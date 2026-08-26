"""Tests BDD (pytest-bdd) — Orquestador de joins (ADR-WALKER-023).

Feature value: cruzar CPG, CKG, Delta y Trace por archivo o task_id sin tocar
CognitiveGraph directamente, con fallback explícito por repo.

Escenarios: bdd/features/intelligence_service.feature
"""
from __future__ import annotations

from dataclasses import asdict

import pytest
from pytest_bdd import given, scenarios, then, when
from returns.pipeline import is_successful

scenarios("bdd/features/intelligence_service.feature")


@pytest.fixture
def is_ctx() -> dict:
    return {}


# ── Helpers ──────────────────────────────────────────────────────────────────


class FakeCpg:
    def __init__(self, symbols=None):
        self._symbols = symbols or []

    def search_nodes(self, filters, max_results=50, offset=0):
        file = filters.get("file", "")
        if file:
            hits = [s for s in self._symbols if s.get("file") == file]
        else:
            hits = list(self._symbols)
        return {
            "hits": {
                "total": len(hits),
                "hits": [{"_source": s} for s in hits[:max_results]],
            }
        }


class FakeCkg:
    def __init__(self, nodes=None):
        self._nodes = nodes or []

    def query(self, filters=None):
        return self._nodes

    def search(self, query, max_results=20):
        return self._nodes[:max_results]


class FakeDelta:
    def __init__(self, specs=None, spring=None):
        from features.delta_tracking.domain.entities import Spring

        self._specs = specs or []
        self._spring = spring or Spring(
            spring_id="sprint-a", status="active", active=True
        )

    def get_active_spring(self):
        return self._spring

    def get_spring(self, spring_id):
        return self._spring

    def get_specs(self, spring_id, status=""):
        return [s for s in self._specs if s.spring_id == spring_id]

    def get_spec(self, spring_id, spec_id):
        for s in self._specs:
            if s.spring_id == spring_id and s.spec_id == spec_id:
                return s
        return None


class FakeTrace:
    def __init__(self, events=None):
        self._events = events or []

    def load_traces(self, chain_dir):
        return self._events


class _Event:
    def __init__(self, tool, timestamp, files=None, task_id=""):
        self.event_type = "tool_call"
        self.tool = tool
        self.timestamp = timestamp
        self.files = files or []
        self.task_id = task_id
        self.duration_ms = 120


def _make_service(cpg=None, ckg=None, delta=None, trace=None):
    from orchestration.intelligence_service import (
        TaskIntelligenceService,
    )

    return TaskIntelligenceService(
        cpg_repo=cpg or FakeCpg(),
        ckg_repo=ckg or FakeCkg(),
        delta_repo=delta or FakeDelta(),
        trace_repo=trace or FakeTrace(),
    )


# ── Joins ────────────────────────────────────────────────────────────────────


@given("un CPG con símbolos en src/auth.py")
def _cpg_with_symbols(is_ctx: dict):
    is_ctx["cpg"] = FakeCpg(
        symbols=[
            {"file": "src/auth.py", "type": "function", "name": "login", "line": 10},
            {"file": "src/auth.py", "type": "class", "name": "AuthService", "line": 3},
            {"file": "src/other.py", "type": "function", "name": "util", "line": 1},
        ]
    )


@given("un CKG con nodos de convención en auth")
def _ckg_with_nodes(is_ctx: dict):
    is_ctx["ckg"] = FakeCkg(
        nodes=[
            {
                "id": "ckg-1",
                "type": "convention",
                "statement": "Toda mutación pasa por el repo",
                "scope": "auth",
            }
        ]
    )


@given("un Delta con specs para src/auth.py y src/other.py")
def _delta_with_specs(is_ctx: dict):
    from features.delta_tracking.domain.entities import Spec

    is_ctx["delta"] = FakeDelta(
        specs=[
            Spec(
                spring_id="sprint-a", objective="Agregar login", files=["src/auth.py"]
            ),
            Spec(
                spring_id="sprint-a", objective="Otro spec", files=["src/other.py"]
            ),
        ]
    )


@given("un Trace con eventos en src/auth.py")
def _trace_with_events(is_ctx: dict):
    is_ctx["trace"] = FakeTrace(
        events=[
            _Event(
                tool="edit.file",
                timestamp="2026-08-07T10:00",
                files=["src/auth.py"],
            ),
            _Event(tool="recall_knowledge", timestamp="2026-08-07T11:00", files=[]),
        ]
    )


@when("se consulta inteligencia por archivo src/auth.py")
def _query_intelligence_by_file(is_ctx: dict):
    svc = _make_service(
        cpg=is_ctx["cpg"],
        ckg=is_ctx["ckg"],
        delta=is_ctx["delta"],
        trace=is_ctx["trace"],
    )
    is_ctx["result"] = svc.get_task_intelligence(file="src/auth.py")


@then("el resultado es exitoso")
def _assert_result_successful(is_ctx: dict):
    assert is_successful(is_ctx["result"])
    is_ctx["vo"] = is_ctx["result"].value_or(None)


@then("CPG tiene 2 símbolos (login, AuthService)")
def _assert_cpg_symbols(is_ctx: dict):
    vo = is_ctx["vo"]
    assert vo.cpg.ok is True
    assert vo.cpg.total == 2
    assert {s.name for s in vo.cpg.symbols} == {"login", "AuthService"}


@then("Delta tiene 1 item filtrado por archivo")
def _assert_delta_items(is_ctx: dict):
    vo = is_ctx["vo"]
    assert vo.delta.ok is True
    assert len(vo.delta.items) == 1
    assert vo.delta.items[0].objective == "Agregar login"


@then("CKG tiene 1 nodo")
def _assert_ckg_total(is_ctx: dict):
    vo = is_ctx["vo"]
    assert vo.ckg.total == 1


@then("Trace tiene 1 evento")
def _assert_trace_total(is_ctx: dict):
    vo = is_ctx["vo"]
    assert vo.trace.total == 1
    assert vo.trace.events[0].tool == "edit.file"


@then("el payload type es task_intelligence")
def _assert_payload_type(is_ctx: dict):
    payload = is_ctx["vo"].as_dict()
    assert payload["type"] == "task_intelligence"
    assert payload["cpg"]["total"] == 2


@given("un Trace con eventos de TASK-042 y TASK-999")
def _trace_with_task_ids(is_ctx: dict):
    is_ctx["trace"] = FakeTrace(
        events=[
            _Event(tool="write", timestamp="t1", files=[], task_id="TASK-042"),
            _Event(tool="edit", timestamp="t2", files=[], task_id="TASK-999"),
        ]
    )


@when("se consulta inteligencia por task_id TASK-042")
def _query_by_task_id(is_ctx: dict):
    svc = _make_service(trace=is_ctx["trace"])
    is_ctx["result"] = svc.get_task_intelligence(task_id="TASK-042")


@then("Trace tiene 1 evento con task_id TASK-042")
def _assert_trace_by_task_id(is_ctx: dict):
    vo = is_ctx["vo"]
    assert vo.trace.total == 1
    assert vo.trace.events[0].task_id == "TASK-042"
    assert vo.trace.events[0].tool == "write"


@then("Delta está disponible")
def _assert_delta_available(is_ctx: dict):
    assert is_ctx["vo"].delta.ok is True


@given("un CPG que lanza RuntimeError")
def _broken_cpg(is_ctx: dict):
    class BrokenCpg:
        def search_nodes(self, filters, max_results=50, offset=0):
            raise RuntimeError("boom")

    is_ctx["cpg"] = BrokenCpg()


@then("CPG está degradado con ok False")
def _assert_cpg_degraded(is_ctx: dict):
    vo = is_ctx["vo"]
    assert vo.cpg.ok is False
    assert "no disponible" in vo.cpg.error


@then("CKG, Delta y Trace siguen vivas")
def _assert_others_alive(is_ctx: dict):
    vo = is_ctx["vo"]
    assert vo.ckg.ok is True
    assert vo.delta.ok is True
    assert vo.trace.ok is True


@given("un Spring con goal y un Spec con dependencias")
def _spring_with_goal_and_deps(is_ctx: dict):
    from features.delta_tracking.domain.entities import Spring, Spec

    spring = Spring(
        spring_id="sprint-a", status="active", active=True, goal="Agregar auth"
    )
    spec = Spec(
        spring_id="sprint-a",
        objective="Implementar login",
        files=["src/auth.py"],
        depends_on=["SPEC-001"],
    )
    is_ctx["delta"] = FakeDelta(specs=[spec], spring=spring)
    is_ctx["spec"] = spec


@when("se consulta inteligencia por task_id y archivo")
def _query_by_task_id_and_file(is_ctx: dict):
    svc = _make_service(delta=is_ctx["delta"])
    is_ctx["result"] = svc.get_task_intelligence(
        task_id=is_ctx["spec"].spec_id, file="src/auth.py"
    )
    is_ctx["vo"] = is_ctx["result"].value_or(None) if is_successful(is_ctx["result"]) else None


@then("el Delta tiene 1 item")
def _assert_delta_one_item(is_ctx: dict):
    vo = is_ctx["vo"]
    items = vo.delta.items
    assert len(items) == 1


@then("el item tiene spring_goal enriquecido")
def _assert_spring_goal_enriched(is_ctx: dict):
    assert is_ctx["vo"].delta.items[0].spring_goal == "Agregar auth"


@then("dependencies es una lista")
def _assert_dependencies_list(is_ctx: dict):
    assert isinstance(is_ctx["vo"].delta.items[0].dependencies, list)


@given("el servicio de inteligencia")
def _intelligence_service(is_ctx: dict):
    is_ctx["svc"] = _make_service()


@when("se consulta inteligencia por archivo")
def _query_by_file_generic(is_ctx: dict):
    if "svc" not in is_ctx:
        cpg = is_ctx.get("cpg")
        is_ctx["svc"] = _make_service(cpg=cpg)
    svc = is_ctx["svc"]
    is_ctx["result"] = svc.get_task_intelligence(file="src/auth.py")
    is_ctx["vo"] = is_ctx["result"].value_or(None) if is_successful(is_ctx["result"]) else None


@then("el scope.file es src/auth.py")
def _assert_scope_file(is_ctx: dict):
    vo = is_ctx["vo"]
    assert vo.scope.file == "src/auth.py"


@then("el summary tiene claves cpg_total, ckg_total, delta_total y trace_total")
def _assert_summary_keys(is_ctx: dict):
    keys = set(asdict(is_ctx["vo"].summary).keys())
    assert keys == {
        "cpg_total",
        "ckg_total",
        "delta_total",
        "trace_total",
    }


# ── Use cases ────────────────────────────────────────────────────────────────


@given("un CPG con un símbolo login en src/auth.py")
def _cpg_one_symbol(is_ctx: dict):
    is_ctx["cpg"] = FakeCpg(
        symbols=[
            {"file": "src/auth.py", "type": "function", "name": "login", "line": 10}
        ]
    )


@given("un Delta con spec Agregar login para src/auth.py")
def _delta_one_spec(is_ctx: dict):
    from features.delta_tracking.domain.entities import Spec

    is_ctx["delta"] = FakeDelta(
        specs=[
            Spec(
                spring_id="sprint-a",
                objective="Agregar login",
                files=["src/auth.py"],
            )
        ]
    )


@when("se ejecuta task_file_intelligence")
def _run_task_file_intelligence(is_ctx: dict):
    delta = is_ctx.get("delta", FakeDelta())
    svc = _make_service(cpg=is_ctx.get("cpg"), delta=delta)
    is_ctx["result"] = svc.task_file_intelligence(file="src/auth.py")
    is_ctx["vo"] = is_ctx["result"].value_or(None) if is_successful(is_ctx["result"]) else None


@then("el file es src/auth.py")
def _assert_file(is_ctx: dict):
    vm = is_ctx["vo"]
    assert vm.file == "src/auth.py"


@then("symbols tiene login")
def _assert_symbols(is_ctx: dict):
    vm = is_ctx["vo"]
    assert vm.symbols[0].name == "login"


@then("specs tiene Agregar login")
def _assert_specs(is_ctx: dict):
    vm = is_ctx["vo"]
    assert vm.specs == ["Agregar login"]


@then("risk es LOW, MED o HIGH")
def _assert_risk(is_ctx: dict):
    vm = is_ctx["vo"]
    assert vm.risk in ("LOW", "MED", "HIGH")


@then("el payload type es task_file_intelligence")
def _assert_task_file_payload(is_ctx: dict):
    payload = is_ctx["vo"].as_dict()
    assert payload["phase"] == "IMPLEMENT"
    assert payload["entities"]["symbols"][0]["name"] == "login"


@then("el resultado es exitoso (convención: nunca lanza)")
def _assert_never_raises(is_ctx: dict):
    assert is_successful(is_ctx["result"])


@then("el file se preserva")
def _assert_file_preserved(is_ctx: dict):
    vm = is_ctx["vo"]
    assert vm.file == "src/auth.py"


@given("un Trace con eventos edit.file y submit para TASK-042")
def _trace_for_failure_diagnosis(is_ctx: dict):
    is_ctx["trace"] = FakeTrace(
        events=[
            _Event(tool="edit.file", timestamp="t1", files=[], task_id="TASK-042"),
            _Event(tool="submit", timestamp="t2", files=[], task_id="TASK-042"),
        ]
    )


@when("se ejecuta failure_diagnosis")
def _run_failure_diagnosis(is_ctx: dict):
    svc = _make_service(trace=is_ctx["trace"])
    is_ctx["result"] = svc.failure_diagnosis(task_id="TASK-042")


@then("tools_used incluye edit.file")
def _assert_tools_used(is_ctx: dict):
    vm = is_ctx["vo"]
    assert "edit.file" in vm.tools_used


@then("el diagnosis menciona actividad reciente")
def _assert_diagnosis_message(is_ctx: dict):
    vm = is_ctx["vo"]
    assert "Se detectó actividad reciente" in vm.diagnosis


@then("el payload type es failure_diagnosis")
def _assert_failure_diagnosis_payload(is_ctx: dict):
    payload = is_ctx["vo"].as_dict()
    assert payload["phase"] == "REFINE"


@when("se ejecuta qa_coverage para TASK-042")
def _run_qa_coverage(is_ctx: dict):
    svc = is_ctx["svc"]
    is_ctx["result"] = svc.qa_coverage(task_id="TASK-042")
    is_ctx["vo"] = is_ctx["result"].value_or(None)


@when("se ejecuta sprint_evidence con goal agregar auth")
def _run_sprint_evidence(is_ctx: dict):
    svc = is_ctx["svc"]
    is_ctx["result"] = svc.sprint_evidence(goal="agregar auth")
    is_ctx["vo"] = is_ctx["result"].value_or(None)


@then("el payload phase es VERIFY")
def _assert_phase_verify(is_ctx: dict):
    assert is_ctx["vo"].as_dict()["phase"] == "VERIFY"


@then("el payload phase es SPRINT.PLANNING")
def _assert_phase_sprint_planning(is_ctx: dict):
    assert is_ctx["vo"].as_dict()["phase"] == "SPRINT.PLANNING"
