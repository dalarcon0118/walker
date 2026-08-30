"""tests/test_mcp_cwd_resolution_bdd.py — Regresión del bug multi-proyecto.

ANTES: mcp_servers_block() fijaba "cwd": root (raíz del walker). El servidor
MCP arrancaba con CWD = raíz del walker, así que la detección git-like
(find_cognitive_root / resolve_from_registry desde os.getcwd()) SIEMPRE
resolvía a .cognitive/projects/walker/ aunque el agente trabajara en OTRO
proyecto. Por eso "lista las mcp tool" en otro proyecto devolvía memoria
cognitiva del proyecto walker.

AHORA: el bloque MCP NO fija cwd (el cliente MCP lanza el server con su
propio directorio de trabajo = la carpeta del proyecto activo) y se honra
WALKER_PROJECT_ID como override explícito (ADR-WALKER-018).
"""
from __future__ import annotations

import os

from protocols.cli.cli_interface.application import mcp_config_service as mcp_cfg
from protocols.mcp import mcp_adapter as mcp_adapter


class TestMcpNoPinnedCwd:
    """El servidor MCP no debe fijar cwd en la raíz del walker."""

    def test_mcp_servers_block_no_cwd_root(self):
        cfg = mcp_cfg.mcp_servers_block()
        server = cfg["mcpServers"]["walker"]
        # No debe existir la clave cwd (el cliente usa su propio cwd).
        assert "cwd" not in server, f"cwd fijado rompe multi-proyecto: {server.get('cwd')}"
        # Sigue siendo portable y válido.
        assert server["command"] == "python3"
        assert server["args"] == ["-m", "protocols.cli.cli_interface.mcp"]

    def test_env_propagates_walker_project_id(self, monkeypatch):
        monkeypatch.setenv("WALKER_PROJECT_ID", "otro-proyecto")
        env = mcp_cfg._env()
        assert env.get("WALKER_PROJECT_ID") == "otro-proyecto"

    def test_resolve_project_id_honors_env(self, monkeypatch):
        monkeypatch.setenv("WALKER_PROJECT_ID", "otro-proyecto")
        # Sin args, usa el override de entorno aunque el CWD sea el walker.
        assert mcp_adapter._resolve_project_id({}) == "otro-proyecto"
        # El arg explícito sigue ganando al override de entorno.
        assert mcp_adapter._resolve_project_id({"project_id": "x"}) == "x"
        assert mcp_adapter._resolve_project_id({"_project_id": "y"}) == "y"
