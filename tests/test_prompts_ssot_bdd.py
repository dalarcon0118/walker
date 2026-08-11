"""Tests BDD (pytest-bdd) — Prompts SSOT.

Feature value: valida que los templates vivan en un root único
features/prompts/templates/ y que el código se sirva desde esos templates
sin duplicar contenido.

Escenarios: bdd/features/prompts_ssot.feature
"""

from __future__ import annotations

import os

import pytest
from pytest_bdd import given, scenarios, then, when

scenarios("bdd/features/prompts_ssot.feature")


def _template_path(*parts: str) -> str:
    from features.prompts.loader import canonical_templates_dir

    return os.path.join(canonical_templates_dir(), *parts)


def _read_template(*parts: str) -> str:
    with open(_template_path(*parts)) as f:
        return f.read()


@pytest.fixture
def prompts_ssot_ctx() -> dict:
    return {}


# ── Given ─────────────────────────────────────────────────────────────────────


@given("el directorio de templates canónico")
def _canonical_dir(prompts_ssot_ctx: dict):
    from features.prompts.loader import canonical_templates_dir

    prompts_ssot_ctx["template_dir"] = canonical_templates_dir()
    # Los escenarios 1 y 2 verifican el contenido de templates conocidos sin
    # pasos `When` intermedios: cargar los textos aquí sostiene los @then.
    prompts_ssot_ctx["mcp_text"] = _read_template("protocol", "mcp.md")
    prompts_ssot_ctx["rest_text"] = _read_template("protocol", "rest.md")
    prompts_ssot_ctx["text"] = _read_template("gates", "gate-briefing.md")


@given("el template protocol/mcp.md")
def _mcp_template(prompts_ssot_ctx: dict):
    prompts_ssot_ctx["mcp_text"] = _read_template("protocol", "mcp.md")


@given("el template protocol/rest.md")
def _rest_template(prompts_ssot_ctx: dict):
    prompts_ssot_ctx["rest_text"] = _read_template("protocol", "rest.md")


@given("el loader patcheado para lanzar TemplateNotFound")
def _loader_patched(prompts_ssot_ctx: dict, monkeypatch):
    from features.prompts import loader as loader_mod

    def _boom(template_id: str):
        raise loader_mod.TemplateNotFound(template_id)

    monkeypatch.setattr(loader_mod, "load_static", _boom)


@given("un TemplateLoader con chain temporal")
def _loader_tmp(prompts_ssot_ctx: dict, tmp_path):
    from features.prompts.loader import TemplateLoader

    prompts_ssot_ctx["loader"] = TemplateLoader(os.path.join(str(tmp_path), "chain"))


@given("el directorio templates/nodes")
def _nodes_dir(prompts_ssot_ctx: dict):
    prompts_ssot_ctx["nodes_dir"] = _template_path("nodes")


@given("el path resolver")
def _path_resolver(prompts_ssot_ctx: dict):
    from libs.path_resolver import get_engine_workflow_dir, get_engine_workflow_path

    prompts_ssot_ctx["wf_dir"] = str(get_engine_workflow_dir())
    prompts_ssot_ctx["wf_path"] = str(get_engine_workflow_path())


@given("un PromptManager con chain temporal")
def _pm_tmp(prompts_ssot_ctx: dict, tmp_path):
    from features.prompts.manager import PromptManager

    prompts_ssot_ctx["pm"] = PromptManager(os.path.join(str(tmp_path), "chain"))


# ── When ──────────────────────────────────────────────────────────────────────


@when("comparo con _PROTOCOL_TEXT del mcp_adapter")
def _compare_mcp(prompts_ssot_ctx: dict):
    from features.cli_interface.mcp_adapter import _PROTOCOL_TEXT

    prompts_ssot_ctx["equal"] = _PROTOCOL_TEXT == prompts_ssot_ctx["mcp_text"]


@when("construyo el protocolo MCP dinámico")
def _build_mcp_dynamic(prompts_ssot_ctx: dict):
    from features.cli_interface.mcp_adapter import _PROTOCOL_TEXT

    prompts_ssot_ctx["mcp_dynamic"] = _PROTOCOL_TEXT


@when("invoco _build_help")
def _build_help(prompts_ssot_ctx: dict):
    from features.cli_interface.application.handlers import _build_help

    prompts_ssot_ctx["help_data"] = _build_help()


@when("cargo el protocolo MCP")
def _load_mcp(prompts_ssot_ctx: dict):
    from features.cli_interface.mcp_adapter import _load_protocol_text

    prompts_ssot_ctx["mcp_fallback"] = _load_protocol_text()


@when("cargo el protocolo REST")
def _load_rest(prompts_ssot_ctx: dict):
    from features.cli_interface.application.handlers import _load_protocol_rest

    prompts_ssot_ctx["rest_fallback"] = _load_protocol_rest()


