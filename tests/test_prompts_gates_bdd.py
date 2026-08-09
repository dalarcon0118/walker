"""Tests BDD (pytest-bdd) — Prompts gates.

Feature value: valida que el sistema de prompts para USER GATES cargue
templates reales, resuelva placeholders, exponga last_step_fields y genere
evidencias y briefings correctos.

Escenarios: tests/features/prompts_gates.feature
"""
from __future__ import annotations

import json
import os
import tempfile
from types import SimpleNamespace

import pytest
from pytest_bdd import given, scenarios, then, when

scenarios("features/prompts_gates.feature")


def _setup_project(tmpdir: str) -> tuple[str, str]:
    project_root = os.path.join(tmpdir, "proj")
    chain_dir = os.path.join(project_root, "data", "spring", "test-sprint")
    os.makedirs(os.path.join(project_root, "data"), exist_ok=True)
    os.makedirs(chain_dir, exist_ok=True)
    project_json = {
        "project": {
            "meta": {"project_id": "test-project", "version": "2.0"},
            "identity": {"name": "test-project"},
            "location": {"repo_path": project_root},
        }
    }
    with open(os.path.join(project_root, "data", "project.json"), "w") as f:
        json.dump(project_json, f, ensure_ascii=False)
    return chain_dir, project_root


def _write_template(project_root: str, node: str, prompt_name: str, template: str) -> str:
    prompts_dir = os.path.join(project_root, "workflow.v3", "prompts", node)
    os.makedirs(prompts_dir, exist_ok=True)
    path = os.path.join(prompts_dir, f"{prompt_name}.prompt.json")
    with open(path, "w") as f:
        json.dump(
            {"id": f"{node}/{prompt_name}", "type": "user_input", "template": template},
            f,
            ensure_ascii=False,
        )
    return path


@pytest.fixture
def prompts_gates_ctx() -> dict:
    return {}


# ── Given ─────────────────────────────────────────────────────────────────────


@given("un proyecto con template \"testnode/review\"")
def _project_with_template(prompts_gates_ctx: dict, tmp_path):
    chain_dir, project_root = _setup_project(str(tmp_path))
    _write_template(
        project_root,
        "testnode",
        "review",
        "📋 QUÉ SUCEDIÓ\n  {what_happened}\n\n➡️ OPCIÓN: {decision}\n",
    )
    prompts_gates_ctx["chain_dir"] = chain_dir
    prompts_gates_ctx["project_root"] = project_root


@given("un OrchestrationService")
def _orchestration_service(prompts_gates_ctx: dict):
    from features.engine.application.orchestrator import OrchestrationService

    prompts_gates_ctx["svc"] = OrchestrationService()


@given("un WorkflowState con Q&A del ANALYZE")
def _state_with_qa(prompts_gates_ctx: dict, tmp_path):
    from features.engine.domain.models import SubStateData
    from features.engine.infrastructure.repositories import WorkflowState

    chain_dir, _ = _setup_project(str(tmp_path))
    state = WorkflowState(
        current_node="ANALYZE",
        current_sub_state="REVIEW",
        sub_state_data={
            "ANALYZE.ANALYZE": SubStateData(
                fields={"q1": "¿Qué?", "a1": "Respuesta"}, completed_at="x"
            )
        },
    )
    repo = type("R", (), {"save": lambda s, cd, st: None})()
    repo.save(chain_dir, state)
    prompts_gates_ctx["chain_dir"] = chain_dir
    prompts_gates_ctx["state"] = state


@given("un detalle de verificación con método shell")
def _verification_shell_detail(prompts_gates_ctx: dict):
    # El When 'genero el reporte de evidencia' construye el detalle shell
    # internamente; este given declara la precondición en el feature.
    prompts_gates_ctx["detail"] = {
        "method": "shell",
        "command": "npm run compile",
        "exit_code": 1,
        "stdout": "235 errors in 91 files",
    }


@given("un what_happened sintetizado")
def _what_happened_synth(prompts_gates_ctx: dict):
    from features.prompts.gate_briefing import what_happened_from_state

    prompts_gates_ctx["what_happened"] = what_happened_from_state(
        {"q1": "Q?", "a1": "A1", "q2": "Q2?", "a2": "A2", "q3": "Q3?", "a3": "A3"}
    )


