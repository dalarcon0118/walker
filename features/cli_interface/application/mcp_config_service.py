"""
application/mcp_config_service.py — Genera la configuración MCP del walker.

PROPÓSITO: el usuario quiere usar las tools MCP del walker dentro de
CUALQUIER agente IA (Claude Code, Cursor, Cline, Codex, Roo...), no solo
opencode. Este servicio genera la config en formatos estándar con paths
ABSOLUTOS al módulo MCP real, de modo que funcione en cualquier agente
sin importar el CWD.

Formato estándar:
    {"mcpServers": {"walker": {command, args, env, cwd, ...}}}

El shim en to_clean_next/bootstrap/cli/mcp_config.py re-exporta estas
funciones para el CLI legacy (walker3).
"""

from __future__ import annotations
import json
import os
from pathlib import Path


def _walker_root() -> str:
    """Resuelve la raíz del repo del walker (donde vive features/)."""
    here = Path(
        __file__
    ).resolve()  # <repo>/features/cli_interface/application/mcp_config_service.py
    root = here.parents[3]  # application → cli_interface → features → repo
    if (root / "features" / "cli_interface" / "mcp.py").exists():
        return str(root)
    for parent in here.parents:
        if (parent / "features" / "cli_interface" / "mcp.py").exists():
            return str(parent)
    return str(root)


def _mcp_entry() -> list[str]:
    """Comando de arranque del servidor MCP (portable)."""
    return ["python3", "-m", "features.cli_interface.mcp"]


def _env() -> dict[str, str]:
    """Entorno necesario para que el MCP server arranque en cualquier agente."""
    root = _walker_root()
    env = {}
    current = os.environ.get("PYTHONPATH", "")
    parts = [p for p in current.split(":") if p] if current else []
    if root not in parts:
        parts.insert(0, root)
    env["PYTHONPATH"] = ":".join(parts)
    env["WALKER_DIR"] = os.environ.get("WALKER_DIR", root)
    for k in ("WALKER_SESSION_ID", "WALKER_AGENT_ID"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


def _server_id() -> str:
    return "walker"


def mcp_servers_block() -> dict:
    """Bloque mcpServers estándar (formato portable más común)."""
    root = _walker_root()
    return {
        "mcpServers": {
            _server_id(): {
                "command": "python3",
                "args": ["-m", "features.cli_interface.mcp"],
                "env": _env(),
                "cwd": root,
                "description": "Walker workflow + CPG/CKG/delta knowledge tools",
                "type": "stdio",
            }
        }
    }


def _opencode_format() -> dict:
    """Formato opencode (opencode.json → mcp.<name>.{type:local,command:[...]})."""
    return {
        "$schema": "https://opencode.ai/config.json",
        "mcp": {
            _server_id(): {
                "type": "local",
                "command": _mcp_entry(),
                "enabled": True,
                "environment": _env(),
            }
        },
    }


def _claude_format() -> dict:
    """Formato Claude Desktop (claude_desktop_config.json)."""
    return mcp_servers_block()


def _cursor_format() -> dict:
    """Formato Cursor (.cursor/mcp.json)."""
    return mcp_servers_block()


def generate(format: str = "standard") -> dict:
    """Genera la config en el formato pedido."""
    fmt = format.lower()
    if fmt == "opencode":
        return _opencode_format()
    if fmt == "claude":
        return _claude_format()
    if fmt == "cursor":
        return _cursor_format()
    return mcp_servers_block()


def cmd_mcp_config(args: list[str]) -> int:
    """CLI handler legacy: walker mcp-config [--format=X] [--raw] [--path]."""
    fmt = "standard"
    raw = False
    show_path = False
    for a in args:
        if a == "--raw":
            raw = True
        elif a == "--path":
            show_path = True
        elif a.startswith("--format="):
            fmt = a.split("=", 1)[1]
    if show_path:
        print(_walker_root())
        return 0
    config = generate(fmt)
    if raw:
        print(
            json.dumps(config.get("mcpServers", config), indent=2, ensure_ascii=False)
        )
        return 0
    print(json.dumps(config, indent=2, ensure_ascii=False))
    return 0
