"""
Test: GATE DE AUTO-CONSULTA DE CONVENCIONES CKG en IMPLEMENT.WORK.

Paso 2 del plan acordado (4 pasos): antes de tocar módulos core, el agente
debe consultar automáticamente las convenciones del CKG del scope afectado.

Valida:
  1. El sub-estado WORK del nodo implement declara el provider ckg (convenciones)
     y un focus con la constraint de auto-consulta.
  2. El prompt implement/work ordena explícitamente consultar el CKG antes de
     modificar código (kg_schema + kg_multi con filtros ckg scope).
  3. End-to-end: ContextComposer con el nodo real + QueryManager resuelve la
     capa ckg y el prompt renderizado contiene la sección GATE (sin placeholders
     rotos).
"""

import json
import os
import tempfile
from types import SimpleNamespace

PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", ".cognitive", "projects", "walker", "data")
)
NODE_PATH = os.path.join(PROJECT_ROOT, "workflow.v3", "nodes", "implement.node.json")
PROMPT_PATH = os.path.join(PROJECT_ROOT, "workflow.v3", "prompts", "implement", "work.prompt.json")


def _load_node() -> dict:
    with open(NODE_PATH) as f:
        return json.load(f)


def _load_prompt() -> dict:
    with open(PROMPT_PATH) as f:
        return json.load(f)


def _work_substate(node: dict) -> dict:
    for ss in node.get("sub_states", []):
        if ss.get("key") == "WORK":
            return ss
    raise AssertionError("sub-estado WORK no encontrado en implement.node.json")


# ════════════════════════════════════════════════════════════════════
#  Gate en DESIGN.DESIGN_GATHER (extensión del paso 2)
# ════════════════════════════════════════════════════════════════════

DESIGN_NODE_PATH = os.path.join(PROJECT_ROOT, "workflow.v3", "nodes", "design.node.json")
GATHER_PROMPT_PATH = os.path.join(PROJECT_ROOT, "workflow.v3", "prompts", "design", "design-gather.prompt.json")


def _design_gather(node: dict) -> dict:
    for ss in node.get("sub_states", []):
        if ss.get("key") == "DESIGN_GATHER":
            return ss
    raise AssertionError("sub-estado DESIGN_GATHER no encontrado en design.node.json")


def test_design_gather_declara_capas_conocimiento():
    """DESIGN_GATHER debe inyectar business + ckg + cpg + delta."""
    node = _load_node_design()
    gather = _design_gather(node)
    providers = [s.get("provider") for s in gather.get("context", {}).get("sources", [])]
    for expected in ("business", "ckg", "cpg", "delta"):
        assert expected in providers, f"DESIGN_GATHER no inyecta '{expected}'. Providers: {providers}"
    print("  ✅ test_design_gather_declara_capas_conocimiento")


def test_design_gather_prompt_menciona_gate_y_capas():
    """El prompt de DESIGN_GATHER debe describir el gate de auto-consulta."""
    with open(GATHER_PROMPT_PATH) as f:
        tmpl = json.load(f)["template"]
    assert "GATE DE AUTO-CONSULTA" in tmpl
    for layer in ("<< ckg >>", "<< cpg >>", "<< delta >>"):
        assert layer in tmpl, f"prompt no describe capa {layer}"
    assert "kg_schema" in tmpl and "kg_multi" in tmpl
    print("  ✅ test_design_gather_prompt_menciona_gate_y_capas")


def _load_node_design() -> dict:
    with open(DESIGN_NODE_PATH) as f:
        return json.load(f)


def test_composer_inyecta_capas_en_design_gather():
    """End-to-end: DESIGN_GATHER inyecta ckg/cpg/delta en proyecto temporal."""
    from features.engine.bootstrap import build_context_registry
    from features.engine.context.composer import ContextComposer

    with tempfile.TemporaryDirectory() as tmpdir:
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
        _write_ckg_min(os.path.join(project_root, "data", "ckg.json.gz"), [
            {"_id": "conv-1", "type": "convention", "statement": "DDD vertical slice", "scope": "architecture"},
        ])

        registry = build_context_registry()
        composer = ContextComposer(registry)
        node = _load_node_design()
        gather = _design_gather(node)
        with open(GATHER_PROMPT_PATH) as f:
            template = json.load(f)["template"]
        ctx = SimpleNamespace(
            chain_dir=chain_dir,
            task_id="TASK-001",
            baggage={"sprint_goal": "Diseñar feature"},
        )
        result = composer.compose(gather.get("context", {}), template, ctx)
        layers = "".join(result.context_layers)
        # ckg con convenciones reales del proyecto temporal
        assert "ckg" in layers.lower() and "convention" in layers.lower(), layers[:300]
        # el gate se mantiene en el prompt renderizado
        assert "GATE DE AUTO-CONSULTA" in (result.user_message or "")
        print("  ✅ test_composer_inyecta_capas_en_design_gather")


