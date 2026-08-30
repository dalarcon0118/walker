"""
tests/test_import_acyclicity.py — El composition root es un DAG (contrato).

FIX ciclo handlers↔container: features.main/__init__ re-exportaba EAGER
(container, front_controller, app) y registry inyectaba handlers con un
ImportFrom estático → el grafo cerraba main → container → registry →
handlers → main. El hot-reload topológico detectaba el SCC como ciclo y los
módulos del ciclo se recargaban "al final" con referencias potencialmente
mezcladas.

Este suite congela, sobre el grafo AST REAL de features.* cargado:

  1. Ningún módulo del composition core participa en un SCC (>1 nodo).
  2. La API pública perezosa de features.main funciona incluso cuando el
     paquete se importa SOLO primero (orden limpio, PEP 562).
  3. Los handlers no dependen estáticamente del paquete hub.
"""
import ast
import sys

# ── Tarjan iterativo mínimo (sin deps externas) ────────────────────────────


def _sccs(graph: dict[str, set[str]]) -> list[list[str]]:
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    result: list[list[str]] = []
    counter = [0]

    for root in sorted(graph):
        if root in index:
            continue
        work = [(root, iter(sorted(graph[root])))]
        index[root] = low[root] = counter[0]
        counter[0] += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            v, it = work[-1]
            advanced = False
            for w in it:
                if w not in graph:
                    continue
                if w not in index:
                    index[w] = low[w] = counter[0]
                    counter[0] += 1
                    stack.append(w)
                    on_stack.add(w)
                    work.append((w, iter(sorted(graph[w]))))
                    advanced = True
                    break
                if w in on_stack:
                    low[v] = min(low[v], index[w])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[v])
            if low[v] == index[v]:
                comp = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    comp.append(w)
                    if w == v:
                        break
                result.append(comp)
    return result


def _features_graph() -> dict[str, set[str]]:
    """Grafo estático features.*+app.*→features.*+app.* de los módulos cargados.

    REUSA reloader._module_deps (SSOT de aristas: resuelve relativos,
    excluye bloques TYPE_CHECKING) para que este contrato y el plan de
    recarga no puedan divergir.
    """
    from app.composition.import_graph import module_deps

    loaded = {
        n: getattr(m, "__file__", "")
        for n, m in sys.modules.items()
        if (n.startswith("features.") or n.startswith("app."))
        and getattr(m, "__file__", None)
    }
    graph: dict[str, set[str]] = {}
    for name, path in loaded.items():
        deps = module_deps(name, path.replace(".pyc", ".py"))
        graph[name] = {d for d in deps if d in loaded}
    return graph


COMPOSITION_CORE = {
    "app",
    "app.app",
    "app.container",
    "app.composition.container",
    "app.composition.lifecycle",
    "app.composition.registry",
    "features.workflow",
    "protocols.rest.front_controller",
    "protocols.cli.cli_interface.application.handlers",
}


def test_composition_core_sin_ciclos():
    from app.container import AppContainer

    AppContainer.build()  # cargar el grafo completo como en runtime

    graph = _features_graph()
    cyclic = {m for comp in _sccs(graph) if len(comp) > 1 for m in comp}
    overlap = cyclic & COMPOSITION_CORE
    assert not overlap, (
        f"el composition core volvió a tener ciclos: {sorted(overlap)}. "
        "Si agregaste un import hacia estos módulos, resuélvelo dinámico "
        "(import_module en factory DI) o apunta al módulo de definición SSOT."
    )


def test_api_publica_perezosa_en_import_aislado():
    """`import features.workflow` SOLO primero no debe romper ni arrastrar nada.

    (features/main —el hub eager— fue absorbido por features/workflow, ADR-068;
    el hub de composición vive en app/, no en features/.)
    """
    import subprocess

    code = (
        "import sys; sys.path.insert(0, '.')\n"
        "import features.workflow as wf\n"
        "assert 'app.container' not in sys.modules, 'hub eager regresó'\n"
        "assert callable(wf.PromptManager)\n"
        "print('lazy-ok')\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, f"stderr: {out.stderr[-500:]}"
    assert "lazy-ok" in out.stdout


def test_handlers_sin_dependencia_del_hub():
    """handlers no importa `from features.main import X` (hub)."""
    import inspect

    from protocols.cli.cli_interface.application import handlers

    source = inspect.getsource(handlers)
    assert "from features.main import" not in source, (
        "handlers volvió a depender del paquete hub; usa el módulo de "
        "definición (app.app / app.container)."
    )


def test_shared_libs_sin_imports_hacia_arriba():
    """shared/libs/ es la capa más baja junto al motor: NO importa
    features.*, app.* ni shared.core.engine.infrastructure.* (ADR-WALKER-065 D8/FS-3).

    REUSA module_deps (SSOT de aristas) para no divergir del plan de recarga.
    Hoy expone deuda: shared/libs/prompts importa features/app/engine.infrastructure.
    """
    from app.composition.import_graph import module_deps

    from app.container import AppContainer

    AppContainer.build()  # poblar sys.modules con el grafo completo

    loaded = {
        n: getattr(m, "__file__", "")
        for n, m in sys.modules.items()
        if n.startswith("shared.libs.") and getattr(m, "__file__", None)
    }
    forbidden = ("features.", "app.", "shared.core.engine.infrastructure")
    for name, path in loaded.items():
        for dep in module_deps(name, path.replace(".pyc", ".py")):
            assert not dep.startswith(forbidden), (
                f"{name} importa hacia arriba: {dep} "
                "(viola ADR-WALKER-065 D8: shared/libs no importa features/app/engine.infrastructure)"
            )
