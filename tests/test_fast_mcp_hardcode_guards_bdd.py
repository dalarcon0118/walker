"""tests/test_fast_mcp_hardcode_guards_bdd.py — Guardas anti-hardcode (PHASE 9 VALIDATION).

Enforce del compromiso G1 (modelo rule.md aplicado al propio proyecto):

  G1. Los literales de ExecutionType ("llm_task", "manual", "auto",
      "user_input", "bootstrap") NO se duplican como string constants en el
      código del feature workflow fuera de domain/enums.py — el SSOT es
      ExecutionType (domain.enums). Docstrings y claves no-literales no
      cuentan (solo ast.Constant de valor EXACTO).
  G2. El hook .opencode/plugins/walker-session.js NO referenciaba tools
      fantasma (walker_kg_*, walker_cpg_business, walker_analyze_code,
      walker_capture_task) y las tools que cita existen en el catálogo
      real declarado en fast_mcp/phases/*.py (SSOT importado, no duplicado).
"""
import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = REPO_ROOT / "fast_mcp" / "features" / "workflow"
PLUGIN = REPO_ROOT / ".opencode" / "plugins" / "walker-session.js"

EXECUTION_VALUES = {"bootstrap", "auto", "llm_task", "user_input", "manual"}

# Tools fantasma del hook legacy — prohibidas (desinformaban cada sesión).
PHANTOM_TOOLS = {
    "walker_kg_task_context",
    "walker_kg_diagnose",
    "walker_kg_qa_evidence",
    "walker_kg_sprint_evidence",
    "walker_kg_query",
    "walker_cpg_business",
    "walker_analyze_code",
    "walker_capture_task",
}


def _real_tool_names() -> set[str]:
    """Catálogo SSOT de tools declaradas en fast_mcp/phases/*.py."""
    names: set[str] = set()
    for phase_file in (REPO_ROOT / "fast_mcp" / "phases").glob("phase*.py"):
        tree = ast.parse(phase_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, (ast.Tuple, ast.List)):
                target = node.targets[0]
                if isinstance(target, ast.Name) and target.id.endswith("_TOOLS"):
                    for elt in node.value.elts:
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                            names.add(elt.value)
    assert names, "no se extrajo el catálogo de tools — revisar fast_mcp/phases/"
    return names


class TestExecutionTypeLiteralsGuard:

    def test_no_execution_literals_outside_enum(self):
        """Los valores de ExecutionType no se duplican como constantes string."""
        offenders: list[str] = []
        for py_file in WORKFLOW_DIR.rglob("*.py"):
            if py_file.name == "enums.py":
                continue  # SSOT: definición legítima
            tree = ast.parse(py_file.read_text())
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and node.value in EXECUTION_VALUES
                ):
                    offenders.append(f"{py_file.relative_to(REPO_ROOT)}:{node.lineno} = {node.value!r}")
        assert not offenders, (
            "Literales de ExecutionType duplicados (usar domain.enums.ExecutionType):\n"
            + "\n".join(offenders)
        )


class TestPluginToolsGuard:

    def test_plugin_has_no_phantom_tools(self):
        text = PLUGIN.read_text()
        found = {tool for tool in PHANTOM_TOOLS if tool in text}
        assert not found, f"Tools fantasma en el hook (no existen en fast_mcp): {found}"

    def test_plugin_references_only_real_tools(self):
        """Todo walker_* citado por el hook debe existir en el catálogo real
        (fases 1-3 de fast_mcp) o ser el prefijo del CLI (bin/walker)."""
        text = PLUGIN.read_text()
        referenced = set(re.findall(r"walker_[a-z_]+", text))
        real = {f"walker_{name}" for name in _real_tool_names()}
        # Nombres legítimos NO registrados como tools MCP (CLI/infraestructura)
        legit = {"walker_session", "walker_mcp", "walker3", "walker_label"}
        unknown = referenced - real - legit
        assert not unknown, (
            "El hook cita tools inexistentes — corregir con el catálogo real:\n"
            + "\n".join(sorted(unknown))
        )

    def test_real_tool_catalog_is_loaded(self):
        """Sanity del SSOT: las 3 fases declaran tools."""
        names = _real_tool_names()
        assert {"submit", "transition", "status", "build_code_index"} <= names
