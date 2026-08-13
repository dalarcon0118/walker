"""
application/mcp_config_service.py — Genera la configuración MCP del walker.

PROPÓSITO (auto-registro del MCP por el agente):
    Un usuario instala el walker en su computador, usa un agente de
    programación (opencode, Claude Code, Cursor, Cline, Codex, Roo... —
    cualquiera), y le dice al agente: "ejecuta el comando `walker mcp-config`".
    El agente ejecuta el comando, recibe esta configuración y con ella
    registra el MCP en su propia configuración. El usuario no toca archivos
    de configuración a mano.

    Por eso el walker NO conoce (ni necesita conocer) qué agente es: expone
    UN solo formato estándar portable (`MCP_FORMAT = "standard"`), con paths
    ABSOLUTOS al módulo MCP real, de modo que cualquier cliente MCP lo
    consuma igual sin importar el CWD ni el agente de turno.

FORMATO ÚNICO (registrado en `MCP_FORMAT`):
    {"mcpServers": {"walker": {command, args, env, cwd, ...}}}

Regla: el walker NO bifurca el formato según el agente (opencode/claude/
cursor). Expone UN solo bloque estándar portable; el agente que ejecute
`walker mcp-config` lo parsea y se auto-registra.
"""

from __future__ import annotations

import json
import os

# Formato único registrado: estándar mcpServers, agnóstico del agente.
# Es la respuesta que `walker mcp-config` devuelve al agente para que
# se auto-registre como cliente MCP (ver docstring del módulo).
MCP_FORMAT = "standard"


def _walker_root() -> str:
    """Raíz del repo del walker.

    Usa el path_resolver oficial (SSOT) en vez de calcular la ruta desde
    __file__ con parents hardcodeados.
    """
    from shared.core.path_resolver import get_walker_root

    return str(get_walker_root())


def _mcp_entry() -> list[str]:
    """Argumentos del servidor MCP (el ejecutable vive en `command`)."""
    return ["-m", "features.cli_interface.mcp"]


def _env() -> dict[str, str]:
    """Entorno necesario para que el MCP server arranque en cualquier contexto."""
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
    """Bloque mcpServers estándar (formato portable, agnóstico del agente)."""
    root = _walker_root()
    return {
        "mcpServers": {
            _server_id(): {
                "command": "python3",
                "args": _mcp_entry(),
                "env": _env(),
                "cwd": root,
                "description": "Walker workflow + CPG/CKG/delta knowledge tools",
                "type": "stdio",
            }
        }
    }


def generate() -> dict:
    """Config MCP del walker — formato único estándar (`MCP_FORMAT`).

    Auto-registro: el agente que ejecuta `walker mcp-config` recibe este
    bloque y se configura como cliente MCP con él. No acepta formatos por
    agente: el walker no conoce de qué agente proviene la petición, expone
    siempre el mismo bloque mcpServers portable.
    """
    return mcp_servers_block()


def cmd_mcp_config(args: list[str]) -> int:
    """CLI handler legacy: walker mcp-config [--raw] [--path].

    `--format=X` ya no existe: el walker expone un único formato estándar.
    """
    raw = False
    show_path = False
    for a in args:
        if a == "--raw":
            raw = True
        elif a == "--path":
            show_path = True
    if show_path:
        print(_walker_root())
        return 0
    config = generate()
    if raw:
        print(
            json.dumps(config.get("mcpServers", config), indent=2, ensure_ascii=False)
        )
        return 0
    print(json.dumps(config, indent=2, ensure_ascii=False))
    return 0
