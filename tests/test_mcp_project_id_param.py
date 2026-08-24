"""
tests/test_mcp_project_id_param.py — project_id descubrible y ruteable.

FIX multi-proyecto: el dispatch SIEMPRE leyó args.project_id
(_resolve_project_id, caminos A y B) pero el catálogo no lo DECLARABA —
un agente MCP no puede enviar un parámetro que no ve (lección del fix
build_code_index: parámetro no descubrible = parámetro muerto). Resultado:
todas las tools respondían ancladas al cwd del server aunque el agente
estuviera trabajando en otro proyecto.

Congela:

  1. TODAS las tools del catálogo declaran `project_id` opcional.
  2. El routing Camino A honra el project_id enviado (status → rnf).
  3. El routing Camino B ya lo hacía (regresión: read_code → rnf).
  4. project_id nunca es required (compatibilidad con flujos cwd-anchored).
"""
import pytest


@pytest.fixture(scope="module")
def catalog():
    from features.main.container import AppContainer

    AppContainer.build()
    from features.main.cli_interface.mcp_adapter import _get_tool_catalog

    return _get_tool_catalog()


def test_todas_las_tools_declaran_project_id(catalog):
    assert len(catalog) >= 25, f"catálogo sospechosamente chico: {len(catalog)}"
    sin_pid = [
        t["name"]
        for t in catalog
        if "project_id" not in ((t.get("inputSchema") or {}).get("properties") or {})
    ]
    assert sin_pid == [], f"tools sin project_id descubrible: {sin_pid}"


def test_project_id_nunca_es_required(catalog):
    for t in catalog:
        required = (t.get("inputSchema") or {}).get("required") or []
        assert "project_id" not in required, t["name"]


def test_camino_a_honra_project_id():
    """status({project_id: rnf}) debe reportar rnf, NO el cwd del server."""
    from features.main.cli_interface.mcp_adapter import _call_tool

    out = _call_tool("status", {"project_id": "react-native-frontend"})
    data = out.get("data") or {}
    assert data.get("project_id") == "react-native-frontend", (
        f"Camino A ignoró el project_id: {data.get('project_id')}"
    )


def test_camino_b_honra_project_id_regresion():
    from features.main.cli_interface.mcp_adapter import _call_tool

    out = _call_tool(
        "read_code",
        {
            "file": "package.json",
            "project_id": "react-native-frontend",
        },
    )
    result = (out.get("data") or {}).get("result") or {}
    content = result.get("content", "")
    assert '"name"' in content and "expo" in content, (
        "read_code no resolvió el package.json de react-native-frontend"
    )
