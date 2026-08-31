"""test_fast_mcp_architecture_bdd.py — Test de ARQUITECTURA (ADR-071 §3bis).

Verifica las reglas de coherencia del paquete fast_mcp (la coherencia se
VALIDA, no se confía):

  R1. features/X NO importa features/Y (comunicación solo vía composition.py
      o shared_kernel).
  R2. La superficie (phases/) no importa features directamente — recibe
      objetos cableados por composition.py.
  R3. Autonomía: fast_mcp no importa el monorepo mcp/ (app/features/shared/
      protocols/config/data como raíces de módulo).
"""

from __future__ import annotations

import ast
import importlib
import inspect
import os


_FAST_MCP_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fast_mcp"
)

_MCP_ROOTS = ("app", "features", "shared", "protocols", "config", "data")
_ALLOWED_EXTERNAL = {
    "mcp.server.fastmcp",
    "mcp.server.fastmcp.utilities.func_metadata",
    "mcp.server.fastmcp.tools.base",
    "pydantic",
    "returns",
}


def _modules_of(subdir: str) -> list[str]:
    """Nombres de módulo de todos los .py bajo fast_mcp/<subdir>."""
    base = os.path.join(_FAST_MCP_ROOT, subdir)
    mods = []
    for dirpath, dirnames, files in os.walk(base):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for f in files:
            if not f.endswith(".py"):
                continue
            rel = os.path.relpath(os.path.join(dirpath, f), _FAST_MCP_ROOT)
            mod = "fast_mcp." + rel[:-3].replace(os.sep, ".")
            if mod.endswith(".__init__"):
                mod = mod[: -len(".__init__")]
            mods.append(mod)
    return sorted(mods)


def _imported_modules(source: str) -> set[str]:
    tree = ast.parse(source)
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
        elif isinstance(node, ast.Import):
            for n in node.names:
                mods.add(n.name)
    return mods


def _feature_of(module: str) -> str | None:
    """'fast_mcp.features.code_analysis.x' → 'code_analysis'; None si no es feature."""
    parts = module.split(".")
    if len(parts) >= 3 and parts[:2] == ["fast_mcp", "features"]:
        return parts[2]
    return None


def test_r1_features_no_se_importan_entre_si():
    """Regla 1 — un feature solo importa: sí mismo, shared_kernel, stdlib/lib."""
    violations = []
    for mod_name in _modules_of("features"):
        mod = importlib.import_module(mod_name)
        owner = _feature_of(mod_name)
        for imported in _imported_modules(inspect.getsource(mod)):
            target = _feature_of(imported)
            if target is not None and target != owner:
                violations.append(f"{mod_name} → {imported}")
    assert not violations, f"R1 violada (feature→feature): {violations}"


def test_r2_phases_no_importa_features_directo():
    """Regla 2 — phases/ no importa features/*: consume objetos de composition."""
    violations = []
    for mod_name in _modules_of("phases"):
        mod = importlib.import_module(mod_name)
        for imported in _imported_modules(inspect.getsource(mod)):
            if imported.startswith("fast_mcp.features"):
                violations.append(f"{mod_name} → {imported}")
    assert not violations, f"R2 violada (phases→features): {violations}"


def test_r3_cero_imports_del_monorepo_mcp():
    """Regla 3 — ningún módulo de fast_mcp importa app/features/shared/protocols."""
    violations = []
    for dirpath, dirnames, files in os.walk(_FAST_MCP_ROOT):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for f in files:
            if not f.endswith(".py"):
                continue
            path = os.path.join(dirpath, f)
            with open(path) as fh:
                for imported in _imported_modules(fh.read()):
                    if imported.split(".")[0] in _MCP_ROOTS:
                        violations.append(f"{path} → {imported}")
    assert not violations, f"R3 violada (import de mcp/): {violations}"