@given("gates de negocio")
def _business_gates(prompts_gates_ctx: dict):
    prompts_gates_ctx["gates"] = [
        {
            "name": "analysis_approved",
            "target": "IMPLEMENT.USER_GATE",
            "description": "Aprobar",
        },
        {"name": "needs_refine", "target": "REFINE.DIAGNOSE", "description": "Refinar"},
    ]


@given("un chain_dir de proyecto")
def _chain_dir_project(prompts_gates_ctx: dict, tmp_path):
    chain_dir, _ = _setup_project(str(tmp_path))
    prompts_gates_ctx["chain_dir"] = chain_dir


@given("un health report con execution_type=user_input")
def _health_user_gate(prompts_gates_ctx: dict):
    prompts_gates_ctx["health"] = {
        "checks": {"ckg": "13 nodes", "cpg": "16891 nodes", "files": "up to date"},
        "warnings": [],
        "sprint": {
            "node": "ANALYZE",
            "sub_state": "REVIEW",
            "gates": [
                {"name": "analysis_approved", "target": "IMPLEMENT"},
                {"name": "needs_refine", "target": "REFINE"},
            ],
            "execution_type": "user_input",
            "prompt_id": "analyze/review",
        },
        "project": {"language": "ts"},
    }


@given("un health report con execution_type=llm_task")
def _health_llm_gate(prompts_gates_ctx: dict):
    prompts_gates_ctx["health"] = {
        "checks": {"ckg": "13 nodes", "cpg": "16891 nodes", "files": "up to date"},
        "warnings": [],
        "sprint": {
            "node": "VERIFY",
            "sub_state": "QA",
            "gates": [
                {"name": "task_complete", "target": "SPRINT"},
                {"name": "needs_refine", "target": "REFINE"},
            ],
            "execution_type": "llm_task",
            "prompt_id": "verify/qa",
        },
        "project": {"language": "ts"},
    }


# ── When ──────────────────────────────────────────────────────────────────────


@when('compongo el prompt con datos extra')
def _compose_prompt(prompts_gates_ctx: dict):
    from features.prompts.manager import PromptManager

    chain_dir = prompts_gates_ctx["chain_dir"]
    project_root = prompts_gates_ctx["project_root"]
    pm = PromptManager(chain_dir, project_dir=project_root)
    ctx = SimpleNamespace(
        chain_dir=chain_dir,
        task_id="TASK-001",
        baggage={
            "task_id": "TASK-001",
            "what_happened": "Análisis completado",
            "decision": "approve",
        },
    )
    node_config = {
        "focus": {"role": "revisor"},
        "sources": [{"provider": "state", "config": {}}],
    }
    result = pm.compose(
        node_config=node_config,
        prompt_template="testnode/review",
        ctx=ctx,
        chain_dir=chain_dir,
        extra_data={"what_happened": "Análisis completado", "decision": "approve"},
    )
    prompts_gates_ctx["result"] = result


@when('resuelvo el prompt con extra_data de Q&A')
def _resolve_prompt(prompts_gates_ctx: dict):
    chain_dir = prompts_gates_ctx["chain_dir"]
    project_root = prompts_gates_ctx["project_root"]
    svc = prompts_gates_ctx["svc"]
    _write_template(
        project_root, "testnode", "review", "Q1: {q1}\nA1: {a1}\n"
    )
    context_config = {
        "focus": {"role": "analista"},
        "sources": [{"provider": "state", "config": {"query": {}}}],
    }
    ctx = SimpleNamespace(
        chain_dir=chain_dir,
        task_id="TASK-001",
        session_id="sess",
        baggage={"task_id": "TASK-001"},
    )
    result = svc.resolve_prompt(
        "testnode/review",
        chain_dir,
        "TASK-001",
        context_config=context_config,
        execution_ctx=ctx,
        extra_data={"q1": "¿Qué componentes?", "a1": "A, B, C"},
    )
    prompts_gates_ctx["result"] = result


