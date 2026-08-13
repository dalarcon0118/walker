"""tests/test_call_tool_dispatch.py — Dos caminos de dispatch de _call_tool (D2).

Fija el contrato de la bifurcación route-vs-fallback documentada en
mcp_adapter._call_tool (capa_rout.md D2, ARQ objetivo):
  - Camino A: tool con route_def en el ModuleRegistry → path/flags desde la
    metadata del route → _ROUTER.dispatch (ej. "list" → handle_list_projects).
  - Camino B: fallback de task pública dinámica → path "{project_id}/{name}"
    → el Router resuelve ":projectId/:taskName" → handle_task →
    execute_public_task. Nunca lanza: devuelve un dict JSON-safe (error si la
    task no existe).
"""
from __future__ import annotations


def test_call_tool_camino_a_route_list():
    """Tool con route_def (list) llega a handle_list_projects por la metadata del route."""
    from features.main.cli_interface.mcp_adapter import _call_tool

    result = _call_tool("list", {})
    assert isinstance(result, dict)
    # handle_list_projects responde projects_list (no el error de task).
    assert result.get("type") == "projects_list", result
    assert isinstance(result.get("data", {}).get("projects"), list)


def test_call_tool_camino_b_fallback_task():
    """Tool sin route_def cae al fallback de task pública y NUNCA lanza.

    Hermético: project_id explícito "fake" (sin resolución desde CWD — evita
    que _ensure_chain_dir/_sync_watcher toquen proyectos reales).
    """
    from features.main.cli_interface.mcp_adapter import _call_tool

    # "project_id" (no _project_id) es la clave que _resolve_project_id lee;
    # la task no existe → el fallback devuelve el error estándar del registry.
    result = _call_tool("task_inexistente_xyz", {"project_id": "fake"})
    assert isinstance(result, dict)
    assert result.get("type") == "error" or result.get("status_code", 0) >= 400, result
