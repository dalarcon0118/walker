"""test_fast_mcp_migration_bdd.py — Capa de test de la migración a FastMCP.

Fase 1: EXACTAMENTE las 3 tools del workflow core (prepare/list/status),
VIVIENDO dentro de fast_mcp bajo la arquitectura §3bis (features + kernel +
composition). Sin walker_ping ni tools extra.

AUTONOMÍA (ADR-WALKER-071 §0 #5): fast_mcp NO importa NADA de `mcp/`
(ni app/, features/, shared/, protocols/) y NO pone mcp/ en sys.path.
La migración PORTA el código a fast_mcp/, no lo llama.
"""

from __future__ import annotations

import ast
import inspect

import pytest


def _remote_list(server) -> list:
    import asyncio

    return asyncio.run(server.list_tools())


@pytest.fixture(scope="module")
def fastmcp_server():
    from fast_mcp.server import build_server

    return build_server()


# ── Superficie ──────────────────────────────────────────────────────


def test_fast_mcp_paquete_importable():
    import fast_mcp
    from fast_mcp import server

    assert hasattr(server, "build_server")
    assert fast_mcp.__doc__


def test_build_server_construye_fastmcp(fastmcp_server):
    from mcp.server.fastmcp import FastMCP

    assert isinstance(fastmcp_server, FastMCP)


def test_server_expone_exactamente_las_3_tools_del_adr(fastmcp_server):
    """Las tools expuestas son EXACTAMENTE las 3 del ADR §3 Fase 1.

    Sin walker_ping ni tools extra (decisión del usuario: foco en las 3).
    """
    names = {t.name for t in _remote_list(fastmcp_server)}
    assert names == {"prepare", "list", "status"}, f"tools inesperadas: {names}"


def test_list_roundtrip(fastmcp_server):
    import asyncio

    result = asyncio.run(fastmcp_server.call_tool("list", {}))
    text = None
    if isinstance(result, dict) and "content" in result:
        blocks = result["content"]
        if blocks and hasattr(blocks[0], "text"):
            text = blocks[0].text
    assert "projects" in str(text or result), f"list sin 'projects': {result}"


# ── Autonomía TOTAL (ADR §0 #5) — cero imports de mcp/ ─────────────


def test_fast_mcp_no_importa_nada_de_mcp():
    """Ningún módulo de fast_mcp importa app./features./shared./protocols./mcp/.

    La migración PORTA el código a fast_mcp/ (§0 #4): los casos de uso viven
    aquí; el único tercero permitido es la lib oficial mcp.server.fastmcp
    (+ pydantic), nunca el monorepo mcp/.
    """
    import fast_mcp.composition
    import fast_mcp.main
    import fast_mcp.registration
    import fast_mcp.server
    import fast_mcp.shared_kernel.paths
    import fast_mcp.phases.phase1_prepare
    import fast_mcp.features.project.application.use_cases
    import fast_mcp.features.project.infrastructure.registry_store
    import fast_mcp.features.project.infrastructure.layout
    import fast_mcp.features.project.infrastructure.detection

    forbidden_prefixes = ("app", "features", "shared", "protocols", "config", "data")
    allowed = {
        "mcp.server.fastmcp",
        "mcp.server.fastmcp.utilities.func_metadata",
        "mcp.server.fastmcp.tools.base",
        "pydantic",
        "returns",
    }

    def is_forbidden(module: str) -> bool:
        root = module.split(".")[0]
        if root in allowed:
            return False
        return root in forbidden_prefixes

    bad = set()
    for mod in (
        fast_mcp.server, fast_mcp.main, fast_mcp.composition,
        fast_mcp.registration, fast_mcp.shared_kernel.paths,
        fast_mcp.phases.phase1_prepare,
        fast_mcp.features.project.application.use_cases,
        fast_mcp.features.project.infrastructure.registry_store,
        fast_mcp.features.project.infrastructure.layout,
        fast_mcp.features.project.infrastructure.detection,
    ):
        tree = ast.parse(inspect.getsource(mod))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and is_forbidden(node.module):
                bad.add(node.module)
            elif isinstance(node, ast.Import):
                for n in node.names:
                    if is_forbidden(n.name):
                        bad.add(n.name)
    assert not bad, f"fast_mcp importa mcp/ (PROHIBIDO): {bad}"


