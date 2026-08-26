"""Context Provider Registry — catálogo de fuentes de contexto.

Cada provider implementa ContextProvider (contrato en shared/common/context) y se
registra en el registry. El nodo declara qué providers necesita por nombre;
el engine los resuelve contra el registry en tiempo de composición.

El ABC ContextProvider vive en shared/common/context (DIP); aquí solo re-exportamos
para backward-compat.
"""

from __future__ import annotations

import logging

from shared.common.context import ContextPiece, ContextProvider

logger = logging.getLogger(__name__)


class ContextProviderRegistry:
    """Registro central de fuentes de contexto.

    Uso:
        registry = ContextProviderRegistry()
        registry.register("task", TaskProvider())
        provider = registry.get("task")
    """

    def __init__(self):
        self._providers: dict[str, ContextProvider] = {}

    def register(self, name: str, provider: ContextProvider) -> None:
        """Registra un provider bajo un nombre."""
        if name in self._providers:
            logger.warning("Provider '%s' already registered — overwriting", name)
        self._providers[name] = provider

    def get(self, name: str) -> ContextProvider | None:
        """Obtiene un provider por nombre. Retorna None si no existe."""
        return self._providers.get(name)

    def available(self) -> list[str]:
        """Lista de nombres de providers registrados."""
        return list(self._providers.keys())

    def __contains__(self, name: str) -> bool:
        return name in self._providers


__all__ = ["ContextProviderRegistry", "ContextProvider", "ContextPiece"]
