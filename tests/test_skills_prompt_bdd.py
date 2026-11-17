"""test_skills_prompt_bdd.py — intención del nodo → catálogo → herramientas.

Cierra el circuito del rediseño: el nodo declara `skill_intent` (needs/tags), el
prompt recibe el catálogo de lo que EXISTE en el entorno, y el agente carga el
skill con `load_skill`. Si no hay match, el prompt lo dice explícitamente.
"""

from __future__ import annotations

import json
from pathlib import Path

from fast_mcp.features.workflow.application.prompts.collector import collect_skills
from fast_mcp.features.workflow.application.prompts.manager import PromptManager
from fast_mcp.memories.context import MemoryContext
from fast_mcp.memories.handlers.knowledge import (
    handle_load_skill,
    handle_search_skills,
)

TAG = "walker-demo-xyz"


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".cognitive" / "projects" / "demo" / "data").mkdir(parents=True)
    (root / "project.json").write_text(
        json.dumps({"project_id": "demo", "repo_path": str(root)})
    )
    skill = root / ".agents" / "skills" / "demo-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\n"
        "name: demo-skill\n"
        "description: Criterio de prueba para la fase\n"
        f"tags: [{TAG}]\n"
        "---\n"
        "Contenido del skill de prueba.\n",
        encoding="utf-8",
    )
    return root


def _ctx(root: Path) -> MemoryContext:
    return MemoryContext(
        chain_dir=str(root), project_root=str(root), project_id="demo",
        data_dir=str(root / ".cognitive" / "projects" / "demo" / "data"),
    )


def test_collect_skills_encuentra_por_tag(tmp_path):
    root = _project(tmp_path)

    skills = collect_skills(str(root), {"tags": [TAG]})

    assert [s.name for s in skills] == ["demo-skill"]
    assert skills[0].scope == "project"


def test_collect_skills_sin_intencion_no_escanea(tmp_path):
    root = _project(tmp_path)
    assert collect_skills(str(root), {}) == []


def test_prompt_manager_inyecta_catalogo(tmp_path):
    root = _project(tmp_path)
    manager = PromptManager(str(root))

    data = manager._skills_data({"skill_intent": {"needs": "criterio", "tags": [TAG]}})

    assert "`demo-skill`" in data["skills_catalog"]
    assert "load_skill(<nombre>)" in data["skills_catalog"]
    assert data["skills_count"] == "1"


def test_prompt_manager_sin_match_lo_dice(tmp_path):
    root = _project(tmp_path)
    manager = PromptManager(str(root))

    data = manager._skills_data({"skill_intent": {"tags": ["no-existe-jamas"]}})

    assert "Sin skills locales" in data["skills_catalog"]
    assert data["skills_count"] == "0"


def test_el_catalogo_se_renderiza_en_el_template(tmp_path):
    from fast_mcp.features.workflow.application.prompts.renderer import TemplateRenderer

    root = _project(tmp_path)
    manager = PromptManager(str(root))
    data = manager._skills_data({"skill_intent": {"tags": [TAG]}})

    text = TemplateRenderer().render("SKILLS:\n{{skills_catalog}}", data)

    assert "SKILLS:" in text
    assert "`demo-skill`" in text


def test_search_skills_tool(tmp_path):
    root = _project(tmp_path)

    out = handle_search_skills(_ctx(root), tags=[TAG])

    assert out["count"] == 1
    assert out["skills"][0]["name"] == "demo-skill"


def test_load_skill_tool_devuelve_contenido(tmp_path):
    root = _project(tmp_path)

    out = handle_load_skill(_ctx(root), name="demo-skill")

    assert out["chars"] > 0
    assert "Contenido del skill de prueba" in out["content"]
    assert out["scope"] == "project"


def test_load_skill_desconocido_lista_los_disponibles(tmp_path):
    root = _project(tmp_path)

    out = handle_load_skill(_ctx(root), name="no-existe")

    assert "no encontrado" in out["error"]
    assert "available" in out


def test_nodos_y_templates_declaran_la_intencion():
    """Anti-drift: si se declara intención, el template tiene el placeholder."""
    base = Path(__file__).resolve().parents[1]
    nodes = base / "data" / "workflow" / "nodes"
    templates = base / "fast_mcp/features/workflow/application/prompts/templates/nodes"

    implement = json.loads((nodes / "implement.node.json").read_text())
    state = next(s for s in implement["sub_states"] if s["key"] == "STATE_CONSTRUCTION")
    assert state["skill_intent"]["tags"], "el nodo debe declarar intención"
    assert "{{skills_catalog}}" in (
        templates / "implement" / "state_construction.prompt.json"
    ).read_text()

    verify = json.loads((nodes / "verify.node.json").read_text())
    qa = next(s for s in verify["sub_states"] if s["key"] == "QA")
    assert qa["skill_intent"]["tags"]
    assert "{{skills_catalog}}" in (templates / "verify" / "qa.prompt.json").read_text()
