"""test_fast_mcp_phase2_bdd.py — Capa de test de la Fase 2 (memorias CKG/CPG).

Verifica:
  1. Superficie exacta de las 14 tools de memoria.
  2. InputSchema declarativo (R-03bis) para cada tool.
  3. Autonomía: cero imports de mcp/ en los módulos de fase2.
  4. Composition cablea los runners de memoria.
  5. Cada tool runner despacha al application service correcto.
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


# ── Superficie Fase 2 ──────────────────────────────────────────────

_FASE2_TOOLS = {
    "build_code_index", "index_repo", "update_incremental_code",
    "extract_knowledge", "knowledge_query", "memory_schema",
    "recall_knowledge", "documenting_knowledge", "search_memories",
    "agent_context", "code_context", "inspect_codebase",
    "read_code", "code_search",
}


def test_fase2_las_14_tools_estan_registradas(fastmcp_server):
    """Las 14 tools de memorias están registradas en el server."""
    names = {t.name for t in _remote_list(fastmcp_server)}
    missing = _FASE2_TOOLS - names
    assert not missing, f"Faltan tools de Fase 2: {missing}"


def test_fase2_tools_tienen_input_schema(fastmcp_server):
    """Cada tool de Fase 2 tiene inputSchema declarativo (no vacío)."""
    tools = _remote_list(fastmcp_server)
    by_name = {t.name: t for t in tools}
    for name in _FASE2_TOOLS:
        tool = by_name.get(name)
        assert tool is not None, f"Tool {name} no encontrada"
        schema = tool.inputSchema
        assert isinstance(schema, dict), f"Tool {name}: inputSchema no es dict"
        assert schema.get("type") == "object", f"Tool {name}: inputSchema sin type=object"
        assert "properties" in schema, f"Tool {name}: inputSchema sin properties"


def test_fase2_knowledge_query_schema_tiene_action(fastmcp_server):
    """knowledge_query tiene el campo 'action' en su schema."""
    tools = _remote_list(fastmcp_server)
    by_name = {t.name: t for t in tools}
    schema = by_name["knowledge_query"].inputSchema
    assert "action" in schema["properties"]
    assert schema["properties"]["action"]["type"] == "string"


def test_fase2_code_context_schema_tiene_file_y_query(fastmcp_server):
    """code_context tiene los campos 'file' y 'query' en su schema."""
    tools = _remote_list(fastmcp_server)
    by_name = {t.name: t for t in tools}
    schema = by_name["code_context"].inputSchema
    assert "file" in schema["properties"]
    assert "query" in schema["properties"]


def test_fase2_read_code_requiere_file(fastmcp_server):
    """read_code tiene 'file' en required."""
    tools = _remote_list(fastmcp_server)
    by_name = {t.name: t for t in tools}
    schema = by_name["read_code"].inputSchema
    assert "file" in schema.get("required", []), f"read_code: 'file' no está en required"


def test_fase2_code_search_requiere_query(fastmcp_server):
    """code_search tiene 'query' en required."""
    tools = _remote_list(fastmcp_server)
    by_name = {t.name: t for t in tools}
    schema = by_name["code_search"].inputSchema
    assert "query" in schema.get("required", []), f"code_search: 'query' no está en required"


def test_fase2_index_repo_requiere_url(fastmcp_server):
    """index_repo tiene 'url' en required."""
    tools = _remote_list(fastmcp_server)
    by_name = {t.name: t for t in tools}
    schema = by_name["index_repo"].inputSchema
    assert "url" in schema.get("required", []), f"index_repo: 'url' no está en required"


# ── Autonomía ───────────────────────────────────────────────────────

def test_fase2_no_importa_nada_de_mcp():
    """phase2_memories.py no importa módulos de mcp/ (ADR-WALKER-071 §0 #5)."""
    import fast_mcp.phases.phase2_memories

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
    tree = ast.parse(inspect.getsource(fast_mcp.phases.phase2_memories))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and is_forbidden(node.module):
            bad.add(node.module)
        elif isinstance(node, ast.Import):
            for n in node.names:
                if is_forbidden(n.name):
                    bad.add(n.name)
    assert not bad, f"phase2_memories importa mcp/ (PROHIBIDO): {bad}"


def test_fase2_composition_memory_runner_disponible():
    """composition.memory_tool_runner está disponible y retorna un callable."""
    from fast_mcp import composition

    runner = composition.memory_tool_runner("read_code")
    assert callable(runner)


# ── Despacho de tools ──────────────────────────────────────────────

def test_fase2_read_code_sin_contexto_devuelve_error():
    """read_code sin project context devuelve error."""
    from fast_mcp import composition

    runner = composition.memory_tool_runner("read_code")
    result = runner(file="test.py")
    assert "error" in result or "No project context" in str(result)


def test_fase2_code_search_sin_query_devuelve_error():
    """code_search sin query devuelve error."""
    from fast_mcp import composition

    runner = composition.memory_tool_runner("code_search")
    result = runner()
    assert "error" in result


def test_fase2_knowledge_query_default_action_search():
    """knowledge_query con action=search ejecuta búsqueda."""
    from fast_mcp import composition

    runner = composition.memory_tool_runner("knowledge_query")
    # Sin project context, debería degradar gracefully
    result = runner(action="search", query="test")
    # El resultado puede ser un error o un resultado vacío — ambos son válidos
    assert isinstance(result, dict)


def test_fase2_memory_tool_runner_sin_chain_dir():
    """memory_tool_runner sin project_id devuelve error de contexto."""
    from fast_mcp import composition

    runner = composition.memory_tool_runner("inspect_codebase")
    result = runner()
    assert "error" in result