@when('guardo y cargo el estado')
def _save_load_state(prompts_gates_ctx: dict):
    from features.engine.infrastructure.repositories import WorkflowStateRepository

    chain_dir = prompts_gates_ctx["chain_dir"]
    state = prompts_gates_ctx["state"]
    repo = WorkflowStateRepository()
    repo.save(chain_dir, state)
    loaded = repo.load(chain_dir)
    prompts_gates_ctx["loaded"] = loaded


@when('consulto last_fields')
def _query_last_fields(prompts_gates_ctx: dict):
    from features.engine.context.query_manager import QueryManager

    qm = QueryManager.__new__(QueryManager)
    qm._chain_dir = prompts_gates_ctx["chain_dir"]
    result = qm._query_state({})
    prompts_gates_ctx["query_result"] = result


@when('genero el reporte de evidencia')
def _build_evidence(prompts_gates_ctx: dict):
    from features.prompts.evidence import build_evidence_report, format_detail_block

    detail = prompts_gates_ctx.get(
        "detail",
        {
            "method": "shell",
            "command": "npm run compile",
            "exit_code": 1,
            "stdout": "235 errors in 91 files",
        },
    )
    prompts_gates_ctx["block"] = format_detail_block(detail)
    checks = {
        "total": 2,
        "passed": 1,
        "passed_items": [
            {
                "predicate": "símbolo existe",
                "detail": {"method": "symbol_exists", "symbol": "X", "file": "a.ts"},
            }
        ],
        "failed": [
            {
                "predicate": "compila",
                "detail": {
                    "method": "command_output",
                    "command": "tsc",
                    "found": False,
                    "stdout": "errores",
                },
                "type": "quality_gate",
            }
        ],
    }
    report = build_evidence_report(
        "TASK-001",
        checks,
        acceptance_criteria=[{"criterion": "Compila", "passed": False}],
        quality_gates=[{"gate": "tsc sin errores", "passed": False}],
    )
    prompts_gates_ctx["report"] = report


@when('renderizo el gate briefing')
def _render_briefing(prompts_gates_ctx: dict):
    from features.prompts.gate_briefing import render_gate_briefing

    wh = prompts_gates_ctx.get("what_happened") or {}
    gates = prompts_gates_ctx.get("gates") or []
    briefing = render_gate_briefing(
        gate_name="ANALYZE.REVIEW",
        task_id="SPEC-001",
        task_title="Probar módulo",
        what_happened=wh,
        evidence="tsc: 235 errores",
        gates=gates,
    )
    prompts_gates_ctx["briefing"] = briefing
    prompts_gates_ctx["what_happened"] = wh


@given('estado con decisión approve')
def _state_decision(prompts_gates_ctx: dict):
    prompts_gates_ctx["state"] = {"decision": "approve", "feedback": "ok"}


@given('estado con intent start_task')
def _state_intent(prompts_gates_ctx: dict):
    prompts_gates_ctx["state"] = {"intent": "start_task"}


@given('estado con task_data')
def _state_task_data(prompts_gates_ctx: dict):
    prompts_gates_ctx["state"] = {"task_data": "contexto"}


@given('estado vacío')
def _state_empty(prompts_gates_ctx: dict):
    prompts_gates_ctx["state"] = {}


@when('what_happened sintetiza el estado')
def _what_happened(prompts_gates_ctx: dict):
    from features.prompts.gate_briefing import what_happened_from_state

    prompts_gates_ctx["result"] = what_happened_from_state(prompts_gates_ctx["state"])


@when('construyo el contexto del prompt')
def _build_context(prompts_gates_ctx: dict):
    from features.engine.application.prompt_context_builder import PromptContextBuilder

    chain_dir = prompts_gates_ctx["chain_dir"]
    prompts_gates_ctx["ctx"] = PromptContextBuilder().build(chain_dir, "TASK-001")


