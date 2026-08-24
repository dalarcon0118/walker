"""features/main — Composition root final de Walker.

Este módulo es el ÚNICO que conoce todos los módulos de la aplicación
y arma el grafo completo de dependencias:

- core/engine: infraestructura de workflow
- delta_tracking: modelo de desarrollo
- code_analysis: análisis de código
- cognitive_knowledge: conocimiento
- event_tracing: trazabilidad
- project: gestión de proyectos

Entry points (CLI, MCP, WorkflowAPI) consumen esta composición.

FIX ciclo de imports (hot-reload topo): este paquete era el hub que cerraba
el ciclo main → container → registry → handlers → main. Los re-exports son
ahora PEREZOSOS (PEP 562 __getattr__): la API pública
(`from features.main import AppContainer, WalkerApp, FrontController`)
se mantiene intacta, pero importar `features.main` ya NO arrastra el grafo
de composición en cascada. La definición real vive en features.main.container
/ app / front_controller — ese es el SSOT.
"""

from typing import Any

# Mapa nombre-público → módulo de definición (SSOT). Sin imports eager:
# cada atributo se resuelve solo cuando alguien lo pide.
_EXPORTS: dict[str, str] = {
    "AppContainer": "features.main.container",
    "WalkerApp": "features.main.app",
    "FrontController": "features.main.front_controller",
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
