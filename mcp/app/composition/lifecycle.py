"""app/composition/lifecycle.py — Ciclo de vida del container.

- RequestScope: scope efímero por request.
- _CONTAINER: singleton global del container.
- _inject_service_locator: inyecta el resolutor en core/engine.
"""
from __future__ import annotations

from typing import Any

# Evita import circular: AppContainer se usa en anotaciones de tipo.
try:
    from typing import TYPE_CHECKING
except ImportError:
    TYPE_CHECKING = False

if TYPE_CHECKING:
    from app.composition.container import AppContainer


class RequestScope:
    """Scope efímero de un request (ctx + chain_dir + task_id).

    Vida: UN request. Es ligero (referencias a instancias ya construidas) y
    no retiene registries mutables: el request toma TODAS sus referencias
    del container al entrar, y un swap de container entre requests nunca
    afecta a un scope en curso.
    """

    def __init__(self, container: "AppContainer", chain_dir: str = "", task_id: str = "", ctx: Any = None) -> None:
        self.container = container
        self.chain_dir = chain_dir
        self.task_id = task_id
        self.ctx = ctx


# Única fuente de la composición (absorbe _COMPOSITION_SINGLETON, ADR-046).
_CONTAINER: "AppContainer" | None = None


def _inject_service_locator(container: "AppContainer") -> None:
    """Inyecta el resolutor del container en el facade neutro de core/engine.

    core/engine sigue sin conocer features/: solo recibe el callable
    (module_registry.get_service) vía set_service_locator().
    """
    from shared.core.engine.bootstrap import set_service_locator

    set_service_locator(container.module_registry.get_service)
