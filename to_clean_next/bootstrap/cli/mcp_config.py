"""
cli/mcp_config.py — Shim: configuración MCP portable (legacy walker3).

La lógica real vive en features/cli_interface/application/mcp_config_service.py.
Este módulo re-exporta para el CLI legacy (walker3).
"""
from __future__ import annotations
import os
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
# bootstrap/cli → bootstrap → to_clean_next → repo root
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_THIS_DIR)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from features.cli_interface.application.mcp_config_service import (  # noqa: E402,F401
    _walker_root,
    _mcp_entry,
    _env,
    mcp_servers_block,
    generate,
    cmd_mcp_config,
)

__all__ = [
    "_walker_root", "_mcp_entry", "_env", "mcp_servers_block",
    "generate", "cmd_mcp_config",
]
