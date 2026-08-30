"""features/main — Feature orquestador de Walker (delivery layer).

Este módulo conoce los features de dominio y los servicios DELIVERY
(cli_interface, mcp_adapter, task_execution, prompts, front_controller,
front_controller, api) que consumen el composition root:

- shared/core/engine: infraestructura de workflow
- delta_tracking: modelo de desarrollo
- code_analysis: análisis de código
- cognitive_knowledge: conocimiento
- event_tracing: trazabilidad
- project: gestión de proyectos

El composition root REAL vive en app/ (app.container, app.app, app.bootstrap,
app.composition) — features/main ya no es el hub de composición.

FIX ciclo de imports (hot-reload topo): este paquete era el hub que cerraba
el ciclo main → container → registry → handlers → main. Los re-exports son
ahora PEREZOSOS (PEP 562 __getattr__): la API pública
(`from features.main import AppContainer, WalkerApp, FrontController`)
se mantiene intacta, pero importar `features.main` ya NO arrastra el grafo
de composición en cascada. La definición real vive en app.container
/ app.app / protocols.rest.front_controller — ese es el SSOT.
"""

from typing import Any

# Mapa nombre-público → módulo de definición (SSOT). Sin imports eager:
# cada atributo se resuelve solo cuando alguien lo pide.
_EXPORTS: dict[str, str] = {
    "AppContainer": "app.container",
    "WalkerApp": "app.app",
    "FrontController": "protocols.rest.front_controller",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module 'features.main' has no attribute {name!r}")
    import importlib

    return getattr(importlib.import_module(target), name)


def __dir__() -> list[str]:
    return sorted(list(globals()) + list(_EXPORTS))
