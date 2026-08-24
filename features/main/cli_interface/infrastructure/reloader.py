"""
infrastructure/reloader.py — Hot-reload de módulos de features/.

Extraído de mcp_adapter.py (F4 — thin adapters, ADR-WALKER-016).

Escanea features/ por .py cambiados (mtime+size) y recarga los módulos
importados via importlib.reload. Best-effort: nunca rompe el loop.

El caller pasa on_changed() callback para invalidar su cache (e.g. _SERVER).
"""

from __future__ import annotations

import ast
import bisect
import importlib
import logging
import os
import sys
import time
from typing import Any

from returns.pipeline import is_successful
from returns.result import safe

logger = logging.getLogger(__name__)

_FEATURES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "features"
)
_reload_cache: dict[str, tuple[float, int]] = {}

# ── Coalescing del hot-reload (fix -32001 / cascadas repetidas) ────────────
# El reload sincrono por-request con muchas ediciones seguidas (sesiones de
# agentes editando features/) produce picos >30s que el cliente MCP mata con
# -32001. Cooldown entre cascadas + NO consumir la firma durante el defer:
# el cambio queda pendiente y se aplica en la proxima ventana.
_MIN_RELOAD_INTERVAL = float(os.environ.get("WALKER_MCP_RELOAD_COOLDOWN", "5"))
# Presupuesto blando: una cascada que exceda esto se loguea como warning
# (senal de modulo pesado o reload mal acotado); no se aborta a mitad porque
# un reload parcial deja clases viejas/nuevas mezcladas (bug isinstance visto
# en produccion como "'TaskResult' object has no attribute 'unwrap'").
_SLOW_RELOAD_BUDGET = float(os.environ.get("WALKER_MCP_RELOAD_BUDGET", "15"))

_last_reload_ts: float = 0.0
_reload_stats = {"cascades": 0, "deferred": 0}

# ── Baseline de arranque (fix -32001 en cold start) ────────────────────────
# El cache nace vacío: sin baseline, el PRIMER request post-boot ve los ~167
# módulos como "cambiados" y paga la cascada completa dentro de su ventana
# de respuesta. Los archivos con mtime anterior al arranque del proceso son
# estado conocido, no cambios.
_BOOT_TIME = time.time()


def _now() -> float:
    """Reloj monotonic inyectable (los tests lo monkeypatchean)."""
    return time.monotonic()


def _detect_changes() -> list[tuple[str, tuple[float, int]]]:
    """Detecta archivos .py de features/ cuya firma (mtime, size) cambió.

    NO muta _reload_cache: el consumo de firmas ocurre solo cuando la
    cascada realmente corre, para que un cambio detectado durante el
    cooldown no se pierda. En el primer pase post-boot siembra el baseline:
    los archivos previos al arranque se marcan vistos SIN recargar.
    """
    if not os.path.isdir(_FEATURES_DIR):
        return []
    changed: list[tuple[str, tuple[float, int]]] = []
    first_pass = not _reload_cache
    for root, dirs, files in os.walk(_FEATURES_DIR):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in files:
            if not f.endswith(".py"):
                continue
            path = os.path.join(root, f)
            stat_outcome = _stat_sig_core(path)
            if not is_successful(stat_outcome):
                continue  # archivo desapareció (race): se omite
            sig = stat_outcome.value_or((0, 0))
            if _reload_cache.get(path) == sig:
                continue
            if first_pass and sig[0] <= _BOOT_TIME:
                # Pre-existente al boot: baseline, nunca dispara cascada.
                _reload_cache[path] = sig
                continue
            changed.append((path, sig))
    return changed


def reload_features_if_changed(on_changed=None) -> bool:
    """Recarga módulos de features/ cuyo source cambió en disco.

    Returns True si algún módulo cambió (el caller puede invalidar cache),
    False si nada cambió O si la cascada quedó diferida por cooldown.
    """
    changed_files = _detect_changes()
    if not changed_files:
        return False

    global _last_reload_ts
    elapsed_since_last = _now() - _last_reload_ts
    if _last_reload_ts > 0 and elapsed_since_last < _MIN_RELOAD_INTERVAL:
        _reload_stats["deferred"] += 1
        logger.info(
            "hot-reload diferido (cooldown %.1fs<%.1fs): %d archivos pendientes "
            "se aplicarán en la próxima ventana",
            elapsed_since_last,
            _MIN_RELOAD_INTERVAL,
            len(changed_files),
        )
        return False

    started_at = _now()
    _last_reload_ts = started_at
    _reload_cache.update(changed_files)

    changed_real = {os.path.realpath(p) for p, _sig in changed_files}

    # ── Plan de recarga en orden topológico (fix hazard isinstance) ────────
    # ANTES: iteración cruda de sys.modules (orden de importación histórica).
    # Recargar un dependiente antes que su dependencia dejaba `from X import
    # Cls` bindeado a la clase VIEJA mientras X ya exponía la NUEVA →
    # isinstance fallaba entre identidades (bug visto como "'TaskResult'
    # object has no attribute 'unwrap'"). AHORA: grafo AST features.*→features.*,
    # cierre de importadores de los módulos cambiados, y Kahn determinista.
    plan = _plan_reload(changed_real)
    for mod_name, mod in plan:
        reload_outcome = _reload_module_core(mod)
        if not is_successful(reload_outcome):
            error = reload_outcome.failure()
            print(
                f"[MCP] reload '{mod_name}' failed (keeping current version): {error}",
                file=sys.stderr,
            )

    duration = _now() - started_at
    _reload_stats["cascades"] += 1
    if duration > _SLOW_RELOAD_BUDGET:
        logger.warning(
            "hot-reload lento (%.1fs > presupuesto %.1fs): %d módulos recargados",
            duration,
            _SLOW_RELOAD_BUDGET,
            len(changed_files),
        )

    if on_changed is not None:
        on_changed()
    return True


