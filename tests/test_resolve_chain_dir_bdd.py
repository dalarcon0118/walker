"""BDD: `_resolve_chain_dir` no cruza de dominio.

Antes, un `project_id` desconocido hacía que `resolve_paths` cayera al repo de
walker y el camino MCP leía/escribía estado del proyecto equivocado. El CLI ya
lo corregía comparando el id resuelto; el MCP no. Ahora ambos fallan cerrado.
"""

from __future__ import annotations

import types

from fast_mcp import composition


def test_id_desconocido_no_cae_al_repo_de_walker():
    assert composition._resolve_chain_dir({"project_id": "proyecto-que-no-existe-123"}) == ""


def test_id_valido_resuelve_al_repo():
    resolved = composition._resolve_chain_dir({"project_id": "walker"})
    assert resolved.endswith("/walker")


def test_sin_project_id_no_hay_contexto():
    assert composition._resolve_chain_dir({}) == ""
    assert composition._resolve_chain_dir({"project_id": ""}) == ""


def test_resolve_paths_que_devuelve_otro_id_se_rechaza(monkeypatch):
    """El caso exacto del bug: resolve_paths resuelve un id DISTINTO (walker)."""
    from fast_mcp.shared_kernel.project_support import ProjectService

    fake = types.SimpleNamespace(project_id="walker", project_root="/tmp/walker")
    monkeypatch.setattr(ProjectService, "resolve_paths", staticmethod(lambda pid: fake))

    assert composition._resolve_chain_dir({"project_id": "otro-inexistente"}) == ""


def test_runner_devuelve_error_accionable_en_vez_de_escribir():
    out = composition.task_tool_runner("implement_task")(
        project_id="proyecto-que-no-existe-123", objective="no debería persistir"
    )

    assert "error" in out
    assert "project_id" in out["error"]
    assert "proyectos registrados" in out["error"] or "registrados" in out["error"]
