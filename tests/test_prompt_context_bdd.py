"""BDD: el briefing de los nodos se arma con datos REALES, no con placeholders.

Motivación medida (2026-11-18): todos los gates `user_input` mostraban
"QUÉ SUCEDIÓ" y "POR QUÉ / EVIDENCIA" vacíos. El `PromptManager` solo pinta lo
que recibe; nadie armaba `what_happened`, la calidad del paso previo, las
tablas de la tarea ni las decisiones del CKG.
"""
from __future__ import annotations

from types import SimpleNamespace

from fast_mcp.features.workflow.application import prompt_context as pc


def _exec(node, sub, action="", data=None, ok=True, error=None):
    return SimpleNamespace(
        node=node,
        sub_state=sub,
        action=action,
        task_ok=ok,
        error=error,
        task_result={"data": data or {}},
        submitted_fields={},
    )


def _state(*execs, context=None):
    return SimpleNamespace(node_executions=list(execs), context=context or {})


def test_what_happened_sale_del_ultimo_paso():
    state = _state(
        _exec("IMPLEMENT", "WORK", "implement_task", {"summary": "código escrito"}),
        _exec(
            "IMPLEMENT",
            "KNOWLEDGE_UPDATE",
            "capture_knowledge",
            {"statement": "el gate no cerraba", "files": ["a.js", "b.py"]},
        ),
    )

    text = pc.last_step_summary(state)

    assert text.startswith("capture_knowledge — IMPLEMENT.KNOWLEDGE_UPDATE")
    assert "el gate no cerraba" in text
    assert "a.js, b.py" in text


def test_what_happened_marca_fallo_y_error():
    state = _state(_exec("IMPLEMENT", "WORK", "implement_task", {}, ok=False, error="boom"))

    text = pc.last_step_summary(state)

    assert "(falló)" in text
    assert "error: boom" in text


def test_what_happened_sin_pasos_es_vacio():
    assert pc.last_step_summary(_state()) == ""


def test_calidad_sale_de_la_ultima_revision():
    state = _state(
        _exec("IMPLEMENT", "CODE_REVIEW_SCAN", "review_code", {"passed": False, "summary": "2 hallazgos"}),
        _exec("IMPLEMENT", "KNOWLEDGE_UPDATE", "capture_knowledge", {"statement": "x"}),
    )

    out = pc.quality_context(state)

    assert out["step_quality_passed"] == "no"
    assert out["step_quality_summary"] == "2 hallazgos"
    assert out["step_failed_gates"] == "(ninguno)"
    assert "corregir" in out["step_recommend"]


def test_calidad_lista_hallazgos():
    state = _state(
        _exec(
            "IMPLEMENT",
            "CODE_REVIEW_SCAN",
            "review_code",
            {"passed": True, "findings": [{"message": "import sin usar"}, "variable muerta"]},
        )
    )

    out = pc.quality_context(state)

    assert out["step_quality_passed"] == "sí"
    assert "import sin usar" in out["step_failed_gates"]
    assert "variable muerta" in out["step_failed_gates"]


def test_calidad_sin_datos_no_inventa():
    assert pc.quality_context(_state(_exec("IMPLEMENT", "WORK", "implement_task", {"summary": "x"}))) == {}


def test_spec_context_arma_tablas_y_predicados():
    spec = {
        "spec_id": "SPEC-002",
        "spec_type": "task",
        "domain": "harness",
        "status": "in_progress",
        "objective": "cerrar solo con verificación",
        "problem_statement": "el shell certificaba",
        "files": ["index.js"],
        "acceptance_criteria": ["no cerrar sin verified"],
        "risks": {"r1": "romper el harness"},
        "predicates": {"items": [{"type": "file_exists", "path": "index.js"}]},
    }

    out = pc.spec_context(spec)

    assert "Tarea: SPEC-002" in out["task_table"]
    assert "no cerrar sin verified" in out["task_table"]
    assert "r1: romper el harness" in out["risk_table"]
    assert out["predicates"].strip() == "- file_exists: index.js"


def test_spec_context_sin_riesgos_lo_dice():
    out = pc.spec_context({"spec_id": "SPEC-1", "objective": "x"})

    assert out["risk_table"] == "Riesgos: (ninguno declarado)"
    assert "predicates" not in out


def test_spec_context_sin_spec_es_vacio():
    assert pc.spec_context(None) == {}