# ── Orden topológico de recarga (grafo AST, sin estado post-reload) ────────

_FEATURES_PREFIX = "features."


from features.main.composition.import_graph import _type_checking_ranges, module_deps


def _plan_reload(changed_realpaths: set[str]) -> list[tuple[str, Any]]:
    """Plan determinista [(mod_name, module)] para recargar.

    1. Mapea realpaths cambiados → módulos cargados (semilla).
    2. Construye grafo AST de TODOS los features.* cargados.
    3. Cierre transitivo de IMPORTADORES de la semilla (quienes hicieron
       `from semilla import X` deben rebindear aunque su archivo no cambió).
    4. Kahn sobre el subgrafo inducido (dependencias primero), tie-break
       alfabético; ciclos residuales se agregan ordenados (no cuelga).
    """
    loaded: dict[str, tuple[str, Any]] = {}  # mod_name -> (realpath, module)
    by_realpath: dict[str, str] = {}
    for mod_name, mod in list(sys.modules.items()):
        if not mod_name.startswith(_FEATURES_PREFIX):
            continue
        mod_file = getattr(mod, "__file__", None)
        if not mod_file:
            continue
        mod_path = mod_file.replace(".pyc", ".py")
        rp_outcome = _realpath_core(mod_path)
        if not is_successful(rp_outcome):
            continue
        rp = rp_outcome.value_or(mod_path)
        loaded[mod_name] = (rp, mod)
        by_realpath.setdefault(rp, mod_name)

    seeds = {by_realpath[rp] for rp in changed_realpaths if rp in by_realpath}
    if not seeds:
        return []

    graph: dict[str, set[str]] = {}
    importers: dict[str, set[str]] = {}
    for mod_name in loaded:
        deps = module_deps(mod_name, loaded[mod_name][0], prefix=_FEATURES_PREFIX)
        graph[mod_name] = {d for d in deps if d in loaded}
        for d in graph[mod_name]:
            importers.setdefault(d, set()).add(mod_name)

    # Cierre transitivo de importadores (BFS sobre aristas inversas).
    closure: set[str] = set(seeds)
    frontier = list(seeds)
    while frontier:
        current = frontier.pop()
        for imp in importers.get(current, ()):  # quien importa a `current`
            if imp not in closure:
                closure.add(imp)
                frontier.append(imp)

    # Kahn sobre subgrafo inducido por `closure`.
    indegree = {n: 0 for n in closure}
    within: dict[str, set[str]] = {n: graph[n] & closure for n in closure}
    for n, deps in within.items():
        indegree[n] = len(deps)
    ready = sorted(n for n, deg in indegree.items() if deg == 0)
    order: list[str] = []
    while ready:
        n = ready.pop(0)
        order.append(n)
        for imp in sorted(importers.get(n, ())):
            if imp in indegree:
                indegree[imp] -= 1
                if indegree[imp] == 0:
                    bisect.insort(ready, imp)

    cyclic = sorted(set(closure) - set(order))
    if cyclic:
        logger.warning(
            "hot-reload: ciclo entre módulos %s — se recargan al final "
            "(puede dejar referencias mezcladas en el ciclo)",
            cyclic[:5],
        )
        order.extend(cyclic)

    return [(name, loaded[name][1]) for name in order if name in loaded]


@safe
def _stat_sig_core(path: str) -> tuple[float, int]:
    """Core puro: firma (mtime, size) de un archivo (puede lanzar OSError)."""
    st = os.stat(path)
    return (st.st_mtime, st.st_size)


@safe
def _realpath_core(path: str) -> str:
    """Core puro: resuelve el realpath (puede lanzar OSError)."""
    return os.path.realpath(path)


@safe
def _reload_module_core(mod) -> None:
    """Core puro: recarga un módulo via importlib (puede lanzar)."""
    importlib.reload(mod)
