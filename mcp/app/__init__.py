"""app/ — Composition root de Walker (app shell, fase 5 de domain-first).

NO es un feature: es el lugar donde se cablea TODO el sistema
(shared + features) y desde donde arrancan los entry points (CLI, MCP).

Regla de dependencias (obligatoria, domain-first):
    shared → nada
    features → shared
    app → features + shared
    features NUNCA importan app/ (resolución por id+contrato via resolve_locator)

Definición real:
    app.bootstrap        → compose_app() (thin wrapper sobre AppContainer.build),
                            reset_composition() (thin wrapper sobre AppContainer.reset)
    app.app              → WalkerApp (API pública)
    app.container        → re-export público de AppContainer / register_all
    app.composition      → AppContainer, registry (register_all = único wiring),
                            lifecycle, import_graph

API pública perezosa (PEP 562, FIX ciclo hot-reload topo): importar `app`
NO arrastra el grafo de composición en cascada; cada atributo se resuelve
solo cuando alguien lo pide.
"""

from typing import Any

# Mapa nombre-público → módulo de definición (SSOT). Sin imports eager.
_EXPORTS: dict[str, str] = {
    "AppContainer": "app.container",
    "WalkerApp": "app.app",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module 'app' has no attribute {name!r}")
    import importlib

    return getattr(importlib.import_module(target), name)


def __dir__() -> list[str]:
    return sorted(list(globals()) + list(_EXPORTS))