def test_work_node_declara_provider_ckg_convenciones():
    """WORK debe inyectar la capa ckg (query type:convention) para conocer convenciones."""
    work = _work_substate(_load_node())
    sources = work.get("context", {}).get("sources", [])
    ckg_sources = [s for s in sources if s.get("provider") == "ckg"]
    assert ckg_sources, f"WORK no declara provider ckg. Sources: {[s.get('provider') for s in sources]}"
    query = ckg_sources[0].get("config", {}).get("query", {})
    assert query.get("type") == "convention", f"query ckg debe pedir type:convention: {query}"
    print("  ✅ test_work_node_declara_provider_ckg_convenciones")


def test_work_focus_ordena_auto_consulta():
    """El focus de WORK debe contener la constraint de auto-consulta CKG."""
    work = _work_substate(_load_node())
    focus = work.get("context", {}).get("focus", {})
    constraints = " ".join(focus.get("constraints", []))
    assert "GATE DE AUTO-CONSULTA" in constraints, constraints
    assert "kg_multi" in constraints, constraints
    assert "ANTES de tocar cualquier archivo" in constraints, constraints
    print("  ✅ test_work_focus_ordena_auto_consulta")


def test_work_prompt_ordena_auto_consulta_ckg():
    """El prompt implement/work debe ordenar consultar CKG antes de codificar."""
    tmpl = _load_prompt()["template"]
    assert "GATE DE AUTO-CONSULTA DE CONVENCIONES CKG" in tmpl
    assert "walker <project>/kg_schema" in tmpl
    assert 'kg_multi' in tmpl
    assert '"ckg"' in tmpl
    assert "type" in tmpl and "convention" in tmpl
    assert "scope" in tmpl
    assert tmpl.index("GATE DE AUTO-CONSULTA") < tmpl.index("CALIDAD DEL CODIGO"), (
        "el gate debe estar ANTES de la sección de calidad"
    )
    print("  ✅ test_work_prompt_ordena_auto_consulta_ckg")


def test_work_prompt_no_rompe_placeholders_existentes():
    """El template de WORK mantiene los placeholders de step sin romper el render."""
    from features.prompts.renderer import TemplateRenderer

    tmpl = _load_prompt()["template"]
    data = {
        "step_quality_summary": "CPG indexado; cyclo falla",
        "step_quality_passed": "False",
        "step_failed_gates": "cyclomatic_complexity",
        "step_recommend": "refactorizar_primero",
    }
    r = TemplateRenderer()
    rendered = r.render(tmpl, data)
    assert "{step_" not in rendered, "render vacío deja placeholders rotos"
    assert "GATE DE AUTO-CONSULTA DE CONVENCIONES CKG" in rendered
    print("  ✅ test_work_prompt_no_rompe_placeholders_existentes")


def test_composer_inyecta_capa_ckg_en_work():
    """End-to-end: ContextComposer + nodo WORK real + QueryManager produce la capa ckg.

    Se usa un proyecto temporal con CKG mínimo para no depender del estado real
    del walker (el test debe pasar sin datos reales del proyecto).
    """
    from features.engine.bootstrap import build_context_registry
    from features.engine.context.composer import ContextComposer

    with tempfile.TemporaryDirectory() as tmpdir:
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

        # CKG mínimo en el proyecto temporal
        ckg_path = os.path.join(project_root, "data", "ckg.json.gz")
        _write_ckg_min(ckg_path, [
            {"_id": "conv-1", "type": "convention", "statement": "DDD vertical slice", "scope": "architecture"},
            {"_id": "dec-1", "type": "decision", "statement": "Use TEA pattern", "scope": "global"},
        ])

        registry = build_context_registry()
        composer = ContextComposer(registry)
        node = _load_node()
        work = _work_substate(node)
        node_config = work.get("context", {})
        ctx = SimpleNamespace(
            chain_dir=chain_dir,
            task_id="TASK-001",
            baggage={"sprint_goal": "Migrar módulo"},
        )

        result = composer.compose(node_config, _load_prompt()["template"], ctx)
        system = result.system_message or ""
        layers = "".join(result.context_layers)
        user = result.user_message or ""

        # El system_message debe incluir la instrucción de la capa ckg
        assert "ckg" in system.lower(), f"capa ckg no en system_message: {system[:300]}"
        # La capa ckg debe tener contenido (convenciones consultadas)
        assert "ckg" in layers.lower() and "convention" in layers.lower(), f"capa ckg vacía: {layers[:300]}"
        # El prompt renderizado mantiene el gate
        assert "GATE DE AUTO-CONSULTA DE CONVENCIONES CKG" in user
        print("  ✅ test_composer_inyecta_capa_ckg_en_work")


def _write_ckg_min(path: str, docs: list[dict]) -> None:
    """Escribe un CKG mínimo en formato nativo (index/docs/edges)."""
    import gzip

    payload = {
        "index": "ckg",
        "docs": docs,
        "edges": [],
    }
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