def test_sprint_extra_cuenta_listas_y_tarea():
    specs = [
        {"spec_id": "SPEC-001", "objective": "uno", "status": "completed"},
        {"spec_id": "SPEC-002", "objective": "dos", "status": "specified", "depends_on": []},
        {"spec_id": "SPEC-003", "objective": "tres", "status": "specified", "depends_on": ["SPEC-002"]},
    ]
    spring = {"spring_id": "SPR-T", "goal": "meta", "status": "active"}

    out = pc.sprint_extra(specs, spring, "SPEC-002")

    assert out["sprint_goal"] == "meta"
    assert out["goal_display"] == "Goal: meta"
    assert out["task_count"] == 3
    assert out["completed_count"] == 1
    assert "SPEC-002" in out["ready_tasks"]
    assert "SPEC-003" in out["blocked_tasks"]
    assert out["task_id"] == "SPEC-002"
    assert out["task_title"] == "dos"


def test_build_prompt_extra_no_falla_sin_sprint(monkeypatch):
    def boom(chain_dir):
        raise RuntimeError("sin sprint")

    monkeypatch.setattr("fast_mcp.composition.build_sprint_service", boom)
    state = _state(_exec("IMPLEMENT", "WORK", "implement_task", {"summary": "escrito"}))

    out = pc.build_prompt_extra(state, "/tmp/x", "s1")

    assert out["what_happened"].startswith("implement_task")
    assert "sprint_goal" not in out


def test_decisions_context_sin_ckg_es_vacio(monkeypatch):
    monkeypatch.setattr("fast_mcp.composition.ckg_for", lambda chain_dir: None)

    assert pc.decisions_context("/tmp/x", "SPEC-002") == {}


def test_decisions_context_prioriza_la_tarea(monkeypatch):
    nodes = [
        {"_id": "decision-a", "label": "otra", "metadata": {"spec_id": "SPEC-999"}},
        {"_id": "decision-b", "label": "la de la tarea", "metadata": {"spec_id": "SPEC-002"}},
    ]
    fake = SimpleNamespace(query=lambda filters: nodes)
    monkeypatch.setattr("fast_mcp.composition.ckg_for", lambda chain_dir: fake)

    out = pc.decisions_context("/tmp/x", "SPEC-002")

    assert "decision-b" in out["decisions_list"]
    assert "decision-a" not in out["decisions_list"]


# ── el 6->3 que el agente YA envió ──────────────────────────────────────
# Medido 2026-11-18: ANALYZE.REVIEW renderizaba `{q1}..{a3}` y
# `{preconditions_list}` en blanco. El dato existía en `sub_state_data`; nadie
# lo pasaba al template, así que el PO decidía sin ver el análisis.

def test_submitted_context_expone_el_6a3_y_las_precondiciones():
    state = SimpleNamespace(
        current_node="ANALYZE",
        sub_state_data={
            "ANALYZE": SimpleNamespace(
                fields={
                    "q1": "¿qué existe?",
                    "a1": "index.js:628",
                    "q2": "¿impacto?",
                    "a2": "ninguno",
                    "q3": "¿enfoque?",
                    "a3": "traza",
                    "preconditions": ["grep MAX_SESSIONS => rc=0"],
                }
            )
        },
    )

    out = pc.submitted_context(state)

    assert out["q1"] == "¿qué existe?"
    assert out["a3"] == "traza"
    assert out["preconditions"] == ["grep MAX_SESSIONS => rc=0"]


def test_submitted_context_acepta_el_shape_de_json():
    state = SimpleNamespace(
        current_node="ANALYZE",
        sub_state_data={"ANALYZE": {"fields": {"q1": "q", "a1": "a"}}},
    )

    assert pc.submitted_context(state)["a1"] == "a"


def test_submitted_context_ignora_nodos_sin_analisis():
    state = SimpleNamespace(
        current_node="VERIFY",
        sub_state_data={"VERIFY": SimpleNamespace(fields={"q1": "x"})},
    )

    assert pc.submitted_context(state) == {}


def test_el_extra_usa_el_6a3_y_no_inventa_riesgos(monkeypatch):
    monkeypatch.setattr("fast_mcp.composition.build_sprint_service", lambda *a, **k: None)
    state = SimpleNamespace(
        current_node="ANALYZE",
        sub_state_data={
            "ANALYZE": SimpleNamespace(
                fields={"q1": "q", "a1": "a", "preconditions": ["p1"]}
            )
        },
        node_executions=[],
        context={},
    )

    extra = pc.build_prompt_extra(state, "/tmp", "")

    assert extra["q1"] == "q"
    assert extra["preconditions_list"] == "- p1"
    assert extra["curvature"] == "no declarado"


def test_los_hallazgos_del_cpg_se_listan_con_archivo_y_linea():
    state = SimpleNamespace(
        node_executions=[
            _exec(
                "IMPLEMENT",
                "CODE_REVIEW_SCAN",
                data={
                    "passed": False,
                    "findings": [
                        {"source": "cpg", "type": "dead_code", "function": "f", "file": "a.js", "line": 7}
                    ],
                },
            )
        ],
        context={},
    )

    quality = pc.quality_context(state)

    assert quality["step_failed_gates"] == "f (a.js:7)"
    assert quality["step_quality_passed"] == "no"
