"""Tests BDD (pytest-bdd) — Gate de auto-consulta de convenciones CKG en IMPLEMENT.WORK.

Feature value: el agente consulta automaticamente las convenciones del CKG antes
de tocar codigo, mediante provider ckg + focus con constraint de auto-consulta +
prompt que ordena memory_schema/search_memories.

Escenarios: bdd/features/auto_query_gate.feature
"""

from __future__ import annotations

import gzip
import json
import os
import tempfile
from types import SimpleNamespace

import pytest
from pytest_bdd import given, scenarios, then, when

scenarios("bdd/features/auto_query_gate.feature")

NODE_PATH = os.path.join(
    "core", "engine", "workflow", "nodes", "implement.node.json"
)
DESIGN_NODE_PATH = os.path.join(
    "core", "engine", "workflow", "nodes", "design.node.json"
)


@pytest.fixture
def gate_ctx() -> dict:
    return {}


def _load(path):
    with open(path) as f:
        return json.load(f)


def _write_ckg_min(path: str, docs: list[dict]) -> None:
    payload = {"index": "ckg", "docs": docs, "edges": []}
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)


def _work_substate(node: dict) -> dict:
    for ss in node.get("sub_states", []):
        if ss.get("key") == "WORK":
            return ss
    raise AssertionError("sub-estado WORK no encontrado en implement.node.json")


def _design_gather(node: dict) -> dict:
    for ss in node.get("sub_states", []):
        if ss.get("key") == "DESIGN_GATHER":
            return ss
    raise AssertionError("sub-estado DESIGN_GATHER no encontrado en design.node.json")


@given(
    "que el nodo IMPLEMENT esta definido en core/engine/workflow/nodes/implement.node.json"
)
def _load_implement_node(gate_ctx: dict):
    gate_ctx["node"] = _load(NODE_PATH)


@when("se inspecciona el sub-estado WORK del nodo IMPLEMENT")
def _inspect_work(gate_ctx: dict):
    gate_ctx["work"] = _work_substate(gate_ctx["node"])
    gate_ctx["sources"] = gate_ctx["work"].get("context", {}).get("sources", [])


@then("WORK tiene una fuente con provider ckg")
def _assert_work_ckg_provider(gate_ctx: dict):
    ckg_sources = [s for s in gate_ctx["sources"] if s.get("provider") == "ckg"]
    assert ckg_sources, (
        f"WORK no declara provider ckg. Sources: {[s.get('provider') for s in gate_ctx['sources']]}"
    )


@then("la config de esa fuente declara query type:convention")
def _assert_work_ckg_query(gate_ctx: dict):
    ckg_sources = [s for s in gate_ctx["sources"] if s.get("provider") == "ckg"]
    query = ckg_sources[0].get("config", {}).get("query", {})
    assert query.get("type") == "convention", (
        f"query ckg debe pedir type:convention: {query}"
    )


@when("se inspecciona el focus de WORK")
def _inspect_work_focus(gate_ctx: dict):
    gate_ctx["focus"] = gate_ctx["work"].get("context", {}).get("focus", {})
    gate_ctx["constraints"] = " ".join(gate_ctx["focus"].get("constraints", []))


@then("el focus contiene GATE DE AUTO-CONSULTA")
def _assert_focus_gate(gate_ctx: dict):
    assert "GATE DE AUTO-CONSULTA" in gate_ctx["constraints"], gate_ctx["constraints"]


@then("el focus contiene search_memories")
def _assert_focus_search_memories(gate_ctx: dict):
    assert "search_memories" in gate_ctx["constraints"], gate_ctx["constraints"]


@then("el focus contiene ANTES de tocar cualquier archivo")
def _assert_focus_antes(gate_ctx: dict):
    assert "ANTES de tocar cualquier archivo" in gate_ctx["constraints"], gate_ctx[
        "constraints"
    ]


@when("se carga el template implement/work")
def _load_work_template(gate_ctx: dict):
    from features.main.prompts.loader import TemplateLoader

    with tempfile.TemporaryDirectory() as tmpdir:
        gate_ctx["template"] = TemplateLoader(tmpdir).load("implement/work")


@then("el prompt contiene GATE DE AUTO-CONSULTA DE CONVENCIONES CKG")
def _assert_template_gate(gate_ctx: dict):
    tmpl = gate_ctx["template"]
    assert "GATE DE AUTO-CONSULTA DE CONVENCIONES CKG" in tmpl, tmpl[:200]


