"""
Test: Centralización de prompts — SSOT en features/prompts/templates/.

Valida:
  1. Los protocolos (MCP y REST) viven como templates externos en
     features/prompts/templates/protocol/ (root único con subpaths).
  2. _PROTOCOL_TEXT (mcp_adapter) y el protocol de _build_help (handlers)
     SE SIRVEN desde esos templates (sin duplicar contenido en el código).
  3. TemplateLoader resuelve el root canónico como primera fuente.
  4. PromptManager.available_templates lista el root canónico con subpaths.
"""

import os
import tempfile


def _template_path(*parts: str) -> str:
    from features.prompts.loader import canonical_templates_dir

    return os.path.join(canonical_templates_dir(), *parts)


def _read_template(*parts: str) -> str:
    with open(_template_path(*parts)) as f:
        return f.read()


# ── 1. Templates externos existentes ──────────────────────────────────────────


def test_protocol_templates_existen():
    assert os.path.exists(_template_path("protocol", "mcp.md")), "mcp.md ausente"
    assert os.path.exists(_template_path("protocol", "rest.md")), "rest.md ausente"
    mcp = _read_template("protocol", "mcp.md")
    rest = _read_template("protocol", "rest.md")
    assert "MANDATORY BEHAVIOR CONTRACT" in mcp
    assert "capture_task" in mcp
    assert "PROTOCOLO REST OBLIGATORIO" in rest
    assert "submit" in rest and "transition" in rest
    print(
        "  ✅ test_protocol_templates_existen: templates de protocolo en el root único"
    )


def test_gate_briefing_en_subpath():
    assert os.path.exists(_template_path("gates", "gate-briefing.md")), (
        "gate-briefing.md debe vivir en templates/gates/"
    )
    assert "QUÉ SUCEDIÓ" in _read_template("gates", "gate-briefing.md")
    print("  ✅ test_gate_briefing_en_subpath: gate briefing en subpath gates/")


# ── 2. SSOT: el código se sirve desde los templates ───────────────────────────


def test_mcp_protocol_text_es_el_template():
    """_PROTOCOL_TEXT debe ser EL contenido del template, no una copia en código."""
    from features.cli_interface.mcp_adapter import _PROTOCOL_TEXT

    assert _PROTOCOL_TEXT == _read_template("protocol", "mcp.md"), (
        "_PROTOCOL_TEXT diverge del template SSOT features/prompts/templates/protocol/mcp.md"
    )
    print("  ✅ test_mcp_protocol_text_es_el_template: SSOT MCP = template externo")


def test_rest_protocol_es_el_template():
    """El protocol de _build_help debe ser EL contenido del template, no una copia."""
    from features.cli_interface.application.handlers import _build_help

    help_data = _build_help()
    assert help_data.get("protocol") == _read_template("protocol", "rest.md"), (
        "protocol de _build_help diverge del template SSOT features/prompts/templates/protocol/rest.md"
    )
    print("  ✅ test_rest_protocol_es_el_template: SSOT REST = template externo")


def test_fallbacks_activos_si_template_falta(monkeypatch):
    """Si el template no existe, los fallbacks del código mantienen el servicio."""
    from features.prompts import loader as loader_mod

    def _boom(template_id: str):
        raise loader_mod.TemplateNotFound(template_id)

    monkeypatch.setattr(loader_mod, "load_static", _boom)

    from features.cli_interface import mcp_adapter as mcp_mod

    assert mcp_mod._load_protocol_text() == mcp_mod._PROTOCOL_FALLBACK

    from features.cli_interface.application import handlers as handlers_mod

    assert handlers_mod._load_protocol_rest() == handlers_mod._PROTOCOL_REST_FALLBACK
    print("  ✅ test_fallbacks_activos_si_template_falta: fallbacks operativos")


# ── 3. TemplateLoader resuelve el root canónico primero ───────────────────────


def test_loader_resuelve_root_canonico():
    """TemplateLoader debe resolver protocol/mcp desde features/prompts/templates
    incluso sin un proyecto real (root canónico es la primera fuente)."""
    from features.prompts.loader import TemplateLoader

    with tempfile.TemporaryDirectory() as tmpdir:
        loader = TemplateLoader(os.path.join(tmpdir, "chain"))
        text = loader.load("protocol/mcp")
        assert "WALKER PROTOCOL (MCP)" in text
        assert "capture_task" in text
    print("  ✅ test_loader_resuelve_root_canonico: loader prioriza el root único")


def test_loader_resuelve_gate_briefing():
    from features.prompts.loader import TemplateLoader

    with tempfile.TemporaryDirectory() as tmpdir:
        loader = TemplateLoader(os.path.join(tmpdir, "chain"))
        text = loader.load("gates/gate-briefing")
        assert "QUÉ SUCEDIÓ" in text
    print("  ✅ test_loader_resuelve_gate_briefing: loader resuelve subpath gates/")