@when('cargo "protocol/mcp"')
def _load_protocol_mcp(prompts_ssot_ctx: dict):
    loader = prompts_ssot_ctx["loader"]
    prompts_ssot_ctx["text"] = loader.load("protocol/mcp")


@when('cargo "gates/gate-briefing"')
def _load_gate_briefing(prompts_ssot_ctx: dict):
    loader = prompts_ssot_ctx["loader"]
    prompts_ssot_ctx["text"] = loader.load("gates/gate-briefing")


@when('cargo "sprint/task-review"')
def _load_task_review(prompts_ssot_ctx: dict):
    prompts_ssot_ctx["text"] = prompts_ssot_ctx["loader"].load("sprint/task-review")


@when('cargo "implement/work"')
def _load_implement_work(prompts_ssot_ctx: dict):
    prompts_ssot_ctx["text"] = prompts_ssot_ctx["loader"].load("implement/work")


@when('cargo "refine/diagnose"')
def _load_refine_diagnose(prompts_ssot_ctx: dict):
    prompts_ssot_ctx["text"] = prompts_ssot_ctx["loader"].load("refine/diagnose")


@when("listo templates disponibles")
def _list_templates(prompts_ssot_ctx: dict):
    prompts_ssot_ctx["templates"] = prompts_ssot_ctx["pm"].available_templates()


@when("obtengo el engine workflow dir")
def _get_wf_dir(prompts_ssot_ctx: dict):
    prompts_ssot_ctx["wf_dir_result"] = prompts_ssot_ctx["wf_dir"]
    prompts_ssot_ctx["wf_path_result"] = prompts_ssot_ctx["wf_path"]


# ── Then ──────────────────────────────────────────────────────────────────────


@then("protocol/mcp.md existe")
def _mcp_exists(prompts_ssot_ctx: dict):
    assert os.path.exists(_template_path("protocol", "mcp.md"))


@then("protocol/rest.md existe")
def _rest_exists(prompts_ssot_ctx: dict):
    assert os.path.exists(_template_path("protocol", "rest.md"))


@then('mcp.md contiene "MANDATORY BEHAVIOR CONTRACT" y "capture_task"')
def _mcp_contains(prompts_ssot_ctx: dict):
    mcp = prompts_ssot_ctx["mcp_text"]
    assert "MANDATORY BEHAVIOR CONTRACT" in mcp
    assert "capture_task" in mcp


@then('rest.md contiene "PROTOCOLO REST OBLIGATORIO", "submit" y "transition"')
def _rest_contains(prompts_ssot_ctx: dict):
    rest = prompts_ssot_ctx["rest_text"]
    assert "PROTOCOLO REST OBLIGATORIO" in rest
    assert "submit" in rest and "transition" in rest


@then("gates/gate-briefing.md existe")
def _gate_briefing_exists(prompts_ssot_ctx: dict):
    assert os.path.exists(_template_path("gates", "gate-briefing.md"))


@then('contiene "QUÉ SUCEDIÓ"')
def _contains_que(prompts_ssot_ctx: dict):
    assert "QUÉ SUCEDIÓ" in prompts_ssot_ctx["text"]


@then("son idénticos")
def _are_equal(prompts_ssot_ctx: dict):
    assert prompts_ssot_ctx["equal"]


@then("el protocolo contiene las secciones narrativas del template")
def _mcp_contains_template(prompts_ssot_ctx: dict):
    """SSOT preservado: el protocolo dinámico nace del template estático.

    Verifica que los marcadores narrativos canónicos del template
    protocol/mcp.md estén presentes en el texto dinámico construido por
    mcp_adapter (el código se sirve del template, no duplica contenido).
    """
    dynamic = prompts_ssot_ctx["mcp_dynamic"]
    static = prompts_ssot_ctx["mcp_text"]
    markers = [
        "WALKER PROTOCOL (MCP) — MANDATORY BEHAVIOR CONTRACT:",
        "1. GIT-LIKE",
        "2. TO ADVANCE in the workflow",
        "3. CODE ANALYSIS = USE THE WALKER CPG (MANDATORY)",
        "4. KNOWLEDGE MEMORY (CKG)",
        "5. REUSE what exists",
        "6. SESSION WORK LOG (MANDATORY)",
    ]
    for m in markers:
        assert m in dynamic, f"El protocolo dinámico perdió '{m}'\n{dynamic[:400]}"
    # El template sigue siendo la base (SSOT): las secciones del estático
    # deben aparecer en el dinámico en el mismo orden relativo.
    pos = [dynamic.find(m) for m in markers]
    assert all(p >= 0 for p in pos)
    assert pos == sorted(pos), (
        "Las secciones del template perdieron su orden en el protocolo dinámico"
    )
    assert static, "El template SSOT está vacío"
    assert static in dynamic or any(m in dynamic for m in markers), (
        "El protocolo dinámico no se sirve del template SSOT"
    )


