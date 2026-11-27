"""Contrato del gate SPRINT.REPORT (informe al PO antes de la retro y el cierre).

Reglas que este test fija:

1. Entre REVIEW y RETRO hay un sub-estado REPORT con skill obligatoria.
2. El informe exige resumen, métricas, riesgos y próximos pasos.
3. `review_accepted` lleva a REPORT; `report_delivered` recién ahí a RETRO.
4. NO existe un atajo `close_sprint` desde ACTIVE: la única entrada a CLOSE es
   `close_confirmed` desde CLOSE_CONFIRM (review → report → retro → confirmar).
5. La skill `po-sprint-report` existe en skills-src y matchea la intención.
"""
from pathlib import Path

from fast_mcp.features.workflow.infrastructure.graph_repository import GraphRepository
from fast_mcp.shared_kernel.path_manager import PathManager
from fast_mcp.shared_kernel.skills.index import read_skill, score, search

REPO_ROOT = Path(__file__).resolve().parents[1]


def _graph():
    return GraphRepository(str(PathManager().resolve_workflow_manifest())).load()


def _report_sub_state(graph):
    node = graph.get_node("SPRINT")
    return next(s for s in node.sub_states if s.key == "REPORT")


def test_report_vive_entre_review_y_retro():
    keys = [s.key for s in _graph().get_node("SPRINT").sub_states]
    assert "REPORT" in keys
    assert keys.index("REPORT") == keys.index("REVIEW") + 1
    assert keys.index("RETRO") == keys.index("REPORT") + 1


def test_report_exige_informe_y_skill():
    report = _report_sub_state(_graph())
    assert report.skill_intent.get("policy") == "required"
    assert report.skill_intent.get("tags")
    required = set((report.input_schema or {}).get("required") or [])
    assert {"summary", "metrics", "risks", "next"} <= required


def test_las_aristas_no_saltean_el_informe():
    graph = _graph()
    assert graph.find_gate("SPRINT", "review_accepted").target_sub_state == "REPORT"
    assert graph.find_gate("SPRINT", "report_delivered").target_sub_state == "RETRO"
    # Sin atajo: la única arista que entra a CLOSE es la confirmación del PO.
    to_close = {e.name for e in graph.edges if e.target_sub_state == "CLOSE"}
    assert to_close == {"close_confirmed"}
    assert graph.find_gate("SPRINT", "close_sprint") is None


def test_la_skill_po_sprint_report_existe_y_matchea():
    skill = read_skill(REPO_ROOT / "skills-src" / "po-sprint-report", scope="walker")
    assert skill is not None, "falta skills-src/po-sprint-report/SKILL.md"
    assert skill.name == "po-sprint-report"

    report = _report_sub_state(_graph())
    intent = report.skill_intent
    hits = search({skill.name: skill}, intent.get("needs", ""), tuple(intent.get("tags") or ()))
    assert [s.name for s in hits] == ["po-sprint-report"]
    assert score(skill, intent.get("needs", ""), intent.get("tags") or ()) >= 3


def test_el_prompt_del_reporte_apunta_a_la_skill():
    """El catálogo de skills se inyecta en el prompt del nodo; si el template
    no tiene el placeholder, el agente solo se entera al chocar con el gate."""
    import json

    path = (
        REPO_ROOT
        / "fast_mcp/features/workflow/application/prompts/templates/nodes/sprint/report.prompt.json"
    )
    template = json.loads(path.read_text(encoding="utf-8"))["template"]
    assert "{{skills_catalog}}" in template
    assert "po-sprint-report" in template or "informe" in template.lower()


def test_el_cierre_recibe_el_informe_registrado():
    """CLOSE_CONFIRM tiene que recibir el informe ya registrado, no pedirlo de memoria."""
    from types import SimpleNamespace

    from fast_mcp.features.workflow.application.prompt_context import (
        sprint_deliverable_context,
    )

    state = SimpleNamespace(sub_state_data={
        "REPORT": {"fields": {
            "summary": "Se entrego X",
            "metrics": {"specs_completed": 5, "commits": ["a", "b"]},
            "risks": ["deuda abierta"],
            "next": ["paso concreto"],
        }},
        "RETRO": {"fields": {"lecciones": [{"leccion": "leccion uno"}]}},
    })

    text = sprint_deliverable_context(state)["sprint_report"]

    for expected in ("Se entrego X", "specs_completed: 5", "a, b",
                     "deuda abierta", "paso concreto", "leccion uno"):
        assert expected in text


def test_el_prompt_de_cierre_muestra_el_informe():
    import json

    path = (
        REPO_ROOT
        / "fast_mcp/features/workflow/application/prompts/templates/nodes/sprint/close-confirm.prompt.json"
    )
    template = json.loads(path.read_text(encoding="utf-8"))["template"]

    assert "{sprint_report}" in template
    assert "No lo resumas de memoria" in template