# ── 5. Prompts de nodo del motor en templates/nodes/ ──────────────────────────


def test_prompts_de_nodo_viven_en_templates_nodes():
    """Los 47 prompts de nodo del motor deben estar en el root único
    features/prompts/templates/nodes/ (organizado por subpath de nodo)."""
    nodes_dir = _template_path("nodes")
    assert os.path.isdir(nodes_dir), f"templates/nodes/ ausente: {nodes_dir}"
    count = 0
    for _dirpath, _dirs, files in os.walk(nodes_dir):
        count += sum(1 for f in files if f.endswith(".prompt.json"))
    assert count == 47, f"esperaba 47 prompts de nodo, hay {count}"
    # Sin duplicados: la plantilla del motor (features/engine/workflow) no
    # debe conservar copias de prompts (SSOT único en templates/nodes/), y la
    # data del motor ya no existe como contenedor de maquinaria del engine.
    assert not os.path.exists(
        os.path.join("features", "engine", "workflow", "prompts")
    ), "features/engine/workflow no debe conservar copias de prompts (SSOT único)"
    assert not os.path.exists(".cognitive/projects/walker/data/workflow.v3"), (
        "la data del motor no debe contener workflow.v3 (maquinaria del engine = código)"
    )
    print(
        "  ✅ test_prompts_de_nodo_viven_en_templates_nodes: 47 prompts en templates/nodes/"
    )


def test_loader_resuelve_prompts_de_nodo():
    """TemplateLoader debe resolver IDs de nodo (sprint/task-review, implement/work)
    desde features/prompts/templates/nodes/ sin proyecto real."""
    from features.prompts.loader import TemplateLoader

    with tempfile.TemporaryDirectory() as tmpdir:
        loader = TemplateLoader(os.path.join(tmpdir, "chain"))
        task_review = loader.load("sprint/task-review")
        assert "REVISION DE ESPECIFICACION DE TAREA" in task_review
        work = loader.load("implement/work")
        assert "{step_quality_summary}" in work
        diagnose = loader.load("refine/diagnose")
        assert "{step_recommend}" in diagnose
    print(
        "  ✅ test_loader_resuelve_prompts_de_nodo: IDs de nodo desde templates/nodes/"
    )


def test_available_templates_incluye_prompts_de_nodo():
    from features.prompts.manager import PromptManager

    with tempfile.TemporaryDirectory() as tmpdir:
        pm = PromptManager(os.path.join(tmpdir, "chain"))
        templates = pm.available_templates()
        assert "nodes/sprint/task-review.prompt.json" in templates, templates
        assert "nodes/implement/work.prompt.json" in templates, templates
        assert len(templates) == 50, (
            f"50 templates esperados (47 nodo + 3 feature), hay {len(templates)}"
        )
    print(
        "  ✅ test_available_templates_incluye_prompts_de_nodo: 50 templates en el root único"
    )


# ── 6. Plantilla del workflow del motor = CÓDIGO, no data ─────────────────────


def test_engine_workflow_es_codigo_no_data():
    """get_engine_workflow_dir() debe apuntar a features/engine/workflow (código
    del repo), NUNCA a .cognitive/projects/<pid>/data/ (config por proyecto)."""
    from libs.path_resolver import get_engine_workflow_dir, get_engine_workflow_path

    wf_dir = str(get_engine_workflow_dir())
    assert wf_dir.endswith(os.path.join("features", "engine", "workflow")), wf_dir
    assert ".cognitive" not in wf_dir, (
        f"el engine workflow no debe vivir en data: {wf_dir}"
    )
    assert os.path.exists(wf_dir), f"engine workflow ausente en código: {wf_dir}"
    assert os.path.exists(str(get_engine_workflow_path()))
    assert os.path.exists(os.path.join(wf_dir, "nodes", "implement.node.json"))
    print(
        "  ✅ test_engine_workflow_es_codigo_no_data: plantilla del motor en features/engine/workflow/"
    )


# ── 7. PromptManager.available_templates ──────────────────────────────────────


def test_available_templates_lista_subpaths():
    from features.prompts.manager import PromptManager

    with tempfile.TemporaryDirectory() as tmpdir:
        pm = PromptManager(os.path.join(tmpdir, "chain"))
        templates = pm.available_templates()
        assert "protocol/mcp.md" in templates, templates
        assert "protocol/rest.md" in templates, templates
        assert "gates/gate-briefing.md" in templates, templates
    print(
        "  ✅ test_available_templates_lista_subpaths: root canónico listado con subpaths"
    )
