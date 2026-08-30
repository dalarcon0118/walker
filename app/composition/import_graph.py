"""composition/import_graph.py — Static import graph for composition DAG validation.

Reusable by:
- AppContainer._validate_dag_or_fail (ADR-052 D1)
- Hot-reload planner (reloader.py)

SSOT for AST-based dependency extraction between features.* modules.
"""
from __future__ import annotations

import ast
import sys
from typing import Any


_FEATURES_PREFIX = "features."


def _type_checking_ranges(tree: ast.AST) -> list[tuple[int, int]]:
    """Rangos [lineno, end_lineno] de bloques `if TYPE_CHECKING:`.

    Los imports bajo TYPE_CHECKING no existen en runtime (solo anotaciones),
    así que NO deben generar aristas en el grafo estático.
    """
    ranges: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        guarded = False
        if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
            guarded = True
        elif isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING":
            guarded = True
        if guarded and node.end_lineno:
            ranges.append((node.lineno, node.end_lineno))
    return ranges


def module_deps(
    mod_name: str, mod_path: str, prefix: str = _FEATURES_PREFIX
) -> set[str]:
    """Nombres features.* que importa el módulo (parse AST del source).

    Resuelve imports relativos a absolutos con la fórmula estándar de
    importlib (level sobre __package__). Solo devueltos los nombres que
    empiezan por el prefijo y no son el propio módulo. Los imports bajo
    `if TYPE_CHECKING:` se excluyen: no existen en runtime.

    Args:
        mod_name: fully-qualified module name (e.g. app.container)
        mod_path: absolute path to the .py file

    Returns:
        Set of dependency module names (features.* only).
    """
    try:
        with open(mod_path, "r", encoding="utf-8", errors="replace") as f:
            source = f.read()
        tree = ast.parse(source)
    except Exception:
        return set()
    tc_ranges = _type_checking_ranges(tree)

    package = getattr(sys.modules.get(mod_name), "__package__", "") or ""
    pkg_parts = package.split(".") if package else []
    deps: set[str] = set()

    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if any(lo <= node.lineno <= hi for lo, hi in tc_ranges):
            continue  # solo-tipos: sin arista estática
        candidates: list[str] = []
        if isinstance(node, ast.Import):
            candidates = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base_parts: list[str] = []
            if node.level > 0:
                base_parts = (
                    pkg_parts[: len(pkg_parts) - (node.level - 1)] if pkg_parts else []
                )
                if not base_parts:
                    continue
            if node.module:
                candidates.append(".".join(base_parts + node.module.split(".")))
            else:
                base = ".".join(base_parts)
                candidates.append(base)
                candidates.extend(f"{base}.{a.name}" for a in node.names)
        for name in candidates:
            if name.startswith(prefix) and name != mod_name:
                deps.add(name)
    return deps


def tarjan(graph: dict[str, set[str]]) -> list[list[str]]:
    """Tarjan's SCC algorithm.

    Args:
        graph: adjacency list {node: set_of_dependencies}

    Returns:
        List of SCCs, each SCC is a list of node names.
        SCCs with len > 1 are cycles.
    """
    index_counter = [0]
    stack: list[str] = []
    lowlink: dict[str, int] = {}
    index: dict[str, int] = {}
    on_stack: dict[str, bool] = {}
    result: list[list[str]] = []

    def strongconnect(v: str) -> None:
        index[v] = index_counter[0]
        lowlink[v] = index_counter[0]
        index_counter[0] += 1
        stack.append(v)
        on_stack[v] = True

        for w in graph.get(v, ()):
            if w not in index:
                strongconnect(w)
                lowlink[v] = min(lowlink[v], lowlink[w])
            elif on_stack.get(w, False):
                lowlink[v] = min(lowlink[v], index[w])

        if lowlink[v] == index[v]:
            scc: list[str] = []
            while True:
                w = stack.pop()
                on_stack[w] = False
                scc.append(w)
                if w == v:
                    break
            result.append(scc)

    for v in graph:
        if v not in index:
            strongconnect(v)

    return result