@then("el prompt contiene walker <project>/memory_schema")
def _assert_template_memory_schema(gate_ctx: dict):
    tmpl = gate_ctx["template"]
    assert "walker <project>/memory_schema" in tmpl, tmpl[:200]


@then("el prompt contiene search_memories")
def _assert_template_search_memories(gate_ctx: dict):
    tmpl = gate_ctx["template"]
    assert "search_memories" in tmpl, tmpl[:200]


@then('el prompt contiene "ckg" y "type" y "convention" y "scope"')
def _assert_template_query_keywords(gate_ctx: dict):
    tmpl = gate_ctx["template"]
    assert '"ckg"' in tmpl, tmpl[:200]
    assert "type" in tmpl, tmpl[:200]
    assert "convention" in tmpl, tmpl[:200]
    assert "scope" in tmpl, tmpl[:200]


@then("el gate aparece antes de CALIDAD DEL CODIGO")
def _assert_gate_before_quality(gate_ctx: dict):
    tmpl = gate_ctx["template"]
    gate_idx = tmpl.index("GATE DE AUTO-CONSULTA")
    quality_idx = tmpl.index("CALIDAD DEL CODIGO")
    assert gate_idx < quality_idx, "el gate debe estar ANTES de la sección de calidad"


@when("se renderiza con datos de paso")
def _render_template(gate_ctx: dict):
    from features.main.prompts.renderer import TemplateRenderer

    tmpl = gate_ctx["template"]
    data = {
        "step_quality_summary": "CPG indexado; cyclo falla",
        "step_quality_passed": "False",
        "step_failed_gates": "cyclomatic_complexity",
        "step_recommend": "refactorizar_primero",
    }
    r = TemplateRenderer()
    gate_ctx["rendered"] = r.render(tmpl, data)


@then("el render no deja placeholders sin resolver")
def _assert_no_broken_placeholders(gate_ctx: dict):
    assert "{step_" not in gate_ctx["rendered"], "render vacío deja placeholders rotos"


@then("el render mantiene el GATE DE AUTO-CONSULTA")
def _assert_rendered_keeps_gate(gate_ctx: dict):
    assert "GATE DE AUTO-CONSULTA DE CONVENCIONES CKG" in gate_ctx["rendered"]


@given("un proyecto temporal con un CKG minimo")
def _setup_temp_project(gate_ctx: dict, tmp_path):
    project_root = str(tmp_path / "proj")
    os.makedirs(os.path.join(project_root, "data"), exist_ok=True)
    chain_dir = os.path.join(project_root, "data", "spring", "test-sprint")
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
    _write_ckg_min(
        os.path.join(project_root, "data", "ckg.json.gz"),
        [
            {
                "_id": "conv-1",
                "type": "convention",
                "statement": "DDD vertical slice",
                "scope": "architecture",
            },
            {
                "_id": "dec-1",
                "type": "decision",
                "statement": "Use TEA pattern",
                "scope": "global",
            },
        ],
    )
    gate_ctx["project_root"] = project_root
    gate_ctx["chain_dir"] = chain_dir