def test_fast_mcp_runtime_cero_modulos_de_mcp():
    """Tras build + uso, sys.modules NO contiene app.*/features.*/shared.*/protocols.*."""
    import sys

    before = {m for m in sys.modules if m.split(".")[0] in ("app", "features", "shared", "protocols")}
    from fast_mcp import composition
    from fast_mcp.features.project.application import use_cases

    ctx = composition.build().project
    result = use_cases.list_use_case(ctx)

    after = {m for m in sys.modules if m.split(".")[0] in ("app", "features", "shared", "protocols")}
    nuevos = after - before
    assert not nuevos, f"fast_mcp cargó módulos de mcp/ en runtime: {sorted(nuevos)}"
    assert isinstance(result, dict)


# ── Arquitectura §3bis (composition) ───────────────────────────────


def test_composition_cablea_y_root_es_inyectable(tmp_path):
    """composition.build(root=...) construye contexts con raíz propia (tests)."""
    from fast_mcp import composition

    fake_root = tmp_path / "walker-root"
    (fake_root / "config").mkdir(parents=True)
    (fake_root / "config" / "config.json").write_text("{}")

    ctx = composition.build(root=fake_root).project
    # RegistryStore resuelve contra la raíz inyectada.
    assert str(ctx.store.read()) == "{}"
    registered = ctx.store.register("demo", "demo", str(tmp_path / "demo"))
    assert registered == fake_root / "data" / "projects.json"


# ── Fase 1 — contratos de las 3 tools ──────────────────────────────


def test_fase1_input_schema_es_declarativo(fastmcp_server):
    """El inputSchema servido al agente es el declarativo de la fase, no inferido."""
    tools = _remote_list(fastmcp_server)
    by_name = {t.name: t for t in tools}
    assert by_name["prepare"].inputSchema["properties"].get("path") == {"type": "string"}
    assert by_name["status"].inputSchema["properties"].get("_project_id") == {
        "type": "string"
    }


def test_fase1_prepare_crea_y_registra(tmp_path):
    """prepare crea el layout cognitivo + project.json y registra en data/projects.json."""
    import json

    from fast_mcp import composition
    from fast_mcp.features.project.application import use_cases

    repo = tmp_path / "demo-repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("[project]\nname='demo'\n")

    fake_root = tmp_path / "walker-root"
    (fake_root / "config").mkdir(parents=True)
    (fake_root / "config" / "config.json").write_text("{}")
    ctx = composition.build(root=fake_root).project

    result = use_cases.prepare_use_case(ctx, project_path=str(repo))

    assert result["type"] == "prepare_result"
    assert result["project_id"] == "demo-repo"
    assert (repo / ".cognitive" / "projects" / "demo-repo" / "data" / "project.json").is_file()
    reg = json.loads((fake_root / "data" / "projects.json").read_text())
    assert reg["projects"]["demo-repo"]["repo_path"] == str(repo)

    # Idempotencia (mismo guard que legacy).
    again = use_cases.prepare_use_case(ctx, project_path=str(repo))
    assert again["type"] == "already_exists"


def test_fase1_status_resuelve_proyecto(tmp_path):
    """status resuelve project.json del registro y reporta health."""
    from fast_mcp import composition
    from fast_mcp.features.project.application import use_cases

    fake_root = tmp_path / "walker-root"
    (fake_root / "config").mkdir(parents=True)
    (fake_root / "config" / "config.json").write_text("{}")
    ctx = composition.build(root=fake_root).project

    repo = tmp_path / "demo-repo"
    use_cases.prepare_use_case(ctx, project_path=str(repo), args={"language": "python"})

    health = use_cases.status_use_case(ctx, {"_project_id": "demo-repo"})
    assert health["type"] == "project_health"
    assert health["checks"]["project_json"] == "ok"
    assert health["project"]["language"] == "python"
    assert health["project"]["repo_path"] == str(repo)
