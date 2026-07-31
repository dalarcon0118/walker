"""Context Provider Registry — catálogo de fuentes de contexto.

Cada provider implementa ContextProvider y se registra en el registry.
El nodo declara qué providers necesita por nombre; el engine los resuelve
contra el registry en tiempo de composición.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

from .models import ContextPiece

logger = logging.getLogger(__name__)


class ContextProvider(ABC):
    """Fuente de contexto configurable por nodo.

    Cada provider representa una CAPA de datos que el nodo puede declarar.
    No asume inteligencia en el workflow — el provider se PRESENTA al LLM
    como una capa con contenido y propósito.

    Dos salidas:
      - instructions(): texto que va al system prompt. Declara qué capa es,
        qué contiene, cómo usarla, con qué combinarse.
      - collect(): los datos concretos de la capa para el context layer.
    """

    @abstractmethod
    def instructions(self, config: dict, ctx: Any) -> str:
        """Declaración de capa para el LLM.

        No enseña queries — declara qué contiene esta capa, cómo usarla
        eficientemente, y con qué otras capas se combina mejor.

        Retorna string vacío si no hay instrucciones particulares.
        """
        return ""

    @abstractmethod
    def collect(self, config: dict, ctx: Any, query_manager: Any = None) -> ContextPiece | None:
        """Recupera los datos concretos de esta capa.

        Los providers que consultan capas de datos (CPG, CKG, Trace, State)
        reciben un QueryManager para hacer las consultas. Los providers
        que solo dan instrucciones pueden ignorarlo.

        Retorna None si esta capa solo existe para dar instrucciones
        y no tiene datos que entregar.
        """
        ...

    def schema(self) -> dict:
        """JSON Schema de la config que acepta este provider."""
        return {}


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