@given("un spring con tareas con dependencias")
def _spring_with_deps(prompts_gates_ctx: dict, tmp_path):
    from features.delta_tracking import SpringService

    _, project_root = _setup_project(str(tmp_path))
    svc = SpringService(project_root)
    svc.create_spring(
        "test-sprint", goal="Sprint with deps", status="active", project_id="demo"
    )
    # SPEC-001 completada; SPEC-002 (sin deps) lista; SPEC-003 depende de
    # SPEC-001 (lista); SPEC-004 depende de SPEC-003 (bloqueada).
    svc.add_spec("test-sprint", "Objetivo uno", domain="test", spec_index=0)
    svc.add_spec("test-sprint", "Objetivo dos", domain="test", spec_index=1)
    svc.add_spec("test-sprint", "Objetivo tres", domain="test", spec_index=2)
    svc.add_spec("test-sprint", "Objetivo cuatro", domain="test", spec_index=3)
    svc.update_spec_entity(
        "test-sprint", "SPEC-001", {"status": "completed"}
    )
    # depends_on va por los ids canonicos de las specs.
    svc.update_spec_entity(
        "test-sprint", "SPEC-002", {"depends_on": []}
    )
    svc.update_spec_entity(
        "test-sprint", "SPEC-003", {"depends_on": ["SPEC-001"]}
    )
    svc.update_spec_entity(
        "test-sprint", "SPEC-004", {"depends_on": ["SPEC-003"]}
    )
    chain_dir = os.path.join(project_root, "data", "spring", "test-sprint")
    prompts_gates_ctx["chain_dir"] = chain_dir
    prompts_gates_ctx["project_root"] = project_root


@when("construyo el contexto del sprint")
def _build_sprint_context(prompts_gates_ctx: dict):
    from features.engine.application.prompt_context_builder import PromptContextBuilder

    chain_dir = prompts_gates_ctx["chain_dir"]
    prompts_gates_ctx["ctx"] = PromptContextBuilder().build(chain_dir)


@when('analizo la salud')
def _analyze_health(prompts_gates_ctx: dict):
    from features.project import HealthReportAnalyzer

    health = prompts_gates_ctx["health"]
    steps = HealthReportAnalyzer.analyze(health, "rnf", "", task_catalog=[])
    step4 = [s for s in steps if s["priority"] == 4]
    prompts_gates_ctx["step4"] = step4[0] if step4 else None


# ── Then ──────────────────────────────────────────────────────────────────────


@then('el template renderizado contiene "QUÉ SUCEDIÓ"')
def _contains_que_sucedio(prompts_gates_ctx: dict):
    assert "QUÉ SUCEDIÓ" in prompts_gates_ctx["result"]


@then("el contexto tiene cola ready con las tareas cuya dependencia está completada")
def _ready_queue(prompts_gates_ctx: dict):
    ready = prompts_gates_ctx["ctx"].get("ready_tasks", "")
    # SPEC-002 (sin deps) y SPEC-003 (dep SPEC-001 completada) listas.
    assert "SPEC-002" in ready, f"SPEC-002 debería estar ready:\n{ready}"
    assert "SPEC-003" in ready, f"SPEC-003 debería estar ready:\n{ready}"
    # SPEC-001 (completed) y SPEC-004 (dep no satisfecha) NO como tarea ready.
    # Nota: "SPEC-001" puede aparecer como dependencia de otra línea; se valida
    # por id al inicio de línea (tarea listada), no por substring.
    ready_ids = {
        line.strip().split()[0]
        for line in ready.splitlines()
        if line.strip().startswith("SPEC-")
    }
    assert "SPEC-001" not in ready_ids, (
        f"SPEC-001 (completed) no debería estar ready:\n{ready}"
    )
    assert "SPEC-004" not in ready_ids, (
        f"SPEC-004 (bloqueada) no debería estar ready:\n{ready}"
    )


@then("el contexto tiene cola blocked con las tareas pendientes de dependencia")
def _blocked_queue(prompts_gates_ctx: dict):
    blocked = prompts_gates_ctx["ctx"].get("blocked_tasks", "")
    assert "SPEC-004" in blocked, f"SPEC-004 debería estar blocked:\n{blocked}"
    blocked_ids = {
        line.strip().split()[0]
        for line in blocked.splitlines()
        if line.strip().startswith("SPEC-")
    }
    assert "SPEC-002" not in blocked_ids, (
        f"SPEC-002 (lista) no debería estar blocked:\n{blocked}"
    )


@then('contiene "Análisis completado"')
def _contains_analisis(prompts_gates_ctx: dict):
    assert "Análisis completado" in prompts_gates_ctx["result"]