@when("se compone el contexto para WORK con ContextComposer")
def _compose_work_context(gate_ctx: dict):
    from shared.core.engine.context.composer import ContextComposer
    from app.container import AppContainer

    registry = AppContainer.build().workflow._context_registry
    composer = ContextComposer(registry)
    node = _load(NODE_PATH)
    work = _work_substate(node)
    node_config = work.get("context", {})
    ctx = SimpleNamespace(
        chain_dir=gate_ctx["chain_dir"],
        task_id="TASK-001",
        baggage={"sprint_goal": "Migrar modulo"},
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        from features.main.prompts.loader import TemplateLoader

        template = TemplateLoader(tmpdir).load("implement/work")
    gate_ctx["result"] = composer.compose(node_config, template, ctx)


@then("el system_message contiene la instruccion de la capa ckg")
def _assert_system_has_ckg(gate_ctx: dict):
    assert "ckg" in (gate_ctx["result"].system_message or "").lower(), gate_ctx[
        "result"
    ].system_message[:300]


@then("la capa ckg contiene convenciones consultadas")
def _assert_ckg_layer_has_conventions(gate_ctx: dict):
    layers = "".join(gate_ctx["result"].context_layers)
    assert "ckg" in layers.lower() and "convention" in layers.lower(), layers[:300]


@then("el prompt renderizado mantiene el gate")
def _assert_result_user_has_gate(gate_ctx: dict):
    user = gate_ctx["result"].user_message or ""
    assert "GATE DE AUTO-CONSULTA DE CONVENCIONES CKG" in user, user[:300]


@given(
    "que el nodo DESIGN esta definido en core/engine/workflow/nodes/design.node.json"
)
def _load_design_node(gate_ctx: dict):
    gate_ctx["design_node"] = _load(DESIGN_NODE_PATH)


@when("se inspecciona el sub-estado DESIGN_GATHER del nodo DESIGN")
def _inspect_design_gather(gate_ctx: dict):
    gather = _design_gather(gate_ctx["design_node"])
    gate_ctx["gather"] = gather
    gate_ctx["providers"] = [
        s.get("provider") for s in gather.get("context", {}).get("sources", [])
    ]


@then("sus fuentes incluyen business, ckg, cpg y delta")
def _assert_design_gather_providers(gate_ctx: dict):
    for expected in ("business", "ckg", "cpg", "delta"):
        assert expected in gate_ctx["providers"], (
            f"DESIGN_GATHER no inyecta '{expected}'. Providers: {gate_ctx['providers']}"
        )


@when("se carga el template design/design-gather")
def _load_gather_template(gate_ctx: dict):
    from features.main.prompts.loader import TemplateLoader

    with tempfile.TemporaryDirectory() as tmpdir:
        gate_ctx["gather_template"] = TemplateLoader(tmpdir).load(
            "design/design-gather"
        )


@then("el prompt describe el gate y las capas")
def _assert_gather_describes_gate_and_layers(gate_ctx: dict):
    tmpl = gate_ctx["gather_template"]
    assert "GATE DE AUTO-CONSULTA" in tmpl, tmpl[:200]
    for layer in ("<< ckg >>", "<< cpg >>", "<< delta >>"):
        assert layer in tmpl, f"prompt no describe capa {layer}"
    assert "memory_schema" in tmpl and "search_memories" in tmpl, tmpl[:200]


@then("el prompt describe << ckg >>, << cpg >> y << delta >>")
def _assert_gather_layers_y(gate_ctx: dict):
    tmpl = gate_ctx["gather_template"]
    for layer in ("<< ckg >>", "<< cpg >>", "<< delta >>"):
        assert layer in tmpl, f"prompt no describe capa {layer}"


@then("el prompt contiene memory_schema y search_memories")
def _assert_gather_memory_schema_and_multi(gate_ctx: dict):
    tmpl = gate_ctx["gather_template"]
    assert "memory_schema" in tmpl and "search_memories" in tmpl, tmpl[:200]


@then("el prompt contiene GATE DE AUTO-CONSULTA")
def _assert_gather_template_gate(gate_ctx: dict):
    tmpl = gate_ctx["gather_template"]
    assert "GATE DE AUTO-CONSULTA" in tmpl, tmpl[:200]


@when("se compone el contexto para DESIGN_GATHER con ContextComposer")
def _compose_design_gather_context(gate_ctx: dict):
    from shared.core.engine.context.composer import ContextComposer
    from app.container import AppContainer

    registry = AppContainer.build().workflow._context_registry
    composer = ContextComposer(registry)
    gather = _design_gather(gate_ctx["design_node"])
    ctx = SimpleNamespace(
        chain_dir=gate_ctx["chain_dir"],
        task_id="TASK-001",
        baggage={"sprint_goal": "Diseñar feature"},
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        from features.main.prompts.loader import TemplateLoader

        template = TemplateLoader(tmpdir).load("design/design-gather")
    gate_ctx["design_result"] = composer.compose(
        gather.get("context", {}), template, ctx
    )


@then("las capas renderizadas incluyen ckg")
def _assert_design_layers_include_ckg(gate_ctx: dict):
    layers = "".join(gate_ctx["design_result"].context_layers)
    assert "ckg" in layers.lower() and "convention" in layers.lower(), layers[:300]


@then("el prompt renderizado mantiene el GATE DE AUTO-CONSULTA")
def _assert_design_result_user_has_gate(gate_ctx: dict):
    if "design_result" in gate_ctx:
        user = gate_ctx["design_result"].user_message or ""
    else:
        user = gate_ctx["result"].user_message or ""
    assert "GATE DE AUTO-CONSULTA" in user, user[:300]