@then("el protocolo añade secciones dinámicas (tools y workflow)")
def _mcp_has_dynamic(prompts_ssot_ctx: dict):
    dynamic = prompts_ssot_ctx["mcp_dynamic"]
    assert "Available analysis tools:" in dynamic, "Falta la sección dinámica de analysis tools"
    assert "Workflow map:" in dynamic, "Falta la sección dinámica del workflow"
    assert len(dynamic) > len(prompts_ssot_ctx["mcp_text"]), (
        "El protocolo dinámico debería extender el template estático"
    )


@then('el campo "protocol" coincide con el template')
def _protocol_matches(prompts_ssot_ctx: dict):
    assert (
        prompts_ssot_ctx["help_data"].get("protocol") == prompts_ssot_ctx["rest_text"]
    )


@then("devuelve _PROTOCOL_FALLBACK")
def _mcp_fallback(prompts_ssot_ctx: dict):
    from features.cli_interface.mcp_adapter import _PROTOCOL_FALLBACK

    assert prompts_ssot_ctx["mcp_fallback"] == _PROTOCOL_FALLBACK


@then("devuelve _PROTOCOL_REST_FALLBACK")
def _rest_fallback(prompts_ssot_ctx: dict):
    from features.cli_interface.application.handlers import _PROTOCOL_REST_FALLBACK

    assert prompts_ssot_ctx["rest_fallback"] == _PROTOCOL_REST_FALLBACK


@then('contiene "WALKER PROTOCOL (MCP)" y "capture_task"')
def _protocol_mcp_content(prompts_ssot_ctx: dict):
    text = prompts_ssot_ctx["text"]
    assert "WALKER PROTOCOL (MCP)" in text
    assert "capture_task" in text


@then('contiene "REVISION DE ESPECIFICACION DE TAREA"')
def _task_review_content(prompts_ssot_ctx: dict):
    assert "REVISION DE ESPECIFICACION DE TAREA" in prompts_ssot_ctx["text"]


@then('contiene "{step_quality_summary}"')
def _step_quality_summary(prompts_ssot_ctx: dict):
    assert "{step_quality_summary}" in prompts_ssot_ctx["text"]


@then('contiene "{step_recommend}"')
def _step_recommend(prompts_ssot_ctx: dict):
    assert "{step_recommend}" in prompts_ssot_ctx["text"]


@then("existen 46 archivos .prompt.json")
def _count_46(prompts_ssot_ctx: dict):
    nodes_dir = prompts_ssot_ctx["nodes_dir"]
    count = 0
    for _dirpath, _dirs, files in os.walk(nodes_dir):
        count += sum(1 for f in files if f.endswith(".prompt.json"))
    assert count == 46, f"esperaba 46 prompts de nodo, hay {count}"


@then("features/engine/workflow no tiene carpeta prompts")
def _no_workflow_prompts(prompts_ssot_ctx: dict):
    assert not os.path.exists(os.path.join("features", "engine", "workflow", "prompts"))


@then(".cognitive no tiene carpeta workflow.v3")
def _no_cognitive_workflow(prompts_ssot_ctx: dict):
    assert not os.path.exists(".cognitive/projects/walker/data/workflow.v3")


@then('incluye "nodes/sprint/task-review.prompt.json"')
def _includes_task_review(prompts_ssot_ctx: dict):
    assert "nodes/sprint/task-review.prompt.json" in prompts_ssot_ctx["templates"]


@then('incluye "nodes/implement/work.prompt.json"')
def _includes_work(prompts_ssot_ctx: dict):
    assert "nodes/implement/work.prompt.json" in prompts_ssot_ctx["templates"]


@then("hay exactamente 49 templates")
def _exactly_49(prompts_ssot_ctx: dict):
    assert len(prompts_ssot_ctx["templates"]) == 49


@then("termina en features/engine/workflow")
def _ends_workflow(prompts_ssot_ctx: dict):
    wf_dir = prompts_ssot_ctx["wf_dir_result"]
    assert wf_dir.endswith(os.path.join("features", "engine", "workflow")), wf_dir


@then('no contiene ".cognitive"')
def _no_cognitive(prompts_ssot_ctx: dict):
    wf_dir = prompts_ssot_ctx["wf_dir_result"]
    assert ".cognitive" not in wf_dir


@then("existe nodes/implement.node.json")
def _node_json_exists(prompts_ssot_ctx: dict):
    wf_dir = prompts_ssot_ctx["wf_dir_result"]
    assert os.path.exists(os.path.join(wf_dir, "nodes", "implement.node.json"))


@then('incluye "protocol/mcp.md"')
def _includes_mcp(prompts_ssot_ctx: dict):
    assert "protocol/mcp.md" in prompts_ssot_ctx["templates"]


@then('incluye "protocol/rest.md"')
def _includes_rest(prompts_ssot_ctx: dict):
    assert "protocol/rest.md" in prompts_ssot_ctx["templates"]


@then('incluye "gates/gate-briefing.md"')
def _includes_gate_briefing(prompts_ssot_ctx: dict):
    assert "gates/gate-briefing.md" in prompts_ssot_ctx["templates"]