@then('contiene "approve"')
def _contains_approve(prompts_gates_ctx: dict):
    assert "approve" in prompts_gates_ctx["result"]


@then('"¿Qué componentes?" aparece en el resultado')
def _contains_que_comp(prompts_gates_ctx: dict):
    assert "¿Qué componentes?" in prompts_gates_ctx["result"]


@then('"A, B, C" aparece en el resultado')
def _contains_abc(prompts_gates_ctx: dict):
    assert "A, B, C" in prompts_gates_ctx["result"]


@then('last_fields.q1 es "¿Qué?"')
def _last_q1(prompts_gates_ctx: dict):
    lf = prompts_gates_ctx["query_result"].get("last_fields", {})
    assert lf.get("q1") == "¿Qué?"


@then('el nodo es "ANALYZE.REVIEW"')
def _node_analyze_review(prompts_gates_ctx: dict):
    assert prompts_gates_ctx["query_result"].get("node") == "ANALYZE.REVIEW"


@then('el reporte contiene "EVIDENCIA"')
def _report_evidencia(prompts_gates_ctx: dict):
    assert "EVIDENCIA" in prompts_gates_ctx["report"]


@then('contiene "META-INSTRUCTION"')
def _report_meta(prompts_gates_ctx: dict):
    assert "META-INSTRUCTION" in prompts_gates_ctx["report"]


@then('contiene "NO CUMPLE"')
def _report_no_cumple(prompts_gates_ctx: dict):
    assert "NO CUMPLE" in prompts_gates_ctx["report"]


@then('contiene "QUÉ SUCEDIÓ"')
def _briefing_que(prompts_gates_ctx: dict):
    assert "QUÉ SUCEDIÓ" in prompts_gates_ctx["briefing"]


@then('contiene "POR QUÉ / EVIDENCIA"')
def _briefing_por_que(prompts_gates_ctx: dict):
    assert "POR QUÉ / EVIDENCIA" in prompts_gates_ctx["briefing"]


@then('contiene "TU SIGUIENTE PASO"')
def _briefing_siguiente(prompts_gates_ctx: dict):
    assert "TU SIGUIENTE PASO" in prompts_gates_ctx["briefing"]


@then('contiene "analysis_approved"')
def _briefing_analysis(prompts_gates_ctx: dict):
    assert "analysis_approved" in prompts_gates_ctx["briefing"]


@then('what_happened contiene "Decisión registrada: approve"')
def _wh_decision(prompts_gates_ctx: dict):
    assert "Decisión registrada: approve" in prompts_gates_ctx["result"]


@then('what_happened contiene "Intención del paso: start_task"')
def _wh_intent(prompts_gates_ctx: dict):
    assert "Intención del paso: start_task" in prompts_gates_ctx["result"]


@then('what_happened contiene "task_data:"')
def _wh_task_data(prompts_gates_ctx: dict):
    assert "task_data:" in prompts_gates_ctx["result"]


@then('what_happened contiene "no hay resultado del paso anterior"')
def _wh_empty(prompts_gates_ctx: dict):
    assert "no hay resultado del paso anterior" in prompts_gates_ctx["result"]


@then('el contexto contiene "what_happened" o "task_table"')
def _ctx_contains_what(prompts_gates_ctx: dict):
    ctx = prompts_gates_ctx["ctx"]
    assert "what_happened" in ctx or "task_table" in ctx


@then('el paso priority 4 sugiere "submit"')
def _step4_submit(prompts_gates_ctx: dict):
    s = prompts_gates_ctx["step4"]
    assert "submit" in s["command"]


@then('no sugiere "transition <gate_name>"')
def _no_transition(prompts_gates_ctx: dict):
    s = prompts_gates_ctx["step4"]
    assert "transition <gate_name>" not in s["command"]


@then('el paso priority 4 sugiere "transition <gate_name>"')
def _step4_transition(prompts_gates_ctx: dict):
    s = prompts_gates_ctx["step4"]
    assert "transition <gate_name>" in s["command"]


@then('no sugiere "submit"')
def _no_submit(prompts_gates_ctx: dict):
    s = prompts_gates_ctx["step4"]
    assert "submit" not in s["command"]
