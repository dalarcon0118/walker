"""test_prompt_existing_work_bdd.py — el primer nodo arranca desde lo declarado.

`AGENT-EFFICIENCY.md` §8.8: el workflow preguntaba "elegí un proyecto" cuando la
sesión ya tenía tarea, criterios y alcance en su plan de vuelo. El plan se
escribe con `implement_task`, mucho antes de tocar un nodo.

`session_plan_context` es la traducción pura: plan → contexto de prompt. Si no
hay tarea, devuelve `{}` (no se inventa contexto).
"""
from __future__ import annotations

from fast_mcp.features.workflow.application.prompt_context import session_plan_context


def test_plan_completo_se_proyecta_al_prompt():
    ctx = session_plan_context(
        {
            "goal": "Alinear el harness con Fable 5.2",
            "task": "f5-alineacion-fable",
            "acceptance": ["Debe quedar un documento", "sin romper los tests"],
            "scope": ["docs/AGENT-EFFICIENCY.md", "index.js"],
            "next": "implementar y verificar",
        }
    )

    assert ctx["task_id"] == "f5-alineacion-fable", "el task_id de la sesión gana"
    work = ctx["existing_work"]
    assert work["task"] == "f5-alineacion-fable"
    assert work["acceptance"] == ["Debe quedar un documento", "sin romper los tests"]
    assert work["scope"] == ["docs/AGENT-EFFICIENCY.md", "index.js"]
    assert work["next"] == "implementar y verificar"


def test_sin_tarea_no_hay_contexto():
    assert session_plan_context({}) == {}
    assert session_plan_context(None) == {}
    assert session_plan_context({"goal": "algo sin tarea"}) == {}
    assert session_plan_context({"task": "   "}) == {}


def test_listas_largas_se_recortan_y_los_vacios_se_descartan():
    ctx = session_plan_context(
        {
            "task": "t",
            "acceptance": [f"criterio {i}" for i in range(9)] + ["", "   "],
            "scope": [f"f{i}.py" for i in range(9)],
        }
    )

    assert len(ctx["existing_work"]["acceptance"]) == 5
    assert len(ctx["existing_work"]["scope"]) == 5
    assert all(a.strip() for a in ctx["existing_work"]["acceptance"])


def test_campos_faltantes_quedan_vacios_no_en_none():
    ctx = session_plan_context({"task": "t"})

    work = ctx["existing_work"]
    assert work["goal"] == ""
    assert work["next"] == ""
    assert work["acceptance"] == []
    assert work["scope"] == []


def test_linea_renderizada_para_el_template():
    ctx = session_plan_context(
        {
            "task": "f5",
            "acceptance": ["Debe quedar documento", "sin romper tests"],
            "next": "implementar",
        }
    )
    line = ctx["existing_work_line"]
    assert "YA declaró la tarea `f5`" in line
    assert "1) Debe quedar documento" in line
    assert "intent=project_selected" in line
    # Sin plan no hay línea: el template no muestra nada.
    assert "existing_work_line" not in session_plan_context({})


def test_intent_es_alias_de_decision_en_submit():
    """El template manda `intent`; el motor lee `decision`. Sin alias, no cruza."""
    from fast_mcp.features.workflow.handlers.workflow import _extract_fields

    fields = _extract_fields({"intent": "project_selected", "project_id": "p1"})
    assert fields["decision"] == "project_selected"
    # Si vienen los dos, manda el explícito.
    both = _extract_fields({"intent": "project_selected", "decision": "create_project"})
    assert both["decision"] == "create_project"
