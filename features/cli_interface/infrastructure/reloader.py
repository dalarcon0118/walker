"""
infrastructure/reloader.py — Hot-reload de módulos de features/.

Extraído de mcp_adapter.py (F4 — thin adapters, ADR-WALKER-016).

Escanea features/ por .py cambiados (mtime+size) y recarga los módulos
importados via importlib.reload. Best-effort: nunca rompe el loop.

El caller pasa on_changed() callback para invalidar su cache (e.g. _SERVER).
"""

from __future__ import annotations

import importlib
import os
import sys

_FEATURES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "features"
)
_reload_cache: dict[str, tuple[float, int]] = {}


def reload_features_if_changed(on_changed=None) -> bool:
    """Recarga módulos de features/ cuyo source cambió en disco.

    Returns True si algún módulo cambió (el caller puede invalidar cache),
    False si nada cambió.
    """
    if not os.path.isdir(_FEATURES_DIR):
        return False

    changed_files: list[str] = []
    for root, dirs, files in os.walk(_FEATURES_DIR):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in files:
            if not f.endswith(".py"):
                continue
            path = os.path.join(root, f)
            try:
                st = os.stat(path)
                sig = (st.st_mtime, st.st_size)
            except OSError:
                continue
            if _reload_cache.get(path) == sig:
                continue
            _reload_cache[path] = sig
            changed_files.append(path)

    if not changed_files:
        return False

    changed_real = {os.path.realpath(p) for p in changed_files}
    for mod_name, mod in list(sys.modules.items()):
        if not mod_name.startswith("features."):
            continue
        mod_file = getattr(mod, "__file__", None)
        if not mod_file:
            continue
        mod_path = mod_file.replace(".pyc", ".py")
        try:
            mod_path = os.path.realpath(mod_path)
        except OSError:
            continue
        if mod_path in changed_real:
            try:
                importlib.reload(mod)
            except Exception as e:
                print(
                    f"[MCP] reload '{mod_name}' failed (keeping current version): {e}",
                    file=sys.stderr,
                )

    if on_changed is not None:
        on_changed()
    return True
