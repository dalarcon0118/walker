"""test_inspect_codebase_action_bdd.py — el CPG consultable sin tools nuevas.

T-CPG-ADOPTION (redux). La consulta al CPG por acción **no se declara** en el
schema de las tools de tarea: el nodo ya ejecuta su propia tarea de CPG
(`review_code`, `sync_code_index`, `gather_context`) y `brief` entrega el grafo
de los archivos del alcance. El canal de acción queda para el CLI/administración,
que sí pasa payload libre (`runner(project_id=..., **payload)`), así que el
handler tiene que despachar todas las acciones que anuncia.

Contrato:
1. Cada acción del dispatch responde (nada de "Unknown action" por una acción
   declarada y no implementada).
2. Una acción desconocida falla con la lista de válidas, no con un noop.
3. El perfil del agente no gana tools por esto.
"""

from __future__ import annotations

import asyncio
import os

from fast_mcp.memories.handlers.code_analysis import handle_inspect_codebase

#: Acciones que el handler anuncia (orden alfabético para el assert del error).
EXPECTED = {
    "business",
    "bugs",
    "conventions",
    "controlflow",
    "dataflow",
    "dead_code",
    "health",
    "metrics",
    "query",
    "review",
    "security",
}


class _FakeAnalysisService:
    """Cualquier método del servicio responde con su nombre (no toca el CPG)."""

    def __getattr__(self, name: str):
        def _call(**_kwargs):
            return {"ok": True, "method": name}

        return _call


class _Ctx:
    chain_dir = "/tmp/proyecto"
    project_root = "/tmp/proyecto"
    project_id = "walker"

    def code_analysis_service(self):
        return _FakeAnalysisService()


def test_cada_accion_del_dispatch_responde():
    for action in sorted(EXPECTED):
        out = handle_inspect_codebase(_Ctx(), action=action)

        assert "Unknown action" not in str(out.get("error") or ""), action
        assert out["result"]["ok"] is True, action


def test_una_accion_fuera_del_dispatch_falla_con_la_lista():
    out = handle_inspect_codebase(_Ctx(), action="imports")

    assert "Unknown action" in out["error"]
    assert out["actions"] == sorted(EXPECTED)


def test_el_perfil_del_agente_no_gana_tools():
    from fast_mcp import tool_profiles
    from fast_mcp.server import build_server

    os.environ["WALKER_MCP_PROFILE"] = "agent"
    try:
        tools = {t.name for t in asyncio.run(build_server().list_tools())}
    finally:
        os.environ.pop("WALKER_MCP_PROFILE", None)

    assert tools == set(tool_profiles.AGENT)
    assert "inspect_codebase" not in tools
